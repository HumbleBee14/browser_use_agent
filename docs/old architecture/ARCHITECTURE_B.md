# General Browser Agent — Architecture & Build Plan

> **Context**: Evidence collection agent for audit workflows. Replaces manual browser work (screenshots, CSV extraction, multi-hop navigation) with a parallel, LLM-driven agent. Built from scratch on Playwright — no browser-use in the agent loop. Task specs are swappable JSON; all agent code is site-agnostic.

---

## What We Are Building

A two-phase system:

1. **Discovery phase** — one browser session navigates a starting URL, paginates through it, and builds `samples.csv` (the work queue)
2. **Execution phase** — N parallel browser sessions each run the agent loop against one sample, producing structured evidence output

The agent loop is custom: observe (a11y tree) → decide (Claude Sonnet via tool use) → act (Playwright) → repeat. No browser-use. No hardcoded site logic. Everything site-specific lives in a task spec JSON file.

---

## Repository Structure

```
agent/
├── main.py                  # entry point — runs discovery then execution
├── discover.py              # phase 1: builds samples.csv from a start URL
├── worker.py                # phase 2: one worker = one BrowserContext + agent_loop
├── agent_loop.py            # the core loop: observe → decide → act → repeat
│
├── tools/
│   ├── browser.py           # Playwright wrappers (navigation + perception)
│   └── output.py            # file I/O (screenshots, CSV, metadata)
│
├── tasks/
│   ├── github_discovery.json    # task spec: discover org members
│   ├── github_profile.json      # task spec: extract per-profile data
│   ├── linear_tickets.json      # task spec: screenshot Linear tickets
│   └── _template.json           # blank spec to copy for new sites
│
├── evidence/                # output root — one folder per sample
│   └── {sample_id}/
│       ├── screenshot_00.png
│       ├── screenshot_01.png
│       └── metadata.json
│
├── samples.csv              # written by discover.py, read by main.py
├── combined.csv             # merged at end of run
├── .env                     # ANTHROPIC_API_KEY, site credentials
└── pyproject.toml
```

---

## Layer 1 — Orchestrator (`main.py`)

**Responsibility**: read input, run discovery, spawn workers, merge output.

**What it does**:
- Accepts a plain-text instruction: `"go to github.com/microsoft and collect all employee GitHub usernames"`
- Parses the instruction to determine: `start_url`, `task_type` (maps to a JSON spec)
- Calls `discover.py` → writes `samples.csv`
- Reads `samples.csv`, skips any sample where `evidence/{sample_id}/metadata.json` already has `status: done` (idempotency)
- Runs all workers via `asyncio.gather(*tasks, return_exceptions=True)`
- Caps concurrency with `asyncio.Semaphore(N)` — default N=5
- After all workers finish, merges all `metadata.json` files into `combined.csv`

**Key design decisions**:
- `return_exceptions=True` — one failed sample never kills the run
- Idempotency check on startup — safe to restart after crash
- Semaphore is tunable — reduce for slower sites, increase for fast ones

---

## Layer 2 — Discovery (`discover.py`)

**Responsibility**: turn a start URL into a flat list of samples.

**What it does**:
- Opens a single Playwright browser session (not parallel — sequential by design)
- Loads the discovery task spec (e.g. `github_discovery.json`)
- Runs the agent loop against the start URL with the goal of finding all sample URLs
- Handles pagination: URL-based (`?page=N`) or click-based ("next" button) — the LLM decides which based on what it sees
- Deduplicates collected URLs with a `seen` set
- Detects termination: empty page = stop
- Writes `samples.csv` with columns: `sample_id`, `url`, `discovered_at`

**Challenges handled here**:

| Challenge | Solution |
|---|---|
| Link noise (nav, footer, unrelated links) | LLM filters using goal + a11y context |
| Pagination termination | Stop when agent returns `done` with empty list |
| Deduplication | `seen` set in discover.py, checked before appending |
| Rate limiting | `asyncio.sleep(1)` between pages + detect 429 title |
| URL-based vs click-based pagination | LLM reasons from page state, not hardcoded |

**Output**: `samples.csv`
```
sample_id,url,discovered_at
torvalds,https://github.com/torvalds,2025-03-27T10:00:00
gvanrossum,https://github.com/gvanrossum,2025-03-27T10:00:01
```

---

## Layer 3 — Worker (`worker.py`)

**Responsibility**: isolate one sample's execution from all others.

**What it does**:
- Acquires the semaphore slot
- Creates an isolated `BrowserContext` (own cookies, session, storage — no bleed between workers)
- Opens a new page inside that context
- Calls `agent_loop(page, task_spec, output_dir)`
- Closes the context when done (success or failure)
- Releases the semaphore slot

**Why isolated context matters**: if worker A is logged in to a site, worker B doesn't inherit that session. Each worker is a clean slate. This prevents cross-contamination and makes the system stateless per sample.

---

## Layer 4 — Agent Loop (`agent_loop.py`)

**This is the core of the system.** Everything else is scaffolding around this.

### The loop

```
for step in range(max_steps):
    1. OBSERVE  — get pruned a11y tree from current page
    2. DECIDE   — send to Claude with task spec + history → get one typed action
    3. ACT      — execute the action via Playwright
    4. CHECK    — if action is "done" or "fail" → exit loop
```

### What Claude sees each turn (the prompt)

```
SYSTEM:
  {task_spec.system_prompt}        ← fixed, cached, site-specific goal

USER:
  ## Current page state
  {pruned_a11y_tree}               ← rebuilt fresh every turn

  ## Actions taken so far
  {last_5_actions}                 ← capped at 5, never grows unbounded

  ## Your goal
  {task_spec.goal}

  ## Output schema (populate when calling done)
  {task_spec.output_schema}

  Take the single best next action.
```

### Action schema (9 typed actions — no free-form prose ever)

```python
class AgentAction(BaseModel):
    action: Literal[
        "goto",        # navigate to a URL
        "click",       # click an element by selector
        "type",        # type text into an input
        "scroll",      # scroll up or down
        "screenshot",  # take a full-page screenshot and save it
        "extract",     # extract text from a selector into history
        "wait",        # wait for a selector to appear
        "done",        # task complete — write extracted data
        "fail"         # unrecoverable — write reason
    ]
    selector: str | None = None    # for click, type, extract, wait
    url: str | None = None         # for goto
    text: str | None = None        # for type
    direction: str | None = None   # for scroll: "up" | "down"
    extracted: dict | None = None  # for done — the structured output
    note: str | None = None        # for fail — reason string
```

Claude is forced to always return one of these via `tool_choice={"type": "any"}`. It can never return prose. If it can't decide, it returns `fail` with a note — never hangs.

### Key loop rules

- `wait_for_load_state("networkidle")` after every `goto`, `click`, `scroll` — before next observe
- History capped at last 5 actions — token cost stays flat regardless of run length
- System prompt is static per run — eligible for Anthropic prompt caching (saves ~80% of input tokens)
- Max steps guard — if `max_steps` reached without `done`/`fail`, auto-write `status: failed, reason: max_steps_exceeded`
- Screenshot fallback — if a11y tree returns fewer than 5 meaningful nodes (canvas-heavy page), send screenshot to vision model instead

---

## Layer 5 — A11y Pruner (inside `agent_loop.py`)

Transforms the raw Playwright accessibility snapshot (2000+ nodes) into a compact, task-relevant context (~20-40 nodes) that fits in the LLM prompt.

### Four-pass filter

**Pass 1 — prune dead nodes** (~2000 → ~600)
Drop nodes with no role, no name, hidden=true, or role in `{none, presentation, generic}` with no children.

**Pass 2 — keep semantic roles** (~600 → ~150)
Whitelist: `button, link, textbox, checkbox, radio, tab, menuitem, heading, table, row, cell, listitem, combobox, option, status, alert, img` (img only if has alt text).

**Pass 3 — task-aware keyword scoring** (~150 → ~40)
Boost nodes whose name/value contains keywords from `task_spec.keywords`. Keep all boosted nodes. Trim zero-score nodes to a budget of 20.

**Pass 4 — viewport bias** (~40 → ~20)
Prefer elements currently in the viewport (bounding box y < viewport height). Deprioritize below-fold content.

### Output format (sent to LLM — not JSON, token-efficient text)

```
[heading] "Overview / Repositories / Projects / Stars"
[tab] "Repositories" (selected=false)
[tab] "Overview" (selected=true)
[heading] "Pinned"
[link] "linux" → href=https://github.com/torvalds/linux
[text] "The Linux kernel"
[link] "subsurface" → href=https://github.com/torvalds/subsurface
[heading] "torvalds" 
[text] "Portland, OR"
```

---

## Layer 6 — Browser Tools (`tools/browser.py`)

Thin wrappers over Playwright. The agent loop calls these by name — it never touches Playwright directly.

### Navigation tools

| Tool | Playwright call | Notes |
|---|---|---|
| `goto(url)` | `page.goto(url)` | Always followed by `wait_for_load_state` |
| `click(selector)` | `page.click(selector)` | Always followed by `wait_for_load_state` |
| `type(selector, text)` | `page.fill(selector, text)` | Use `fill` not `type` — clears field first |
| `scroll(direction)` | `page.mouse.wheel(0, ±600)` | Converts "up"/"down" to pixel delta |
| `wait(selector)` | `page.wait_for_selector(selector)` | Timeout 10s default |

### Perception tools

| Tool | Implementation | Notes |
|---|---|---|
| `get_a11y_tree()` | `page.accessibility.snapshot()` → pruner | Core perception — always try first |
| `screenshot()` | `page.screenshot(full_page=True)` | Returns bytes, caller decides where to save |
| `extract_text(selector)` | `page.inner_text(selector)` | For targeted extraction after locating element |
| `get_all_links()` | `page.evaluate(JS)` | Returns `[{text, href}]` — used in discovery |

---

## Layer 7 — Output Tools (`tools/output.py`)

Zero Playwright. Pure file I/O. All paths relative to `evidence/{sample_id}/`.

| Tool | What it writes |
|---|---|
| `save_screenshot(name, bytes)` | `evidence/{sample_id}/{name}.png` |
| `write_metadata(obj)` | `evidence/{sample_id}/metadata.json` |
| `write_csv_row(data)` | appends to `combined.csv` with filelock |
| `flag_sample(reason)` | sets `flagged: true` + reason in metadata |
| `mark_done(extracted)` | sets `status: done` + extracted data in metadata |
| `mark_failed(reason)` | sets `status: failed` + reason in metadata |
| `emit_llm_note(text)` | appends to `notes: []` in metadata — LLM judgment trail |

`combined.csv` uses `filelock` — the one place multiple workers write to the same file.

---

## Task Spec Schema (`tasks/_template.json`)

Every site-specific configuration lives here. The agent code never changes.

```json
{
  "task_id": "unique_identifier",
  "phase": "discovery | execution",

  "start_url": "https://example.com/start",

  "system_prompt": "You are a browser agent. Your job is to [specific goal]. Extract only what you can see. Set missing fields to null. Never guess.",

  "goal": "Detailed natural language description of what to collect and when to stop.",

  "keywords": ["word1", "word2"],

  "output_schema": {
    "field_name": "string | null",
    "other_field": "number | null",
    "list_field": "array | null"
  },

  "max_steps": 25,

  "pagination": false,
  "stop_condition": "natural language description of done state"
}
```

### Real example — `tasks/github_discovery.json`

```json
{
  "task_id": "github_discovery",
  "phase": "discovery",
  "start_url": "https://github.com/orgs/microsoft/people",
  "system_prompt": "You are a browser agent collecting member profile URLs from a GitHub organization page. The page is paginated. Navigate page by page appending ?page=N to the URL. Stop when a page has no member links.",
  "goal": "Collect all member usernames and profile URLs. Each member appears as a link whose visible text matches their username. Ignore navigation links, org links, footer links.",
  "keywords": ["member", "people", "username", "profile", "avatar"],
  "output_schema": {
    "members": [{ "username": "string", "url": "string" }]
  },
  "max_steps": 300,
  "pagination": true,
  "stop_condition": "page returns zero member profile links"
}
```

### Real example — `tasks/github_profile.json`

```json
{
  "task_id": "github_profile",
  "phase": "execution",
  "start_url": "https://github.com/{username}",
  "system_prompt": "You are a browser agent extracting public profile data. Visit the page, take a full-page screenshot, extract all visible fields. If a field is not present, set it to null.",
  "goal": "Extract the public profile for this user: display name, bio, company, location, follower count, following count, pinned repository names. Take one full-page screenshot.",
  "keywords": ["bio", "followers", "following", "pinned", "company", "location", "repositories"],
  "output_schema": {
    "display_name": "string | null",
    "bio": "string | null",
    "company": "string | null",
    "location": "string | null",
    "followers": "number | null",
    "following": "number | null",
    "pinned_repos": "array | null"
  },
  "max_steps": 10,
  "pagination": false,
  "stop_condition": "all visible fields extracted and screenshot taken"
}
```

---

## Evidence Output Contract

Every sample produces the same structure regardless of task type.

```
evidence/
└── {sample_id}/
    ├── screenshot_00.png      # full page, step 0
    ├── screenshot_01.png      # follow-up screenshot if needed
    └── metadata.json
```

```json
{
  "sample_id": "torvalds",
  "url": "https://github.com/torvalds",
  "task_id": "github_profile",
  "status": "done",
  "steps": 4,
  "extracted": {
    "display_name": "Linus Torvalds",
    "bio": "Just a simple coder",
    "company": "Linux Foundation",
    "location": "Portland, OR",
    "followers": 231400,
    "following": 0,
    "pinned_repos": ["linux", "subsurface"]
  },
  "notes": [],
  "flagged": false,
  "started_at": "2025-03-27T10:01:00Z",
  "finished_at": "2025-03-27T10:01:18Z"
}
```

---

## Tech Stack

| Layer | Library | Why |
|---|---|---|
| Browser automation | `playwright` (async) | Direct control, no abstraction layer |
| LLM | `anthropic` SDK | Tool use / structured output |
| Model | `claude-sonnet-4-5` | Fast enough (<2s/turn), cheap at scale |
| Concurrency | `asyncio` + `Semaphore` | No external queue needed |
| Schema validation | `pydantic v2` | Action schema + task spec validation |
| File I/O | `aiofiles` | Non-blocking writes across workers |
| CSV merge lock | `filelock` | One lock for combined.csv |
| Input reading | `openpyxl` | Excel input if provided |
| Logging | `loguru` | Per-sample bound context |
| Progress | `rich` | Live terminal dashboard |
| Env / creds | `python-dotenv` | Never hardcode tokens |
| HTML cleanup | `beautifulsoup4` | `get_dom()` noise stripping |
| Downloads | `httpx` | `download_file()` with session cookies |

```toml
[tool.poetry.dependencies]
python = "^3.11"
playwright = "^1.40"
anthropic = "^0.25"
pydantic = "^2.0"
aiofiles = "^23.0"
filelock = "^3.13"
openpyxl = "^3.1"
pandas = "^2.0"
loguru = "^0.7"
rich = "^13.0"
python-dotenv = "^1.0"
beautifulsoup4 = "^4.12"
httpx = "^0.27"
```

---

## What We Reuse from browser-use

browser-use was the previous implementation. These parts are worth porting:

| What | Where it lives in new code | Notes |
|---|---|---|
| Playwright session setup | `worker.py` — BrowserContext init | Reuse the launch args, viewport, user-agent |
| A11y snapshot call | `tools/browser.py` — `get_a11y_tree()` | Same API: `page.accessibility.snapshot()` |
| Basic Playwright wrappers | `tools/browser.py` | `goto`, `click`, `type` are identical |
| Output folder structure | `tools/output.py` | Same `evidence/{id}/` pattern |

**What we do NOT reuse**:
- browser-use's agent loop — replaced entirely by `agent_loop.py`
- browser-use's prompt structure — replaced by task spec system prompts
- browser-use's action schema — replaced by our typed `AgentAction` Pydantic model
- browser-use's LLM integration — replaced by direct Anthropic SDK with `tool_choice=any`

---

## Build Order (one day)

| Phase | Time | Deliverable |
|---|---|---|
| 1 | 1.5h | `tools/browser.py` + `tools/output.py` — all tool wrappers working |
| 2 | 2h | `agent_loop.py` — loop + pruner + action schema + Claude integration |
| 3 | 1h | `discover.py` — discovery loop working on github.com/orgs/microsoft/people |
| 4 | 0.5h | `worker.py` + `main.py` — orchestration, semaphore, idempotency |
| 5 | 1h | `github_profile.json` task spec + end-to-end test on 5 profiles |
| 6 | 1h | `linear_tickets.json` task spec + end-to-end test |
| 7 | remainder | scale test to 50 samples, fix flakiness, write README |

Start with Phase 1 — having working tools lets you test each piece in isolation before wiring the loop.

---

## The One Sentence Pitch

> A site-agnostic browser agent that discovers sample lists by navigating and paginating any web UI, then collects structured evidence from each sample in parallel — all driven by a custom observe-decide-act loop with Claude, zero hardcoded site logic, and a swappable JSON task spec per target system.
