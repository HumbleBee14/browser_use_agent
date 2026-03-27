# General Browser Agent — Final Architecture

**Version:** 2.0 (merged)
**Stack:** Python 3.11 · Playwright (async) · Anthropic SDK · Pydantic v2
**Constraint:** No browser-use. No LangChain. Custom agent loop only.
**Build time:** One day

---

## The One-Sentence Design

A site-agnostic browser agent that discovers sample lists by navigating any web UI,
then collects structured evidence from each sample in parallel — driven by a single
custom ReAct loop with Claude, zero hardcoded site logic, and a swappable JSON task
spec per target system.

---

## What We Kept From Each Proposal

**Kept from my (Claude's) proposal:**
- Task spec JSON as the only site-specific config — swapping site = swapping one file
- Single `agent_loop.py` — no 4-agent class hierarchy, same capability in far less code
- `tool_choice={"type":"any"}` forcing structured output always — no free-form prose
- 5-action rolling history cap — token cost stays flat regardless of run length
- Build order: tools → loop → orchestrator (each layer testable in isolation)

**Kept from your agent's proposal:**
- `dom_confidence` scoring — quantified per-page trust in DOM vs vision
- SHA-256 hash on every artifact — audit-grade tamper evidence
- `action_log.json` separate from `result.json` — process vs evidence
- `JudgeAgent` concept — as a loop *mode*, not a separate class
- `DomainRateLimiter` with per-domain intervals
- `EvidenceArtifact` model with `source_url`, `sha256`, `artifact_ref`
- Graceful degradation: always return a result, never raise from the loop
- Consecutive failure tracking with recovery prompt injection

**Cut from your agent's proposal (over-engineered for one day):**
- 4-agent class hierarchy (PlannerAgent / NavigatorAgent / ExtractorAgent / JudgeAgent)
- AgentPool with LRU eviction and background idle tasks
- Set-of-Mark image annotation
- 4-week phased implementation plan
- Strategy family classes (GraphTraversal, SinglePageExtraction, etc.)
- Manifest validation in EvidencePackager
- `run_summary.json` with checkpoint pass rates

---

## Repository Structure

```
agent/
├── main.py                   # entry point: discovery → execution → merge CSV
├── discover.py               # phase 1: navigate start URL, paginate, write samples.csv
├── worker.py                 # phase 2: one BrowserContext per sample + agent_loop call
├── agent_loop.py             # THE core: ReAct observe→decide→act, all modes
│
├── core/
│   ├── dom_extractor.py      # a11y pruner, dom_confidence score, serializer
│   ├── vision_module.py      # screenshot capture, Claude vision, hybrid dispatch
│   ├── action_registry.py    # 16 typed actions, tool schema, Playwright dispatch
│   ├── memory_manager.py     # rolling 5-action buffer, token budget enforcement
│   └── llm_client.py         # Anthropic SDK wrapper, tool use, prompt caching
│
├── tools/
│   ├── browser.py            # thin Playwright wrappers (goto, click, type, scroll…)
│   └── output.py             # save_screenshot (SHA-256), write_metadata, write_csv
│
├── models/
│   ├── task.py               # TaskSpec (Pydantic), loaded from tasks/*.json
│   ├── evidence.py           # SampleResult, EvidenceArtifact, FieldExtraction
│   └── actions.py            # AgentAction schema, ActionResult
│
├── tasks/
│   ├── github_discovery.json
│   ├── github_profile.json
│   ├── linear_tickets.json
│   └── _template.json
│
├── evidence/                 # output root — one folder per sample_id
│   └── {sample_id}/
│       ├── 01_{label}.png
│       ├── 02_{label}.png
│       ├── result.json       # extracted fields + artifact manifest
│       └── action_log.json   # every step: thinking, action, outcome
│
├── samples.csv               # written by discover.py, consumed by main.py
├── combined.csv              # merged at end of run
└── .env                      # ANTHROPIC_API_KEY, credentials
```

---

## Layer 1 — Orchestrator (`main.py`)

Single responsibility: coordinate discovery → execution → output merge.

```
1. Parse plain-text instruction → load matching task spec JSON
2. Run discover.py → writes samples.csv
3. Read samples.csv
4. Skip samples where evidence/{sample_id}/result.json has status:"done"  ← idempotency
5. asyncio.gather(*[worker(s) for s in pending], return_exceptions=True)
6. After all workers: merge all result.json → combined.csv
```

Key decisions:
- `return_exceptions=True` — one bad sample never kills the run
- Idempotency on startup — restart after crash picks up from where it stopped
- `asyncio.Semaphore(N)` caps concurrency — default N=5, tunable per task type

---

## Layer 2 — Discovery (`discover.py`)

One sequential browser session. Builds the work queue.

Runs `agent_loop` with the discovery task spec. The loop paginates (URL-based `?page=N`
or click-based "next") until the agent calls `done` with an empty member list.

Challenges handled:

| Challenge | Solution |
|---|---|
| Link noise (nav / footer / org links) | LLM filters using goal + a11y context |
| Pagination termination | Agent calls `done` when page returns zero member links |
| Deduplication | `seen: set` in discover.py checked before appending |
| Rate limiting | `asyncio.sleep(1)` between pages + 429 title detection |
| URL vs click pagination | LLM reasons from live page state — no hardcoding |

Output — `samples.csv`:
```
sample_id,url,discovered_at
torvalds,https://github.com/torvalds,2026-03-27T10:00:00Z
```

---

## Layer 3 — Worker (`worker.py`)

Owns one sample's full lifecycle.

```python
async def run_sample(browser, sem, sample, task_spec):
    async with sem:
        ctx = await browser.new_context()        # isolated: own cookies, session
        page = await ctx.new_page()
        try:
            await agent_loop(page, sample, task_spec, output_dir)
        except Exception as e:
            write_metadata(output_dir, {"status": "failed", "reason": str(e)})
        finally:
            await ctx.close()
```

Each worker gets an isolated `BrowserContext` — no session bleed between samples.
If a worker throws, the exception is caught here and written to `result.json`.
The semaphore slot is always released in `finally`.

---

## Layer 4 — Agent Loop (`agent_loop.py`)

**This is the entire agent.** Everything else is scaffolding around this loop.

### The ReAct cycle

```
for step in range(task_spec.max_steps):           ← default 25, configurable

    1. OBSERVE
       page_state = dom_extractor.snapshot(page, task_spec.keywords)
       if dom_confidence < 0.6:
           page_state += vision_module.analyze(page, targeted_question)

    2. DECIDE
       response = llm_client.call(
           system   = task_spec.system_prompt,     ← static, prompt-cached
           messages = build_prompt(page_state, history[-5:], task_spec)
           tools    = action_registry.tool_schema()
           tool_choice = {"type": "any"}           ← forces structured output always
       )
       action = AgentAction(**response.tool_input)
       history.append(action)

    3. ACT
       result = action_registry.dispatch(action, page)
       # result is always ActionResult — never raises

    4. CHECK TERMINATION
       if action.action == "done":  write_result(action.extracted); return
       if action.action == "fail":  write_result(status="failed");   return

    5. LOOP DETECTION
       if same (url, action) seen 3+ times:
           inject recovery nudge into next observation

# Reached max_steps without done/fail:
write_result(status="failed", reason="max_steps_exceeded")
```

### What Claude sees each turn

```
SYSTEM (static — prompt cached across all steps):
  {task_spec.system_prompt}

USER (rebuilt every turn):
  ## Current page state
  {pruned a11y tree — ~20-40 nodes, task-keyword boosted}

  ## Actions taken so far
  {last 5 actions only — never grows unbounded}

  ## Goal
  {task_spec.goal}

  ## Output schema (populate when calling done)
  {task_spec.output_schema}

  Take the single best next action.
```

### Action schema — 9 typed actions, no free-form prose ever

```python
class AgentAction(BaseModel):
    action: Literal[
        "goto",        # navigate to a URL
        "click",       # click an element
        "type",        # fill an input field
        "scroll",      # scroll up or down
        "screenshot",  # capture full-page evidence screenshot
        "extract",     # read text from a selector into history
        "wait",        # wait for a selector to appear
        "done",        # task complete — write extracted data
        "fail",        # unrecoverable — write reason and stop
    ]
    selector: str | None = None     # click, type, extract, wait
    url: str | None = None          # goto
    text: str | None = None         # type
    direction: str | None = None    # scroll: "up" | "down"
    extracted: dict | None = None   # done: the structured output
    note: str | None = None         # fail: reason string
    label: str | None = None        # screenshot: filename label
```

### Judgment mode (JudgeAgent — no new class needed)

When `task_spec.judgment_required = true`, the agent loop uses a different system
prompt for the final step. Same loop, same LLM, same tool dispatch:

```json
// in task spec:
{
  "judgment_required": true,
  "judgment_question": "Did this code change materially affect the calculation?",
  "judgment_output_schema": {
    "answer": "yes | no | inconclusive",
    "confidence": "0.0–1.0",
    "reasoning": "string",
    "evidence_refs": ["array of screenshot filenames"]
  }
}
```

The agent's `done` action `extracted` dict then includes the judgment fields alongside
the regular extracted data. One `result.json`, full provenance.

### Loop detection

A `(url, action_name)` counter is maintained. At count ≥ 3, inject into next observation:

```
[NOTICE] You have taken the same action on this URL 3 times without recording
new evidence. Do not repeat it. Try a different approach or call fail().
```

### Consecutive failure tracking

`ActionResult` always returns, never raises. After 3 consecutive `success=False`:
inject a recovery prompt listing all currently-visible interactive elements.

---

## Layer 5 — DOM Extractor (`core/dom_extractor.py`)

Converts raw Playwright accessibility snapshot (2000+ nodes) into a compact,
task-relevant context (~20-40 nodes) that fits the LLM prompt.

### Four-pass filter

**Pass 1 — prune dead nodes** (~2000 → ~600)
Drop: no role, no name, `hidden=true`, role in `{none, presentation, generic}` with no children.

**Pass 2 — keep semantic roles** (~600 → ~150)
Whitelist: `button link textbox checkbox radio tab menuitem heading table row
cell listitem combobox option status alert img` (img only if has alt text).

**Pass 3 — task-aware keyword scoring** (~150 → ~40)
Boost nodes whose `name`/`value` matches keywords from `task_spec.keywords`.
Keep all boosted. Trim zero-score nodes to a budget of 20 by tree order.

**Pass 4 — viewport bias** (~40 → ~20)
Prefer elements with bounding box y < viewport height. Deprioritize below-fold.

### `dom_confidence` score

Computed before passing to LLM. If score < 0.6, vision module activates automatically.

```python
def dom_confidence(snapshot, page_metrics) -> float:
    score = 1.0
    score -= 0.3 * (canvas_elements / total_elements)   # canvas = invisible to DOM
    score -= 0.2 * (missing_aria_labels / interactive_count)
    score -= 0.1 * (svg_icon_count / interactive_count) # SVG status icons
    return max(0.0, score)
```

### Output format (token-efficient text, not JSON)

```
[heading]   "Overview / Repositories / Stars"
[tab]       "Repositories"  (selected=false)
[tab]       "Overview"      (selected=true)
[link]      "linux"  →  https://github.com/torvalds/linux
[text]      "The Linux kernel"
[button]    "Follow"
[text]      "Portland, OR"
[status]    "231k followers · 0 following"
```

---

## Layer 6 — Vision Module (`core/vision_module.py`)

Activated when `dom_confidence < 0.6` or task spec requires a screenshot.

### Screenshot capture

```python
await page.emulate_media(color_scheme="light")   # consistent for audit docs
await page.set_viewport_size({"width": 1280, "height": 900})
bytes = await page.screenshot(full_page=full_page, type="png", animations="disabled")
```

### Claude vision — targeted questions only

Never "describe this page." Always specific:
- `"What is the status of the 'build / test' check? Pass, fail, or pending?"`
- `"Is the form field labeled 'Start Date' filled? If yes, what value?"`
- `"Which user approved this PR? Look for a green checkmark next to a name."`

Targeted questions reduce hallucination risk. The model answers what it can see.

### Hybrid DOM + Vision

DOM finds the structure → Vision reads the visual-only signals:

```
1. DOM: finds [button "Show all checks"] → agent clicks it
2. DOM: finds check names + some text but SVG icons only → dom_confidence = 0.4
3. Vision activates: "What is the icon next to 'build / test (ubuntu)'?"
4. Vision: "Red X — failure"
5. Combined: check_status = "failed", source: a11y index 3 + screenshot 03_checks.png
```

---

## Layer 7 — Action Registry (`core/action_registry.py`)

All 16 actions. Pure functions. Always return `ActionResult`, never raise.

| Action | Playwright call | Error policy |
|---|---|---|
| `goto` | `page.goto(url, wait_until="networkidle")` | Timeout → fail step |
| `click` | `page.click(selector)` | Not found after 3 strategies → list visible elements |
| `type` | `page.fill(selector, text)` | Not editable → fail clearly |
| `scroll` | `page.mouse.wheel(0, ±600)` | At limit → report position |
| `wait` | `page.wait_for_selector(sel, timeout=10000)` | Timeout → ActionResult(success=False) |
| `screenshot` | `page.screenshot(full_page=True)` | Always succeeds |
| `extract` | `page.inner_text(selector)` | Not found → empty string + warning |
| `done` | write result + signal loop exit | Always succeeds |
| `fail` | write failure + signal loop exit | Always succeeds |

**Element resolution — three strategies tried in order:**
1. Index-based: fastest, most reliable (from DOM extractor's index map)
2. Text-based: `page.get_by_text(value)` — case-insensitive, partial match fallback
3. Selector-based: last resort — explicitly discouraged in agent system prompts

After every `goto` / `click` / `scroll`:
```python
await page.wait_for_load_state("networkidle")   # mandatory — never skip
```
This is the single biggest source of flakiness if omitted.

---

## Layer 8 — Output (`tools/output.py`)

Zero Playwright. Pure file I/O. All paths relative to `evidence/{sample_id}/`.

### SHA-256 on every artifact

```python
def save_screenshot(data: bytes, label: str, source_url: str) -> EvidenceArtifact:
    self._counter += 1
    filename = f"{self._counter:02d}_{label}.png"
    path = self.sample_dir / filename
    path.write_bytes(data)
    sha256 = hashlib.sha256(data).hexdigest()
    artifact = EvidenceArtifact(
        filename=filename, sha256=sha256,
        source_url=source_url, timestamp=datetime.utcnow()
    )
    self._artifacts.append(artifact)
    return artifact
```

### `result.json` — evidence manifest

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
  "flagged": false,
  "notes": [],
  "started_at": "2026-03-27T10:01:00Z",
  "finished_at": "2026-03-27T10:01:18Z"
}
```

### `action_log.json` — process audit trail

Separate from `result.json`. Every step: LLM thinking, action taken, outcome.
An engineer can replay exactly what the agent did without re-running the browser.

```json
[
  {
    "step": 1,
    "thinking": "Page shows a GitHub profile. I should take a full screenshot first then extract fields.",
    "action": "screenshot",
    "params": {"label": "profile", "full_page": true},
    "result": "Saved 01_profile.png",
    "timestamp": "2026-03-27T10:01:02Z"
  }
]
```

### `combined.csv` — filelock protected

Multiple workers write simultaneously. One lock for the shared file:
```python
with FileLock("combined.csv.lock"):
    with open("combined.csv", "a") as f:
        writer.writerow(row)
```

---

## Task Spec Schema (`tasks/_template.json`)

**All site-specific knowledge lives here. Agent code never changes.**

```json
{
  "task_id": "string",
  "phase": "discovery | execution",
  "start_url": "https://...",

  "system_prompt": "You are a browser agent. Your job is to [specific goal]. Extract only what you can see. Set missing fields to null. Never guess.",

  "goal": "Natural language: what to collect, when to stop, what to do if a field is missing.",

  "keywords": ["word1", "word2"],

  "output_schema": {
    "field": "string | null",
    "count": "number | null",
    "items": "array | null"
  },

  "max_steps": 25,

  "judgment_required": false,
  "judgment_question": null,
  "judgment_output_schema": null,

  "pagination": false,
  "stop_condition": "natural language description of done state"
}
```

### `tasks/github_discovery.json`

```json
{
  "task_id": "github_discovery",
  "phase": "discovery",
  "start_url": "https://github.com/orgs/microsoft/people",
  "system_prompt": "You are a browser agent collecting member profile URLs from a GitHub org people page. The page is URL-paginated. Navigate page by page appending ?page=N. Stop when a page has no member links.",
  "goal": "Collect all member usernames and profile URLs. Member links have exactly one path segment and their visible text matches the username. Ignore nav, org, footer links.",
  "keywords": ["member", "people", "username", "profile"],
  "output_schema": {
    "members": [{"username": "string", "url": "string"}]
  },
  "max_steps": 300,
  "pagination": true,
  "stop_condition": "page returns zero member profile links"
}
```

### `tasks/github_profile.json`

```json
{
  "task_id": "github_profile",
  "phase": "execution",
  "start_url": "https://github.com/{username}",
  "system_prompt": "You are a browser agent extracting public profile data. Take a full-page screenshot. Extract all visible fields. Set missing fields to null.",
  "goal": "Extract: display name, bio, company, location, followers, following, pinned repos. Take one full-page screenshot.",
  "keywords": ["bio", "followers", "following", "pinned", "company", "location"],
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

### `tasks/github_commit_audit.json` (Task 2 — with judgment)

```json
{
  "task_id": "github_commit_audit",
  "phase": "execution",
  "start_url": "https://github.com/{org}/{repo}/commit/{sha}",
  "system_prompt": "You are a browser audit agent. For each commit: screenshot the commit page, navigate to its PR, screenshot the PR, expand the checks section and screenshot it. If any check failed AND the PR was merged, navigate into the CI failure and screenshot it. If a Jira link appears in the PR description, follow it and screenshot the Jira ticket.",
  "goal": "Collect: commit SHA, PR number, PR creator, approvers, merger, check statuses (pass/fail/optional), CI failure details if merged with failures, Jira ticket URL if present. Screenshot every significant page.",
  "keywords": ["commit", "PR", "checks", "passed", "failed", "approve", "merge", "jira", "ci", "workflow"],
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
  "judgment_question": "Was this PR merged with failing CI checks? If yes, were those failures material (blocking tests) or non-material (optional/flaky)?",
  "judgment_output_schema": {
    "answer": "yes | no | inconclusive",
    "confidence": "0.0–1.0",
    "reasoning": "string",
    "evidence_refs": ["array of screenshot filenames"]
  },
  "stop_condition": "all fields extracted, all screenshots taken, judgment recorded"
}
```

---

## Tech Stack

| Layer | Library | Why |
|---|---|---|
| Browser | `playwright` async | Direct control, no abstraction layer |
| LLM | `anthropic` SDK | Tool use, structured output, prompt caching |
| Model (primary) | `claude-sonnet-4-5` | Fast (<2s/turn), accurate, cheap at scale |
| Model (extraction) | `claude-haiku-4-5` | Simple field extraction — 60% cost saving |
| Concurrency | `asyncio` + `Semaphore` | No external queue, no Redis, no broker |
| Schema validation | `pydantic v2` | Task spec + action schema + result models |
| Async file I/O | `aiofiles` | Non-blocking writes across parallel workers |
| CSV lock | `filelock` | One shared file, many concurrent writers |
| Hashing | `hashlib` (stdlib) | SHA-256 per artifact, no extra dependency |
| Logging | `loguru` | Per-sample bound context across async workers |
| Progress | `rich` | Live terminal dashboard during batch runs |
| Credentials | `python-dotenv` | Never hardcode tokens |
| Rate limiting | Custom `DomainRateLimiter` | Per-domain intervals (LinkedIn: 3s, GitHub: 0.5s) |
| Image I/O | `Pillow` | Screenshot resize + compress before vision call |
| Downloads | `httpx` | `download_file()` with session cookies from context |

```toml
[tool.poetry.dependencies]
python = "^3.11"
playwright = "^1.42"
anthropic = "^0.25"
pydantic = "^2.0"
aiofiles = "^23.0"
filelock = "^3.13"
loguru = "^0.7"
rich = "^13.0"
python-dotenv = "^1.0"
Pillow = "^10.0"
httpx = "^0.27"
```

---

## Domain Rate Limiter

```python
RATE_LIMITS = {
    "linkedin.com": 3.0,
    "github.com": 0.5,
    "atlassian.net": 1.0,
    "default": 0.2,
}
```

Configured in `.env`, applied automatically per worker before every `goto`.

---

## Build Order (one day)

| # | Time | Deliverable | Test |
|---|---|---|---|
| 1 | 1.5h | `tools/browser.py` + `tools/output.py` | Call each tool directly, verify files written |
| 2 | 1h | `core/dom_extractor.py` | Print pruned tree for a real page |
| 3 | 2h | `core/action_registry.py` + `agent_loop.py` | Run loop on a single Linear ticket |
| 4 | 0.5h | `worker.py` + `main.py` | Run 3 samples in parallel |
| 5 | 1h | `discover.py` | Run against github.com/orgs/microsoft/people |
| 6 | 1h | `github_commit_audit.json` + judgment mode | Run on 5 commits end-to-end |
| 7 | remaining | Scale test 50 samples, fix flakiness, README | — |

Start with step 1. Working tools let you test everything else in isolation before wiring the loop.

---

## Evidence Output Contract

Every sample, every task type, same structure:

```
evidence/
└── {sample_id}/
    ├── 01_{label}.png           # sequential, deterministic naming
    ├── 02_{label}.png
    ├── result.json              # extracted fields + artifact manifest + SHA-256
    └── action_log.json          # every agent step with LLM thinking
combined.csv                     # one row per sample, all fields
```

The `sample_id` is stable. If the run crashes at sample 47, restart picks up from 48.
If a worker fails partway, `result.json` captures whatever was collected before failure.
Nothing is lost, nothing is duplicated.

---

## What Makes This System-Agnostic

The agent code contains zero knowledge of GitHub, LinkedIn, Jira, Linear, or Workday.

The only things that know about a specific site:
- `tasks/{site}.json` — goal, keywords, output schema, system prompt
- `.env` — credentials

To add a new site: write one JSON file. No Python changes.

The LLM reasons about the live DOM of whatever site it lands on. It doesn't know
what site it's on — it only knows what it can see in the accessibility tree and
what the task spec tells it to look for.
