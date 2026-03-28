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

---

## Agent Loop — Step-by-Step Explanation

The agent loop (`agent_loop.py`) is the brain. Every sample goes through this cycle. Here's exactly what happens at each stage, what data flows through, and how the agent self-corrects.

### Overview

```
for each step (up to max_steps):
    1. OBSERVE   → read the page, build compact DOM text
    2. DECIDE    → send DOM + history + goal to Claude, get one action back
    3. ACT       → execute the action (click, screenshot, etc.)
    4. CHECK     → if "done": validate fields/artifacts. if "fail": stop.
    5. TRACK     → detect loops and spam, update failure counters
```

### Step 1: OBSERVE — Read the Page

The DOM extractor reads the page's accessibility tree and produces a numbered list:

```
URL: https://github.com/torvalds
Title: torvalds (Linus Torvalds)

[0]  [text]     "231k followers · 0 following"
[1]  [heading]  "Linus Torvalds"  (h1)
[2]  [link]     "linux" → https://github.com/torvalds/linux
[3]  [button]   "Follow"
```

This goes through three pruning passes:
- **Pass 1:** Skip entire `navigation`, `banner`, `contentinfo` blocks (headers, navs, footers — gone with all their children)
- **Pass 2:** Keep only semantic roles (`heading`, `link`, `button`, `text`, `tab`, etc). Drop structural wrappers (`generic`, `group`, `separator`)
- **Pass 3:** Keyword boost — nodes matching task keywords (e.g., "followers", "bio") go first, rest fill a budget

A **confidence score** is computed. If below 0.6 (SVG-heavy or canvas pages), vision activates — takes a screenshot and asks Claude a targeted question about what the DOM couldn't see.

### Step 2: DECIDE — Ask Claude

A single user message is built fresh each turn with these sections:

| Section | What it contains | Purpose |
|---------|-----------------|---------|
| Current page state | The pruned DOM text | What's on screen right now |
| Visual analysis | Vision answer (if activated) | What SVG/canvas elements look like |
| Actions taken so far | Last 5 actions + results | Short-term memory (prevents repeating) |
| Loop detection notice | Warning if same action 3+ times | Unstick the agent |
| Recovery elements | Interactive element list (on 3+ failures) | Give the agent something to try |
| Spam detection notice | Warning if same action type 3× consecutively | Stop screenshot/scroll spam |
| Sample info | sample_id, url, extra fields | What we're working on |
| Goal | Natural language goal from task spec | What to accomplish |
| Output schema | JSON field definitions | What to put in `done()` |
| Required fields | Field names that must be non-empty | Hard requirements |

The system prompt is static across all steps (prompt-cached to save tokens). Claude must return one of 9 typed actions via `tool_choice={"type": "any"}` — it cannot respond with prose.

### Step 3: ACT — Execute the Action

Claude's response is parsed into an `AgentAction` and dispatched:

| Action | What happens | Returns |
|--------|-------------|---------|
| `goto(url)` | Navigate to URL, wait for page load | success/timeout |
| `click(selector)` | Resolve element (index → text → CSS), click it | success/not found |
| `type(selector, text)` | Find input, clear it, type text | success/not found |
| `scroll(direction)` | Scroll up or down by 600px | always succeeds |
| `screenshot(label)` | Full-page screenshot, SHA-256 hash, save to evidence | filename + hash |
| `extract(selector)` | Read text content of an element into history | extracted text |
| `wait(selector)` | Wait up to 10s for element to appear | success/timeout |
| `done(extracted)` | Signal completion with extracted data | triggers validation |
| `fail(note)` | Signal failure with reason | terminates loop |

Every action returns an `ActionResult(success, description, error)` — never raises an exception.

### Step 4: CHECK — Validate Completion

When the agent calls `done`, the loop doesn't just accept it. It machine-checks:

1. **Required fields** — every field in `task_spec.required_fields` must be present and not `None` (but `0` and `False` are valid)
2. **Required artifacts** — every label in `task_spec.required_artifacts` must match a saved screenshot filename by substring

If something is missing and steps remain → **bounce back**: inject a notice into history telling the agent what's missing, and continue the loop.

If something is missing on the **last step** → write `status: "needs_review"` (not "done") so a human knows to check it.

If everything passes → write `result.json` with extracted data, artifacts manifest, and timestamps.

### Step 5: TRACK — Self-Correction

Two mechanisms prevent the agent from getting stuck:

**Loop detection** — a `(url, action_name)` counter tracks how many times the same action was taken on the same page. At 3+ repetitions, the next prompt includes:
```
[NOTICE] You have repeated 'click' on this URL 3 times without progress.
Try a different approach or call fail().
```

**Spam detection** — if the last 3 actions are the same type (e.g., `screenshot`, `screenshot`, `screenshot`), inject:
```
You have called 'screenshot' 3 times consecutively.
STOP repeating this action. Extract the fields and call done now.
```
This excludes `click` and `type` since consecutive form-filling is normal.

**Consecutive failure recovery** — after 3+ failed actions, the prompt includes a list of all visible interactive elements so the agent can try a different path.

### What Claude Never Sees

- Raw HTML — only the pruned accessibility tree
- Full history — only the last 5 actions (token cost stays flat)
- Previous turn's prompt — each turn is a fresh single-message call
- Other samples — each worker is completely isolated

### Termination Conditions

| Condition | Status written | What happens |
|-----------|---------------|-------------|
| `done` with all requirements met | `done` | Evidence saved, loop exits |
| `done` with missing fields (steps left) | — | Bounced back, loop continues |
| `done` with missing fields (last step) | `needs_review` | Partial result saved |
| `fail` called | `failed` | Reason logged, loop exits |
| `max_steps` exhausted | `failed` | "Exhausted N steps" error |
| LLM error (API down, timeout) | `failed` | Error logged, loop exits |
