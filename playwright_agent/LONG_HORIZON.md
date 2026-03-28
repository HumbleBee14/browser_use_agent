# Long-Horizon Task Support

**Branch:** `long-horizon-test`

---

## The Problem

Standard tasks (profile extraction, single-page audit) complete in 2-10 steps. Long-horizon tasks — multi-page audits, cross-link navigation chains, paginated data collection — need 30-50+ steps.

Before these changes, the agent had:

- Hard `max_steps` ceiling (only termination)
- Rolling 5-action history (forgets everything older)
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
  "accumulated_data": { "prs": [{ "title": "Fix editor", "author": "alice" }, ...] },
  "progress_notes": ["PR #1 done", "PR #2 done"],
  "artifacts_so_far": [{ "filename": "01_pr_overview.png", "sha256": "..." }],
  "updated_at": "2026-03-27T18:30:00Z"
}
```

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

This replaces the raw step list for old history. The agent sees:

- LLM summaries of earlier work (long-term memory)
- Last 5 raw actions (recent context)
- Full accumulated data (what was collected)

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

If 5 consecutive steps produce no new data (no `save_progress`, `extract`, or `screenshot`), the watchdog injects:

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
Steps 1-10: Navigated to contributors page, clicked into torvalds profile.
Extracted name and followers. Took screenshot. Saved progress. Navigated back.

## Progress so far
Pages visited: github.com/microsoft/vscode/graphs/contributors, github.com/torvalds, github.com/gvanrossum, github.com/user123
Screenshots taken: contributors_list, profile_torvalds, profile_gvanrossum

## Recent actions (last 5)
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

## Architecture Summary

```
Before long-horizon:
  history = last 5 actions (everything else forgotten)
  termination = max_steps only
  output = all-or-nothing (done or failed)

After long-horizon:
  history = last 5 actions + LLM summaries + accumulated data + progress tracker
  termination = 8 conditions checked every step
  output = done | partial_success | failed | needs_review
  checkpoint = live file updated every 5 steps
  pagination = auto-detected, bonus steps granted
  watchdog = stall detection with nudge injection
  budget = agent sees "Step X of Y (N remaining)"
```
