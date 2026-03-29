# Browser Workflow Agent — Architecture

**Stack:** Python 3.11+ · Playwright (async) · Anthropic SDK · Pydantic v2

**Constraint:** No browser-use library. No LangChain. Custom agent loop only.

---

## Design in One Sentence

A site-agnostic browser agent that discovers sample lists by navigating any web UI, then executes structured browser tasks and collects reviewable evidence from each sample in parallel — driven by a custom ReAct loop with Claude, zero hardcoded site logic, and a swappable JSON task spec per target system.

---

## Table of Contents

1. [Project Requirements](#1-project-requirements)
2. [Task Analysis](#2-task-analysis)
3. [Repository Structure](#3-repository-structure)
4. [System Overview](#4-system-overview)
5. [Layer 1 — Orchestrator](#5-layer-1--orchestrator-mainpy)
6. [Layer 2 — Discovery](#6-layer-2--discovery-discoverpy)
7. [Layer 3 — Worker](#7-layer-3--worker-workerpy)
8. [Layer 4 — Agent Loop](#8-layer-4--agent-loop-agent_looppy)
9. [Layer 5 — DOM Extractor](#9-layer-5--dom-extractor)
10. [Layer 6 — Vision Module](#10-layer-6--vision-module)
11. [Layer 7 — Action System](#11-layer-7--action-system)
12. [Layer 8 — Output & Evidence](#12-layer-8--output--evidence)
13. [Task Spec Schema](#13-task-spec-schema)
14. [Tech Stack](#14-tech-stack)
15. [Build Order](#15-build-order)

---

## 1. Project Requirements

This is a **Browser Workflow Agent** for structured browser workflows. It started from audit/compliance evidence collection, where analysts manually navigate systems like Workday, GitHub, Jira, and LinkedIn, take screenshots, extract data into spreadsheets, and download artifacts. It has since expanded into a broader DOM-first browser agent for extraction, navigation, enrichment, form workflows, judgments, and long-horizon evidence collection.

The core design target is now:
- **General-purpose across accessible DOM-first web tasks**
- **Evidence-oriented by default** — screenshots, structured outputs, traces, checkpoints
- **Task-configurable** — discovery, extraction, judgments, and form workflows via JSON task specs

### Requirements

- **System-agnostic within scope** — works across a broad class of accessible DOM-first websites via browser UI, no site-specific Python code
- **Two modes** — Manual UI (default): navigate, extract, screenshot, download. Integration-assisted (optional): use exports/APIs if available
- **Evidence-grade outputs** — screenshots, CSVs, judgments, per-sample folders
- **Scalable** — 50 to 1,000+ samples per batch
- **Natural language input** — "Go to Microsoft's GitHub and get all users' GitHub usernames"
- **Broad workflow coverage** — supports audit tasks, enrichment, form fill, graph traversal, and long-horizon collection

### Priorities (in order)

1. Accuracy
2. Generability
3. Scalability to thousands of samples
4. Consistency between samples
5. Speed

### What They're Evaluating

1. **Custom agent loop** — real ReAct design, not an LLM-in-a-loop
2. **Sub-agent coordination** — brief explicitly says "many subagents"
3. **Evidence quality** — deterministic, hashed, audit-reviewable
4. **Scalability** — 1,000 samples without degradation
5. **System-agnostic** — reasons over live DOM, not hardcoded selectors

---

## 2. Task Analysis

### Task 1: Linear Tickets — Screenshots + CSV

| Aspect | Detail |
|--------|--------|
| **Input** | Excel/CSV list of Linear ticket URLs |
| **Navigation** | Single-page per sample — visit URL, extract, screenshot |
| **Extract** | ticket_number, assignee, due_date |
| **Output** | CSV + one folder per ticket with screenshot |
| **Challenge** | React SPA — must wait for hydration. Distinguish "unassigned" from "failed to find" |

### Task 2: GitHub Deep Audit — Commit → PR → CI → Jira

| Aspect | Detail |
|--------|--------|
| **Input** | 60 commit SHAs or "last 60 commits on main" |
| **Navigation** | Graph traversal: commit page → PR → checks → CI failure → Jira ticket |
| **Extract** | commit SHA, PR creator, approver, merger, check pass/fail, CI failure details, Jira URL |
| **Output** | CSV + per-commit folder with 3-5 screenshots |
| **Challenge** | SVG status icons (need vision). Cross-domain (GitHub → Jira). Some commits have no PR |

### Task 3: LinkedIn Enrichment — CSV Combination

| Aspect | Detail |
|--------|--------|
| **Input** | CSV of names (no URLs — agent must search) |
| **Navigation** | Search → disambiguation → profile extraction |
| **Extract** | linkedin_url, school, current_company, tenure |
| **Output** | Original CSV with 4 columns appended |
| **Challenge** | Auth wall. Rate limiting. Ambiguous names need disambiguation |

### Task 4: Code Blame — Materiality Judgment

| Aspect | Detail |
|--------|--------|
| **Input** | Code string (function signature or variable name) |
| **Navigation** | Find file → Blame View → recent commit → assess materiality |
| **Extract** | file_path, last_modified_date, author, material_change (yes/no/inconclusive) |
| **Output** | CSV + screenshots + judgment |
| **Challenge** | Requires genuine reasoning about code changes, not just extraction |

### Task 5: Form Fill + Download — Workday-like

| Aspect | Detail |
|--------|--------|
| **Input** | URL + field values to fill |
| **Navigation** | Fill form → screenshot → submit → download report → iterate tabs |
| **Extract** | form_submitted, report downloaded, attachment count |
| **Output** | Screenshots of empty/filled/result + downloaded files |
| **Challenge** | Custom widgets (date pickers, cascading dropdowns). Download detection |

---

## 3. Repository Structure

```
playwright_agent/
├── main.py                  # entry point: discovery → execution → merge CSV
├── discover.py              # phase 1: navigate start URL, paginate, write samples.csv
├── worker.py                # phase 2: one BrowserContext per sample + agent_loop
├── agent_loop.py            # THE core: observe → decide → act → repeat (orchestrates helpers below)
├── agent_prompt.py          # Token-budget history + user message construction
├── agent_recovery.py        # Smart termination, stagnation escalation, final consolidation
├── agent_merge.py           # deep_merge for checkpoints (id-aware list merge)
├── agent_llm_retry.py       # Retryable LLM error classification
├── agent_navigation.py      # Pagination detection, batch DOM safety
├── agent_dispatch.py        # Playwright execution for one action
├── memory.py                # Long-term memory: patterns + failures, LLM-distilled
├── config.py                # Environment config (.env settings)
│
├── core/
│   ├── dom_extractor.py     # a11y pruner, dom_confidence, serializer
│   └── vision.py            # screenshot capture, Claude vision, hybrid dispatch
│
├── tools/
│   ├── browser.py           # thin Playwright wrappers (goto, click, type, scroll)
│   └── output.py            # save_screenshot (SHA-256), write_metadata, write_csv
│
├── models/
│   ├── task.py              # TaskSpec (Pydantic), loaded from tasks/*.json
│   └── actions.py           # AgentAction schema, ActionResult
│
├── tasks/
│   ├── github_discovery.json
│   ├── github_profile.json
│   ├── github_commit_audit.json
│   ├── linear_tickets.json
│   └── _template.json
│
├── evidence/                # output root — one folder per run
│   └── run_YYYY-MM-DD_HHMMSS/
│       ├── memory/
│       │   ├── patterns.json      # learned only within this run
│       │   └── failures.json      # failure warnings only within this run
│       ├── samples.csv            # optional manual discovery output
│       ├── discovered_samples.csv # planner-driven discovery output
│       ├── combined.csv
│       └── {sample_id}/
│           ├── 01_{label}.png
│           ├── result.json        # extracted fields + artifact manifest
│           └── action_log.json    # every step: thinking, action, outcome
│
├── .env                     # ANTHROPIC_API_KEY, credentials
└── requirements.txt
```

**A small set of focused Python modules.** Everything site-specific lives in `tasks/*.json`.

---

## 4. System Overview

### Two-Phase Execution

```
Phase 1: DISCOVERY (sequential, one browser)
  "Go to github.com/orgs/microsoft/people"
      → agent navigates, paginates, collects member URLs
      → writes a run-local samples file (the work queue)

Phase 2: EXECUTION (parallel, N browsers)
  For each row in the run-local samples file:
      → worker launches isolated BrowserContext
      → agent_loop runs the task spec against that sample
      → writes evidence/{sample_id}/result.json + screenshots
  After all workers:
      → merge all result.json → run-local combined.csv
```

### Data Flow

```
Input (prompt or CSV)
    │
    ▼
┌─────────┐     ┌───────────┐     ┌──────────┐
│  main.py │────▶│ discover  │────▶│ samples  │
│          │     │   .py     │     │  .csv    │
└─────────┘     └───────────┘     └──────────┘
    │                                   │
    ▼                                   ▼
┌─────────┐     ┌───────────┐     ┌──────────────────┐
│  main.py │────▶│  worker   │────▶│ evidence/        │
│ (gather) │     │   .py     │     │  {sample_id}/    │
│          │     │ × N parallel    │   01_page.png    │
└─────────┘     └───────────┘     │   result.json    │
    │                              │   action_log.json│
    ▼                              └──────────────────┘
┌──────────┐
│ combined │
│  .csv    │
└──────────┘
```

### Component Dependencies

```
main.py
  ├── discover.py ──── agent_loop.py
  ├── worker.py ────── agent_loop.py
  │                       ├── core/dom_extractor.py
  │                       ├── core/vision.py
  │                       ├── tools/browser.py
  │                       ├── tools/output.py
  │                       └── memory.py
  └── (merge CSV)
```

---

## 5. Layer 1 — Orchestrator (`main.py`)

Coordinates discovery → execution → output merge.

```
1. Parse input → load matching task spec JSON
2. If discovery task exists → run discover.py → writes samples.csv
3. If samples.csv provided as input → skip discovery
4. Read samples.csv
5. Skip samples where evidence/{sample_id}/result.json has status:"done"  ← idempotency
6. asyncio.gather(*[worker(s) for s in pending], return_exceptions=True)
7. Merge all result.json → combined.csv
```

Key decisions:
- **`return_exceptions=True`** — one bad sample never kills the run
- **Idempotency** — restart after crash picks up where it stopped
- **`asyncio.Semaphore(N)`** — default N=5, tunable per task

---

## 6. Layer 2 — Discovery (`discover.py`)

One sequential browser session. Builds the work queue.

Runs `agent_loop` with the discovery task spec. The loop paginates until the agent calls `done` with an empty list.

| Challenge | Solution |
|-----------|----------|
| Link noise (nav/footer/org links) | LLM filters using goal + a11y context |
| Pagination termination | Agent calls `done` when page returns zero target links |
| Deduplication | `seen: set` checked before appending |
| Rate limiting | `asyncio.sleep(1)` between pages + 429 detection |
| URL vs click pagination | LLM reasons from live page state — no hardcoding |

Output — `samples.csv` (columns vary by task):
```
# URL-based tasks (tickets, commits, profiles):
sample_id,url,discovered_at
torvalds,https://github.com/torvalds,2026-03-27T10:00:00Z

# Name-based tasks (LinkedIn enrichment):
sample_id,name,company,url
person_001,Satya Nadella,Microsoft,

# Code-based tasks (blame review):
sample_id,code_string,repo_url,url
blame_001,"class ExchangeRate",https://github.com/org/repo,
```

The `url` column is optional. Tasks like LinkedIn enrichment start with a name and the agent discovers the URL. The task spec's `input_schema` defines which columns are expected:

```json
{
  "input_schema": {
    "name": "string",
    "company": "string | null"
  }
}
```

---

## 7. Layer 3 — Worker (`worker.py`)

Owns one sample's full lifecycle. Isolated context per sample.

```python
async def run_sample(browser, sem, sample, task_spec, output_dir):
    async with sem:
        ctx = await browser.new_context()       # isolated: own cookies, session
        page = await ctx.new_page()
        await page.emulate_media(color_scheme="light")  # consistent white screenshots
        try:
            await agent_loop(page, sample, task_spec, output_dir)
        except Exception as e:
            write_metadata(output_dir, {"status": "failed", "reason": str(e)})
        finally:
            await ctx.close()
```

- Each worker gets an isolated `BrowserContext` — no session bleed
- If `task_spec.auth_profile` is set, load `storage_state` JSON into the context (saved cookies from a prior manual login — handles LinkedIn, Workday, etc.)
- Exceptions are caught and written to `result.json`
- Semaphore slot always released in `finally`

---

## 8. Layer 4 — Agent Loop (`agent_loop.py`)

**This is the entire agent.** Everything else is scaffolding.

### The ReAct Cycle

```
for step in range(task_spec.max_steps):         ← default 25, configurable

    1. OBSERVE
       page_state = dom_extractor.snapshot(page, task_spec.keywords)
       if dom_confidence(page_state) < 0.6:
           page_state += vision.analyze(page, targeted_question)

    2. DECIDE
       response = anthropic.messages.create(
           system   = task_spec.system_prompt       ← static, prompt-cached
           messages = build_prompt(page_state, fitted_history, task_spec)  # 5-25 items, budget-fitted
           tools    = action_tool_schema(include_reflection=config.REFLECTION_MODE == "full")
           tool_choice = {"type": "any"}            ← forces structured output
       )
       action = AgentAction(**response.tool_input)
       # Reflection fields optional in schema when REFLECTION_MODE=light
       history.append(action)

    3. ACT
       result = dispatch(action, page, output_manager)
       # result is always ActionResult — never raises

    4. CHECK TERMINATION
       if action.action == "done":
           # Machine-checkable completion — not just prompt-driven
           missing = [f for f in task_spec.required_fields if f not in action.extracted]
           if missing:
               inject "You called done but these required fields are missing: {missing}"
               continue  # force agent to try again
           write_result(action.extracted); return
       if action.action == "fail":  write_result(status="failed");   return

    5. LOOP DETECTION
       if same (url, action) seen 3+ times:
           inject recovery nudge into next observation

# Reached max_steps without done/fail:
write_result(status="failed", reason="max_steps_exceeded")
```

### What Claude Sees Each Turn

```
SYSTEM (static — prompt cached across all steps):
  {task_spec.system_prompt}

USER (rebuilt every turn):
  ## Current page state
  URL: https://github.com/torvalds
  Title: Linus Torvalds (torvalds)

  [0] [heading]  "Linus Torvalds"
  [1] [text]     "Portland, OR"
  [2] [link]     "linux" → https://github.com/torvalds/linux
  [3] [link]     "subsurface" → https://github.com/torvalds/subsurface
  [4] [button]   "Follow"
  [5] [text]     "231k followers · 0 following"

  ## Actions taken so far (recent budget-fitted window)
  Step 1: goto https://github.com/torvalds → success
  Step 2: screenshot "profile" → saved 01_profile.png

  ## Goal
  {task_spec.goal}

  ## Output schema (populate when calling done)
  {task_spec.output_schema}

  Take the single best next action.
```

### Action Schema — 12 Typed Actions

```python
class AgentAction(BaseModel):
    action: Literal[
        "goto",        # navigate to a URL
        "click",       # click an element by index or text
        "type",        # fill an input field
        "scroll",      # scroll up or down
        "screenshot",  # capture full-page evidence screenshot
        "extract",     # read text from an element into history
        "wait",        # wait for an element to appear
        "download",    # click a download trigger and save the file
        "select_option",  # select from a native <select> dropdown
        "save_progress",  # checkpoint partial data without stopping
        "done",        # task complete — write extracted data
        "fail",        # unrecoverable — write reason and stop
    ]
    selector: str | None = None     # click, type, extract, wait, download, select_option — element index or text
    value: str | None = None        # select_option: visible option text or value
    url: str | None = None          # goto
    text: str | None = None         # type
    direction: str | None = None    # scroll: "up" | "down"
    extracted: dict | None = None   # done: the structured output matching output_schema
    note: str | None = None         # fail: reason string
    label: str | None = None        # screenshot: filename label (e.g. "profile", "checks")
    # Structured reflection (per-step self-evaluation)
    evaluation_previous_step: str | None = None  # "Did my last action work?"
    memory_update: str | None = None             # "Key fact to remember"
    next_goal: str | None = None                 # "What I'll do next"
```

Claude always returns one or more of these (multi-action batching when enabled) via `tool_choice={"type":"any"}`. No free-form prose. If it can't proceed, it returns `fail` with a note — never hangs.

### Judgment Mode

When `task_spec.judgment_required = true`, the agent includes judgment fields in its `done` call:

```json
{
  "judgment_required": true,
  "judgment_question": "Did this code change materially affect the calculation?",
  "judgment_output_schema": {
    "answer": "yes | no | inconclusive",
    "confidence": "0.0-1.0",
    "reasoning": "string",
    "evidence_refs": ["array of screenshot filenames"]
  }
}
```

Same loop, same LLM, same tool dispatch. The `done` action's `extracted` dict includes both data fields and judgment fields. One `result.json`, full provenance.

### Loop Detection

A `(url, action_name)` counter is maintained. At count >= 3:

```
[NOTICE] You have taken the same action on this URL 3 times without progress.
Try a different approach or call fail().
```

### Escalating Stagnation Recovery

Beyond simple loop counting, the agent tracks page signature stability (URL + DOM hash). Same page state with no new data triggers escalating responses:

| Level | Trigger | Response |
|-------|---------|----------|
| 1 | 3 stagnant steps | Gentle nudge |
| 2 | 5 stagnant steps | "CHANGE YOUR STRATEGY NOW" + checkpoint |
| 3 | 8 stagnant steps | "MUST call done or fail" |

Budget warnings at 75% and 90% of step budget. Final step restricts tools to `done`/`fail` only.

### Consecutive Failure Recovery

After 3 consecutive `ActionResult(success=False)`, inject a recovery prompt listing all currently-visible interactive elements so the agent can try a different path.

---

## 9. Layer 5 — DOM Extractor

Converts raw Playwright accessibility snapshot (2000+ nodes) into compact, task-relevant context (~20-40 nodes) for the LLM prompt.

### Four-Pass Filter

**Pass 1 — prune dead nodes** (~2000 → ~600)
Drop: no role, no name, `hidden=true`, role in `{none, presentation, generic}` with no children.

**Pass 2 — keep semantic roles** (~600 → ~150)
Whitelist: `button link textbox checkbox radio tab menuitem heading table row cell listitem combobox option status alert img` (img only if has alt text).

**Pass 3 — task-aware keyword scoring** (~150 → ~40)
Boost nodes whose `name`/`value` matches keywords from `task_spec.keywords`. Keep all boosted. Trim zero-score nodes to budget of 20 by tree order.

**Pass 4 — viewport bias** (~40 → ~20)
Prefer elements with bounding box y < viewport height. Deprioritize below-fold.

### DOM Confidence Score

Computed before passing to LLM. If score < 0.6, vision activates automatically.

```python
def dom_confidence(snapshot, page_metrics) -> float:
    total = max(len(snapshot), 1)
    interactive = len([n for n in snapshot if n.role in INTERACTIVE_ROLES])
    interactive = max(interactive, 1)

    score = 1.0
    score -= 0.3 * (page_metrics.canvas_count / total)          # canvas = invisible to DOM
    score -= 0.2 * (page_metrics.missing_aria_labels / interactive)  # unlabeled buttons
    score -= 0.1 * (page_metrics.svg_icon_count / interactive)  # SVG status icons
    return max(0.0, score)
```

Why not just count nodes: GitHub's CI checks page has 15+ meaningful DOM nodes but status icons are pure SVG — node count says "DOM is fine" but the actual pass/fail data is invisible to the a11y tree. The canvas/SVG ratio catches this.

### Output Format (token-efficient text, not JSON)

```
[0] [heading]   "Overview / Repositories / Stars"
[1] [tab]       "Repositories"  (selected=false)
[2] [tab]       "Overview"      (selected=true)
[3] [link]      "linux"  →  https://github.com/torvalds/linux
[4] [text]      "The Linux kernel"
[5] [button]    "Follow"
[6] [text]      "Portland, OR"
[7] [status]    "231k followers · 0 following"
```

Integer indices (`[0]`, `[1]`...) are the primary way the LLM references elements in actions: `click(selector="3")` means click element at index 3.

---

## 10. Layer 6 — Vision Module

Activated when `dom_confidence < 0.6` or task spec requires a screenshot.

### Screenshot Capture

```python
await page.emulate_media(color_scheme="light")    # consistent white background
await page.set_viewport_size({"width": 1280, "height": 900})
data = await page.screenshot(full_page=full_page, type="png", animations="disabled")
```

### Claude Vision — Targeted Questions Only

Never "describe this page." Always specific:
- `"What is the status icon next to 'build / test'? Pass (green check), fail (red X), or pending?"`
- `"Is the form field labeled 'Start Date' filled? If yes, what value?"`
- `"Which user approved this PR? Look for a green checkmark next to a name."`

Targeted questions reduce hallucination. The model answers what it can see.

### Hybrid DOM + Vision Example

```
1. DOM: finds [button "Show all checks"] → agent clicks it
2. DOM: finds check names but SVG icons only → dom_confidence = 0.4
3. Vision activates: "What is the icon next to 'build / test (ubuntu)'?"
4. Vision: "Red X — failure"
5. Combined: check_status = "failed"
```

DOM provides structure + interactable elements (fast, cheap). Vision provides visual interpretation (accurate, targeted). Neither used for what it does poorly.

---

## 11. Layer 7 — Action System

12 actions. Pure functions. Always return `ActionResult`, never raise.

| Action | Playwright Call | Error Policy |
|--------|----------------|--------------|
| `goto` | `page.goto(url, wait_until="networkidle")` | Timeout → ActionResult(success=False) |
| `click` | 3-strategy resolution (see below) | Not found after all strategies → list visible elements |
| `type` | `page.fill(selector, text)` | Not editable → fail clearly |
| `scroll` | `page.mouse.wheel(0, ±600)` | At limit → report scroll position |
| `wait` | `page.wait_for_selector(sel, timeout=10000)` | Timeout → ActionResult(success=False) |
| `screenshot` | `page.screenshot(full_page=True)` | Always succeeds |
| `extract` | `page.inner_text(selector)` | Not found → empty string + warning |
| `download` | `page.expect_download()` + artifact save | Timeout / no file → ActionResult(success=False) |
| `select_option` | `locator.select_option(label|value)` | Missing native select / missing option → fail clearly |
| `save_progress` | Checkpoint data, continue loop | Always succeeds |
| `done` | Write result + signal loop exit | Always succeeds |
| `fail` | Write failure + signal loop exit | Always succeeds |

### Element Resolution — Three Strategies

Tried in order until one succeeds:

1. **Index-based** — `click(selector="3")` → resolves index 3 from DOM extractor's map. Fastest, most reliable.
2. **Text-based** — `click(selector="Show all checks")` → `page.get_by_text(value)`. Case-insensitive, partial match fallback.
3. **CSS selector** — last resort, explicitly discouraged in prompts.

### After Every Navigation Action

```python
await page.wait_for_load_state("networkidle")  # mandatory — never skip
```

This is the single biggest source of flakiness if omitted.

---

## 12. Layer 8 — Output & Evidence

Zero Playwright. Pure file I/O. All paths relative to `evidence/{sample_id}/`.

### SHA-256 on Every Artifact

```python
def save_screenshot(self, data: bytes, label: str, source_url: str) -> dict:
    self._counter += 1
    filename = f"{self._counter:02d}_{label}.png"
    path = self.sample_dir / filename
    path.write_bytes(data)
    sha256 = hashlib.sha256(data).hexdigest()
    return {"filename": filename, "sha256": sha256,
            "source_url": source_url, "timestamp": datetime.utcnow().isoformat()}
```

### `result.json` — Evidence Manifest

```json
{
  "sample_id": "torvalds",
  "status": "done",
  "steps": 4,
  "extracted": {
    "display_name": "Linus Torvalds",
    "bio": "Just a simple coder",
    "company": "Linux Foundation",
    "location": "Portland, OR",
    "followers": 231400,
    "pinned_repos": ["linux", "subsurface"]
  },
  "artifacts": [
    {
      "filename": "01_profile.png",
      "sha256": "a3f9c2...",
      "source_url": "https://github.com/torvalds",
      "timestamp": "2026-03-27T10:01:03Z"
    }
  ],
  "judgment": null,
  "flagged": false,
  "notes": [],
  "started_at": "2026-03-27T10:01:00Z",
  "finished_at": "2026-03-27T10:01:18Z"
}
```

### `action_log.json` — Process Audit Trail

Separate from `result.json`. Every step: thinking, action, outcome. Fully replayable.

```json
[
  {
    "step": 1,
    "thinking": "Profile page loaded. Take a screenshot first.",
    "action": "screenshot",
    "params": {"label": "profile"},
    "result": "Saved 01_profile.png",
    "timestamp": "2026-03-27T10:01:02Z"
  }
]
```

### `combined.csv`

Single merge at batch end — `main.py` reads all `result.json` files after all workers finish and writes one CSV in sorted order. No concurrent writes, no filelock needed. Simpler, deterministic.

```python
# in main.py, after all workers complete:
results = []
for sample_dir in evidence_dir.iterdir():
    result_file = sample_dir / "result.json"
    if result_file.exists():
        results.append(json.loads(result_file.read_text()))
results.sort(key=lambda r: r["sample_id"])
write_csv("combined.csv", results, task_spec.output_schema)
```

### Evidence Output Contract

Every sample, every task type, same structure:

```
evidence/
└── {sample_id}/
    ├── 01_{label}.png           # sequential, deterministic naming
    ├── 02_{label}.png
    ├── result.json              # extracted fields + artifacts + SHA-256
    └── action_log.json          # every agent step
combined.csv                     # one row per sample
```

Idempotent: crash at sample 47 → restart picks up at 48. Partial evidence preserved on failure.

---

## 13. Task Spec Schema

**All site-specific knowledge lives here. Agent code never changes.**

### Template (`tasks/_template.json`)

```json
{
  "task_id": "unique_name",
  "phase": "discovery | execution",
  "start_url": "https://...",

  "system_prompt": "You are a browser agent. Your job is to [goal]. Extract only what you can see. Set missing fields to null. Never guess.",

  "goal": "Natural language: what to collect, when to stop.",

  "keywords": ["relevant", "terms", "for", "pruning"],

  "output_schema": {
    "field_name": "string | null",
    "count": "number | null"
  },

  "max_steps": 25,

  "required_fields": ["field_name"],
  "required_artifacts": ["screenshot"],

  "judgment_required": false,
  "judgment_question": null,
  "judgment_output_schema": null,

  "pagination": false,
  "stop_condition": "natural language description of done state",

  "input_schema": {},
  "auth_profile": null
}
```

### Example: GitHub Commit Audit (with judgment)

```json
{
  "task_id": "github_commit_audit",
  "phase": "execution",
  "start_url": "https://github.com/{org}/{repo}/commit/{sha}",
  "system_prompt": "You are a browser audit agent. For each commit: screenshot the commit page, navigate to its PR, screenshot the PR, expand checks and screenshot. If any check failed AND PR was merged, navigate into the CI failure and screenshot. If Jira link in PR description, follow it and screenshot.",
  "goal": "Collect: commit SHA, PR number, creator, approvers, merger, check statuses, CI failure details, Jira URL. Screenshot every significant page.",
  "keywords": ["commit", "PR", "checks", "passed", "failed", "approve", "merge", "jira"],
  "output_schema": {
    "commit_sha": "string",
    "pr_number": "string | null",
    "pr_creator": "string | null",
    "approvers": "array | null",
    "merger": "string | null",
    "checks_passed": "number | null",
    "checks_failed": "number | null",
    "merged_with_failures": "boolean | null",
    "ci_failure_details": "string | null",
    "jira_url": "string | null"
  },
  "max_steps": 30,
  "judgment_required": true,
  "judgment_question": "Was this PR merged with failing CI checks? Were failures material or non-material?",
  "judgment_output_schema": {
    "answer": "yes | no | inconclusive",
    "confidence": "0.0-1.0",
    "reasoning": "string",
    "evidence_refs": ["array of screenshot filenames"]
  },
  "stop_condition": "all fields extracted, all screenshots taken, judgment recorded"
}
```

### Adding a New Site

Write one JSON file. No Python changes.

The LLM reasons about the live DOM. It doesn't know what site it's on — only what's in the a11y tree and what the task spec says to look for.

---

## 14. Tech Stack

| Layer | Library | Why |
|-------|---------|-----|
| Browser | `playwright` async | Direct control, a11y tree, screenshot, download |
| LLM | `anthropic` SDK | Tool use, structured output, prompt caching |
| Model (primary) | `claude-sonnet-4-6` | Fast (<2s/turn), accurate, cost-effective |
| Model (vision) | `claude-sonnet-4-6` | Same model, multimodal for screenshot analysis |
| Concurrency | `asyncio` + `Semaphore` | No Redis, no broker, stdlib only |
| Schemas | `pydantic v2` | Task spec + action schema + result models |
| File locking | `filelock` | Safe concurrent CSV writes |
| Hashing | `hashlib` (stdlib) | SHA-256 per artifact |
| Logging | `loguru` | Per-sample bound context |
| Progress | `rich` | Live terminal dashboard |
| Credentials | `python-dotenv` | .env file support |
| Downloads | `httpx` | File downloads with session cookies |

```
# requirements.txt
playwright>=1.42
anthropic>=0.25
pydantic>=2.0
filelock>=3.13
loguru>=0.7
rich>=13.0
python-dotenv>=1.0
httpx>=0.27
```

### What Is Excluded (and why)

| Package | Why Excluded |
|---------|-------------|
| `browser-use` | Project requires custom agent loop — no agent frameworks |
| `langchain` | Unnecessary abstraction |
| `selenium` | Playwright is async-native, more reliable |
| `beautifulsoup4` | DOM via a11y tree, not HTML parsing |

---

## 15. Build Order

| # | Time | What | Test |
|---|------|------|------|
| 1 | 1.5h | `tools/browser.py` + `tools/output.py` | Call each tool, verify files written |
| 2 | 1h | `core/dom_extractor.py` | Print pruned tree for a real GitHub page |
| 3 | 2h | `agent_loop.py` + `models/actions.py` | Run loop on one GitHub profile |
| 4 | 0.5h | `worker.py` + `main.py` | Run 3 samples in parallel |
| 5 | 1h | `discover.py` | Discover members from github.com/orgs/microsoft/people |
| 6 | 1h | Task specs + judgment mode | Run commit audit on 5 commits |
| 7 | rest | Scale test 50 samples, fix flakiness, README | — |

**Start with step 1.** Working tools let you test everything else in isolation.

---

## Rate Limiting

Per-domain intervals applied before every `goto`:

```python
RATE_LIMITS = {
    "linkedin.com": 3.0,    # seconds between requests
    "github.com": 0.5,
    "atlassian.net": 1.0,
    "default": 0.2,
}
```

---

## What Makes This System-Agnostic

The agent code contains zero knowledge of GitHub, LinkedIn, Jira, Linear, or Workday.

The only things that know about a specific site:
- `tasks/{site}.json` — goal, keywords, output schema, system prompt
- `.env` — credentials

To add a new site: write one JSON file. No Python changes.
