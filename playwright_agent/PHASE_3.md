# Phase 3: Agent Loop — The Brain

**Status:** Complete
**Files created:** 3 (`agent_loop.py`, `run_single.py`, `tasks/github_profile.json`)
**Tests:** End-to-end on 2 real GitHub profiles

---

## What Was Built

### `agent_loop.py` — The Entire Agent

The core ReAct cycle: OBSERVE → DECIDE → ACT → CHECK → repeat.

**Key implementation details:**
- `AsyncAnthropic` client — non-blocking, concurrency-safe
- `tool_choice={"type": "any"}` on every LLM call — Claude always returns a typed tool call
- Rolling 5-action history — token cost stays flat regardless of run length
- System prompt is static (from task_spec) — eligible for Anthropic prompt caching
- Machine-checkable `done`: if required_fields are missing, bounces back to agent
- Loop detection: `(url, action)` counter ≥ 3 → inject nudge
- Consecutive failure recovery: 3+ failures → inject visible element list
- `max_steps` hard ceiling → auto-fail with descriptive error

### `run_single.py` — Quick Test Runner

CLI tool to run the agent on a single sample:
```bash
python run_single.py --task tasks/github_profile.json --url https://github.com/torvalds --id torvalds
```

### `tasks/github_profile.json` — First Real Task Spec

Extracts: display_name, bio, company, location, followers, following, pinned_repos.

---

## Test Results

### Linus Torvalds (github.com/torvalds)
```
Result: done
Steps:     6
Artifacts: 3 screenshots (SHA-256 hashed)
Extracted:
  display_name: Linus Torvalds
  followers: 293k
  following: 0
  pinned_repos: ['linux', 'GuitarPedal']
```

### Guido van Rossum (github.com/gvanrossum)
```
Result: done
Steps:     9
Artifacts: 4 screenshots
Extracted:
  display_name: Guido van Rossum
  followers: 25.9k
  following: 5
  pinned_repos: ['microsoft/typeagent-py']
```

### Evidence Files Per Sample
```
evidence/torvalds/
  01_profile.png       240KB (SHA-256: 1c48e8a7...)
  02_profile.png       240KB
  03_profile.png       240KB
  action_log.json      1.9KB (6 steps with thinking + actions + results)
  result.json          1.2KB (extracted fields + artifact manifest)
```

---

## How to Run

```bash
cd playwright_agent

# GitHub profile extraction
python run_single.py --task tasks/github_profile.json --url https://github.com/torvalds --id torvalds

# Any URL with any task spec
python run_single.py --task tasks/your_task.json --url https://example.com --id sample_001
```

---

## Architecture Validated

This phase proves the full stack works:

```
run_single.py
  → agent_loop.py (ReAct cycle)
    → core/dom_extractor.py (a11y tree → pruned text)
    → core/vision.py (screenshot when dom_confidence < 0.6)
    → tools/browser.py (goto, click, scroll, screenshot, extract)
    → tools/output.py (save screenshots, write result.json + action_log.json)
  → models/actions.py (AgentAction, tool schema for Claude)
  → models/task.py (TaskSpec from JSON)
```

All site-specific knowledge is in `tasks/github_profile.json`. Zero hardcoded selectors. The agent reads the live a11y tree and reasons about what it sees.

---

## What's Next (Phase 4)

- `main.py` — orchestrator: load samples, run workers in parallel, merge CSV
- `worker.py` — isolated BrowserContext per sample
- `discover.py` — paginate a start URL to build samples.csv
- Test: batch run 5 profiles in parallel → combined.csv
