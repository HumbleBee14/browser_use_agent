# Long-Horizon Task Support

**Status:** merged into `main`

---

## The Problem

Standard tasks (profile extraction, single-page audit) complete in 2-10 steps. Long-horizon tasks — multi-page audits, cross-link navigation chains, paginated data collection — need 30-50+ steps.

Before these changes, the agent had:

- Hard `max_steps` ceiling (only termination)
- Fixed 5-action history (forgets everything older)
- All-or-nothing output (`done` or `failed`, no partial saves)
- No awareness of how much work is left

---

### Part 1 — Incremental Checkpointing

**Files changed:** `agent_loop.py`, `models/actions.py`, `tools/output.py`

**`save_progress` — the 10th agent action:**

The agent can now call `save_progress(extracted, note)` to checkpoint partial data without stopping. Data is deep-merged across calls — arrays append, dicts recurse.

```
Step 8:  save_progress({ "prs": [{ title: "Fix editor", author: "alice" }] }, "PR #1 done")
Step 16: save_progress({ "prs": [{ title: "Refactor sync", author: "bob" }] }, "PR #2 done")
Step 22: done({ "total_audited": 2 })
         → accumulated data merged with final extraction → result.json
```

If the agent crashes at step 20, `checkpoint.json` has all data from steps 8 and 16.

**Live `checkpoint.json`:**

Written to the sample's evidence folder every 5 steps and on every `save_progress` call:

```json
{
  "sample_id": "pr_chain_audit",
  "status": "in_progress",
  "step": 16,
  "max_steps": 50,
  "accumulated_data": { "prs": [{ "title": "Fix editor", "author": "alice" }, ...] },
  "progress_notes": ["PR #1 done", "PR #2 done"],
  "artifacts_so_far": [{ "filename": "01_pr_overview.png", "sha256": "..." }],
  "steps_logged": 16,
  "updated_at": "2026-03-27T18:30:00Z"
}
```

`action_log.json` is also flushed at checkpoint time, so long-running tasks keep a live step trace on disk instead of only writing it at final completion.

Watch it live while the agent runs:

```bash
watch -n 2 cat evidence/run_XXXX/sample_id/checkpoint.json
```

**Step budget awareness:**

Every prompt now includes: `Step 16 of 40 (24 remaining)` — the agent knows how much time it has left.

**Accumulated data in prompt:**

The full `accumulated` dict (from all `save_progress` calls) is shown in the prompt as a JSON block under "Data collected so far." This means when the agent is on PR #5, it can see what it collected from PRs #1-4.

---

### Part 2 — Smarter Memory

**Files changed:** `agent_loop.py`

**LLM-powered step summaries:**

Every 10 steps, Claude (Haiku — fast/cheap model) summarizes old history:

```
Steps 1-10: Navigated to the merged PR list, clicked into PR #305569.
Extracted title, author, reviewer. Took screenshot and saved progress.
Navigated back to the list.
```

This replaces the raw step list for old history. Summaries now use a **structured FOUND/GAPS/NEXT** format (see Part 5). The agent sees:

- Structured LLM summaries of earlier work (findings, gaps, next actions)
- Dynamic budget-fitted recent actions (5-25 items, importance-scored)
- Full accumulated data (what was collected)
- Structured run state (failures, dead ends, blocked selectors)

Cost: < $0.001 per summary. Falls back to mechanical concatenation if the LLM call fails.

**Extract → accumulated buffer:**

Every `extract` action result is now stored in `accumulated["extracted_texts"]`. This means if the agent extracts text from a page, that text persists in memory even after the 5-action history window slides past it.

---

### Part 3 — Continuous Operation

**Files changed:** `agent_loop.py`, `task_planner.py`, `main.py`

**Auto-pagination:**

When the agent clicks "Next", "Load more", "Page 2", etc., the system detects it via keyword matching and grants **+3 bonus steps**. Pagination doesn't eat the task's working budget.

Detection keywords: `next`, `next page`, `load more`, `show more`, `older`, `newer`, `»`, `›`, `→`

```
Step 15 | click("Next page") → Pagination detected → +3 bonus (effective_max=43)
```

**Watchdog (stall detection):**

If 5 consecutive steps produce no new data (no `save_progress` with genuinely new data, or successful `extract`), the watchdog injects:

```
WARNING: You have not produced new data in 5 steps.
You have 12 steps left. Either extract/save_progress with data,
or call done with what you have, or call fail.
```

Also writes a checkpoint so no accumulated data is lost if the stall continues.

**Batch chunking:**

`task_planner.py` now has `plan_chunked()` that detects large-scale tasks (10+ items with individual URLs). It generates a discovery spec alongside the execution spec. `main.py` auto-runs discovery → collects URLs → distributes as parallel samples.

---

### Smart Termination

**Files changed:** `agent_loop.py`, `models/task.py`, `models/actions.py`, `main.py`

Before every step, `_check_termination()` evaluates multiple signals:

| Trigger                         | Status                       | When                                   |
| ------------------------------- | ---------------------------- | -------------------------------------- |
| `done` + all requirements met   | `done`                       | Agent satisfied all fields + artifacts |
| `done` + array count < expected | `partial_success`            | Got some items but not all             |
| Wall-clock timeout              | `partial_success` / `failed` | `max_time_seconds` exceeded            |
| Network circuit breaker         | `partial_success` / `failed` | 5 consecutive infra errors             |
| Watchdog stall                  | warning injected             | 5 steps, no new data                   |
| `max_steps` exhausted           | `failed`                     | Hard ceiling (accumulated data saved)  |
| LLM API error                   | `failed`                     | Claude unreachable                     |
| `fail(reason)`                  | `failed`                     | Agent gives up intentionally           |

**Infrastructure error classification:**

`_is_infra_error()` distinguishes network/browser failures from logic errors:

- Infra: timeout, DNS, connection refused, page crashed, SSL, browser closed
- Logic: element not found, click failed, selector mismatch

Only infra errors count toward the circuit breaker. A wrong CSS selector does NOT trigger early termination.

**New `partial_success` status:**

When the agent collected some data but couldn't finish (site down, timeout, incomplete items). The accumulated data is preserved in `result.json`. Better than binary done/failed.

**New TaskSpec fields:**

```json
{
  "max_steps": 50,
  "max_time_seconds": 300,
  "expected_items": 5,
  "max_consecutive_network_errors": 5
}
```

---

## New Files Created

| File                                       | Purpose                                                  |
| ------------------------------------------ | -------------------------------------------------------- |
| `tasks/github_pr_audit_chain.json`         | PR audit task spec (50 steps, judgment, save_progress)   |
| `tasks/github_contributor_deep_audit.json` | Contributor deep audit (40 steps, cross-page navigation) |
| `tasks/inputs/github_pr_chain.csv`         | Input CSV for PR audit                                   |
| `tasks/inputs/github_pr_audit.csv`         | Alternative PR input                                     |
| `tasks/inputs/github_contributors.csv`     | Input CSV for contributor audit                          |

---

## How to Test

### Test 1: Contributor Deep Audit (recommended first)

```bash
cd playwright_agent
python main.py --task tasks/github_contributor_deep_audit.json \
  --input tasks/inputs/github_contributors.csv --no-headless
```

**What happens:** Agent visits vscode contributors page → clicks top 3 contributor profiles → extracts name/company/location/followers from each → screenshots each → `save_progress` after each → navigates back → repeats.

**What to watch for:**

- `checkpoint.json` appearing and growing after each contributor
- Agent navigating back to the contributors list between profiles
- Console showing `save_progress #1`, `save_progress #2`, `save_progress #3`
- Final `result.json` with all 3 contributors merged

**Monitor checkpoint live (in another terminal):**

```bash
watch -n 2 type playwright_agent\evidence\run_*\vscode_contributors\checkpoint.json
```

### Test 2: PR Audit Chain (harder, multi-page)

```bash
cd playwright_agent
python main.py --task tasks/github_pr_audit_chain.json \
  --input tasks/inputs/github_pr_chain.csv --no-headless
```

**What happens:** Agent navigates merged PR list → clicks into each PR → extracts title/author/reviewers/CI status → clicks "Files changed" tab → extracts file count → screenshots → checkpoints → navigates back → repeats for 3-5 PRs. Includes judgment at the end.

### Test 3: Natural Language Long-Horizon

```bash
cd playwright_agent
python main.py --prompt "Go to the GitHub repository microsoft/vscode. \
  Visit the top 3 contributors' profiles. For each, extract their name, \
  company, and followers count. Take a screenshot of each profile. \
  Use save_progress after each contributor." --no-headless
```

**What to watch for:** Planner generates a task spec with `max_steps=40` and instructs the agent to use `save_progress`. Execution flow should mirror Test 1.

---

## What Each Prompt Looks Like (agent's perspective)

At step 16 of 40, the agent sees:

```
## Current page state
URL: https://github.com/user123
Title: user123 (John Doe)
[0] [heading] "John Doe"
[1] [text] "Software Engineer at Google"
[2] [text] "1.2k followers"
...

**Step 16 of 40** (24 remaining)

## Data collected so far (via save_progress)
{
  "contributors": [
    { "username": "torvalds", "name": "Linus Torvalds", "company": null, "followers": "293k" },
    { "username": "gvanrossum", "name": "Guido van Rossum", "company": null, "followers": "25.9k" }
  ]
}

## Earlier steps (condensed)
Steps 1-10:
FOUND: Extracted name, company, followers for torvalds and gvanrossum. Screenshots taken.
GAPS: 1 of 3 contributors still not visited. user123 profile not started.
NEXT: Navigate to user123's profile and extract the same fields.

## Run state
Pages visited: github.com/microsoft/vscode/graphs/contributors, github.com/torvalds, github.com/gvanrossum, github.com/user123
Screenshots taken: contributors_list, profile_torvalds, profile_gvanrossum
Exhausted pages (all data taken): github.com/torvalds, github.com/gvanrossum

## Action history (5 items, budget-fitted)
Step 12: goto → Navigated to https://github.com/user123
Step 13: screenshot(profile_user123) → Screenshot saved: 04_profile_user123.png
Step 14: extract(1) → Extracted 45 chars
Step 15: extract(2) → Extracted 12 chars
Step 16: save_progress → Progress saved (2/3 items). 24 steps remaining.

## Goal
Audit the top 3 contributors: visit each profile, extract details...

## Output schema
{ "contributors": "array", "total_audited": "number", "repo_name": "string" }

Take the single best next action.
```

---

---

### Part 5 — Research-Backed Memory Architecture

**Files changed:** `agent_loop.py`, `memory.py`, `config.py`

**Research basis:**
- [CoALA](https://openreview.net/forum?id=1i6ZCvflQJ) (Princeton, TMLR 2024): modular memory — working, episodic, procedural
- [Lost in the Middle](http://export.arxiv.org/abs/2307.03172) (Stanford, 2023): keep prompt fill below 20% of context
- [ReSum](https://arxiv.org/abs/2509.13313) (Alibaba, 2025): structured goal-oriented summaries for indefinite exploration
- [BrowserUse + Mem0](https://mem0.ai/blog): procedural memory snapshots for 98% task completion, 41% cost reduction

---

#### Upgrade 1: Structured Run State

The `progress` dict now tracks failures and dead ends, not just successes:

```python
progress = {
    "pages_visited": [],         # URLs successfully loaded
    "fields_found": [],          # data snippets extracted
    "artifacts": [],             # screenshots taken
    "failed_urls": [],           # URLs that errored (404, timeout, auth)
    "exhausted_pages": [],       # pages where data was already extracted
    "blocked_selectors": [],     # selectors that failed 2+ times
    "dead_ends": [],             # actions repeated 3+ times with no progress
}
```

This state is injected into every prompt under "## Run state" so the agent knows what to skip:

```
## Run state
Pages visited: github.com/torvalds, github.com/gvanrossum
FAILED URLs (skip these): github.com/deleted-user-404
BROKEN selectors (don't retry): div.old-layout-sidebar
DEAD ENDS (tried, didn't work): click on github.com/microsoft/vscode/graphs
Exhausted pages (all data taken): github.com/torvalds
```

Selector failure tracking: after a selector fails 2 times, it's flagged as "blocked" so the agent stops retrying it.

Page exhaustion: when `save_progress` successfully extracts new data from a page, that URL is marked as exhausted.

---

#### Upgrade 2: Episodic Failure Memory

`MemoryStore` now stores two kinds of memories:

| Type | File | Learned from | Contains |
|---|---|---|---|
| Procedural patterns | `memory/patterns.json` | `done` runs | action sequences, tips, things to avoid |
| Episodic warnings | `memory/failures.json` | `failed` / `partial_success` runs | dead URLs, broken selectors, dead ends, failure reason |

**Before:** Only successful runs were remembered. Every new run on the same domain would hit the same dead ends again.

**After:** Failure signals are stored and injected into future prompts:

```
## Known issues on github.com (from past failures)
Dead URLs (skip): github.com/deleted-user-404
Broken selectors: div.old-layout-sidebar
Previous failure: Expected 5 items but collected 3
```

`learn_failures()` is called from:
- Smart termination (timeout, network circuit breaker)
- Agent calling `fail()`
- `done` with `partial_success` status

---

#### Upgrade 3: Structured Summary Schema (ReSum-inspired)

**Before:** Step summaries were generic prose:
```
Steps 1-10: Navigated to contributors page, clicked into torvalds profile.
Extracted name and followers. Took screenshot.
```

**After:** Summaries follow a structured FOUND/GAPS/NEXT format:
```
Steps 1-10:
FOUND: Extracted name, company, followers for torvalds. Screenshot taken.
GAPS: 2 of 3 contributors still not visited. No bio data collected yet.
NEXT: Navigate back to contributors list and click the next profile.
```

This is directly inspired by ReSum (Alibaba, 2025) which showed that structured summaries with "verified evidence + information gaps + next-step directions" outperform prose summaries by 4.5% on long-horizon web tasks.

---

#### Upgrade 4: Token Budget Formula

```python
PROMPT_TOKEN_BUDGET = min(24_000, max(8_000, int(LLM_CONTEXT_WINDOW * 0.08)))
```

- **8% of context window**: Stays well below the "lost in the middle" degradation zone (~20% fill)
- **Floor 8K**: Even a small model gets a usable budget
- **Cap 24K**: A 1M-context model doesn't waste 80K of prompt on a single browser step
- **Overridable**: Set `LLM_CONTEXT_WINDOW` in `.env` for non-standard models

| Model | Context | Budget | History (30%) |
|---|---|---|---|
| Claude Sonnet (200K) | 200K | 16,000 | 4,800 |
| Claude Haiku (200K) | 200K | 16,000 | 4,800 |
| Future 1M model | 1M | 24,000 (capped) | 7,200 |
| Small 32K model | 32K | 8,000 (floor) | 2,400 |

---

## Architecture Summary

```
Before long-horizon:
  history = last 5 actions (everything else forgotten)
  termination = max_steps only
  output = all-or-nothing (done or failed)

After long-horizon:
  memory:
    working   = dynamic budget-fitted window (5-25 items, importance-scored)
    summaries = structured FOUND/GAPS/NEXT every 10 steps (Haiku, <$0.001)
    run state = pages visited, failed URLs, blocked selectors, dead ends, exhausted pages
    procedural = domain-keyed patterns from successful runs (patterns.json)
    episodic  = failure warnings from failed runs (failures.json)
  termination = 8 conditions checked every step
  output = done | partial_success | failed | needs_review
  checkpoint = live file updated every 5 steps
  pagination = auto-detected, bonus steps granted
  watchdog = stall detection with nudge injection
  budget = agent sees "Step X of Y (N remaining)"
  token budget = min(24K, max(8K, context_window * 8%))
```
