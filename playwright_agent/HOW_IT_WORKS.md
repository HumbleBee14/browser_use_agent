# How It Works — Technical Overview

## System Flow

```
User Input                          Output
─────────                          ──────
"--prompt 'Go to X and extract Y'"  evidence/run_XXXX/
  or                                  ├── sample_001/
"--task spec.json --input data.csv"   │   ├── 01_page.png    (SHA-256 hashed)
                                      │   ├── result.json    (extracted fields)
         │                            │   └── action_log.json (step trace)
         ▼                            ├── sample_002/...
┌─────────────────┐                   └── combined.csv
│  TASK PLANNER   │ (--prompt only)
│  Claude converts│
│  plain English  │
│  → task spec    │
│  → sample URLs  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  ORCHESTRATOR   │  main.py
│  Load samples   │
│  Skip completed │  ← idempotent restart
│  Launch workers │
└────────┬────────┘
         │
         ├──────────────┬──────────────┐
         ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   WORKER 1   │ │   WORKER 2   │ │   WORKER N   │
│ BrowserCtx   │ │ BrowserCtx   │ │ BrowserCtx   │  ← isolated sessions
│   (sample A) │ │   (sample B) │ │   (sample N) │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       ▼                ▼                ▼
┌──────────────────────────────────────────────────┐
│              AGENT LOOP (per sample)              │
│                                                   │
│  for step in range(max_steps):                    │
│                                                   │
│    ┌─────────┐   ┌─────────┐   ┌─────────┐      │
│    │ OBSERVE │──▶│ DECIDE  │──▶│  ACT    │      │
│    │         │   │         │   │         │      │
│    │ DOM     │   │ Claude  │   │Playwright│      │
│    │ a11y    │   │ tool_use│   │ goto     │      │
│    │ tree    │   │ returns │   │ click    │      │
│    │ pruned  │   │ 1 of 9  │   │ type     │      │
│    │ to ~80  │   │ actions │   │ screenshot│     │
│    │ nodes   │   │         │   │ done/fail│      │
│    └─────────┘   └─────────┘   └─────────┘      │
│         ▲                           │             │
│         └───────────────────────────┘             │
│              loop until done/fail                 │
└──────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│  MERGE CSV      │  main.py reads all result.json
│  Sort by ID     │  → combined.csv
└─────────────────┘
```

## Component-by-Component

### 1. Task Planner (`task_planner.py`)

Only used with `--prompt`. Calls Claude once to convert natural language into:

```
Input:  "Go to torvalds GitHub and extract name, followers, pinned repos"
Output: {
  task_spec: { system_prompt, goal, output_schema, keywords, max_steps... },
  samples:   [{ sample_id: "torvalds", url: "https://github.com/torvalds" }]
}
```

The generated spec is saved to `evidence/run_XXXX/generated_task_spec.json` for inspection. After this, execution is identical to using a pre-built `--task` JSON file.

### 2. Orchestrator (`main.py`)

Coordinates the run:
- Loads task spec (from file or planner)
- Loads samples (from CSV, `--url`, or planner)
- Checks `evidence/run_XXXX/` for already-completed samples → skips them (`--resume`)
- Launches N workers via `asyncio.gather` + `Semaphore(N)` for bounded concurrency
- After all workers finish: merges all `result.json` → `combined.csv`

### 3. Worker (`worker.py`)

One worker per sample. Creates an **isolated BrowserContext** — own cookies, own session, no bleed between workers. If `auth_profile` is set (e.g., LinkedIn), loads saved cookies into the context.

```python
ctx = await browser.new_context(color_scheme="light", storage_state=auth_file)
page = await ctx.new_page()
await agent_loop.run(page, sample, task_spec, output_mgr)
```

All exceptions caught → written to `result.json`. Worker never crashes the batch.

### 4. Agent Loop (`agent_loop.py`) — The Brain

A ReAct cycle that repeats until `done`, `fail`, or `max_steps`:

**OBSERVE** — DOM extractor reads the page's accessibility tree via Playwright's `aria_snapshot()`. Raw tree (~2000 nodes) is pruned through 4 passes:

```
Pass 1: Skip navigation/banner/footer blocks (entire subtrees removed)
Pass 2: Keep semantic roles only (link, button, heading, textbox, checkbox...)
Pass 3: Boost nodes matching task keywords, keep links/buttons always
Pass 4: Trim to 120 nodes max
```

Result: compact indexed text like:
```
[0] [heading]  "Linus Torvalds"
[1] [link]     "linux" → https://github.com/torvalds/linux
[2] [button]   "Follow"
[3] [textbox]  "Search" (value="hello")   ← current input values enriched
```

If `dom_confidence < 0.6` (canvas/SVG-heavy pages), vision activates — takes a screenshot and asks Claude a targeted question.

**DECIDE** — Sends to Claude via Anthropic SDK:
- `system`: task spec's system_prompt (static, prompt-cached across steps)
- `messages`: one user message with page state + last 5 actions + goal + output schema
- `tools`: 9 action definitions
- `tool_choice: {"type": "any"}` — forces structured output, never prose

Claude returns exactly one tool call. Always.

**ACT** — Dispatches the action to Playwright:

| Action | Playwright Call | Element Resolution |
|--------|----------------|--------------------|
| `goto(url)` | `page.goto()` | Direct URL |
| `click(selector)` | 3-strategy: index → text → CSS | `page.get_by_role()` / `page.get_by_text()` |
| `type(selector, text)` | `page.fill()` | Same 3-strategy |
| `scroll(direction)` | `page.mouse.wheel()` | N/A |
| `screenshot(label)` | `page.screenshot()` | N/A, saves with SHA-256 |
| `extract(selector)` | `locator.inner_text()` | Same 3-strategy |
| `wait(selector)` | `wait_for_selector()` | Text or CSS |
| `done(extracted)` | Validates + writes result | N/A |
| `fail(note)` | Writes failure + exits | N/A |

Every action returns `ActionResult(success, description, error)` — never raises.

**CHECK** — When agent calls `done`:
1. Verify `required_fields` are present and not None (but 0/false are valid)
2. Verify `required_artifacts` labels match saved screenshot filenames
3. If missing + steps remain → bounce back with notice
4. If missing + last step → write `needs_review`
5. If all good → write `result.json` + `action_log.json`

**SELF-CORRECTION:**
- **Loop detection**: same `(url, action)` 3+ times → nudge message
- **Spam detection**: same action type 3+ consecutive (screenshot, goto, scroll) → forced stop. Excludes `type`/`click` since form filling is legitimately repetitive.
- **Failure recovery**: 3+ consecutive failures → inject list of visible interactive elements

### 5. DOM Extractor (`core/dom_extractor.py`)

Primary perception. Converts browser page into LLM-digestible text.

```
aria_snapshot() → parse YAML → filter semantic → keyword boost → trim → enrich input values
```

**Input value enrichment**: Reads current values from live `<input>` elements via JavaScript and attaches them to DOM nodes. This prevents the agent from re-filling already-filled form fields.

**CDP fallback**: If `aria_snapshot()` returns < 5 nodes (broken a11y tree), falls back to Chrome DevTools Protocol `Accessibility.getFullAXTree`.

**DOM confidence**: Computed from canvas/SVG/missing-ARIA ratios. Below 0.6 triggers vision.

### 6. Vision Module (`core/vision.py`)

Activated when DOM is insufficient. Sends screenshot to Claude with a **targeted question** (never "describe this page"):

```
"What is the status icon next to 'build / test'? Pass, fail, or pending?"
```

Uses `AsyncAnthropic` with shared module-level client for connection pooling.

### 7. Output (`tools/output.py`)

Deterministic evidence packaging per sample:

- Screenshots: `{counter:02d}_{label}.png` — sequential, never renamed
- SHA-256 hash computed at write time, stored in `result.json`
- Atomic writes: `.tmp` → `Path.replace()` — no partial files on crash
- Download filenames sanitized (path traversal prevention)
- `combined.csv`: single merge at batch end, sorted by sample_id, non-scalar values JSON-serialized

### 8. Rate Limiting (`tools/browser.py`)

Per-domain throttling via `asyncio.Lock`:

```python
RATE_LIMITS = {
    "linkedin.com": 3.0s,
    "github.com":   0.5s,
    "default":      0.2s,
}
```

Concurrency-safe — all workers share one event loop, one lock.

## What Makes It System-Agnostic

Zero site-specific code in any Python file. The agent reads the live DOM and reasons about it. All site knowledge lives in:

- `tasks/*.json` — goal, keywords, output schema, system prompt
- `.env` — credentials

To add a new site: write one JSON file. No code changes.
