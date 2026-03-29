# Browser Workflow Agent

A site-agnostic autonomous browser agent for evidence collection and structured browser workflows. It navigates websites, extracts structured data, takes reviewable screenshots, and produces audit-ready outputs — driven by a custom ReAct loop with Claude and zero hardcoded site logic.

**No frameworks. No LangChain. No browser-use library.** Pure Playwright + Anthropic SDK + a hand-built agent loop.

---

## What It Does

Give it a plain English prompt or a JSON task spec. It opens a browser, reads the live accessibility tree, decides what to do, and does it — clicking, typing, scrolling, extracting, screenshotting — until the job is done.

```bash
python main.py --prompt "Go to torvalds GitHub profile and extract display name, bio, followers, pinned repos"
```

```
evidence/run_20260327_140000/
└── torvalds/
    ├── 01_profile.png       (SHA-256 hashed)
    ├── result.json          (structured extracted data)
    ├── action_log.json      (every step the agent took)
    └── checkpoint.json      (live progress tracking)
```

It works across a **broad class of accessible DOM-first websites** — GitHub, LinkedIn, Jira, Workday, Hacker News, internal tools — without writing site-specific Python code. All site knowledge lives in a JSON task spec.

---

## Key Capabilities

| Capability | Description |
|-----------|-------------|
| **Natural language input** | `--prompt "Go to X and extract Y"` — Claude converts to a task spec and decides whether discovery is needed |
| **Parallel execution** | N concurrent browser contexts, each isolated with own cookies/session |
| **Long-horizon tasks** | 30-50+ step multi-page workflows with incremental checkpointing |
| **Evidence-grade output** | SHA-256 hashed screenshots, structured JSON, full action audit trail |
| **Self-correction** | 3-level escalating recovery, stagnation detection, loop/spam prevention |
| **Run-scoped memory** | Learns navigation patterns within a run, remembers failures, and resumes cleanly without cross-run leakage |
| **Dynamic history** | Budget-fitted context window (5-25 items), importance-scored, not fixed-size |
| **Vision fallback** | When DOM can't see it (SVG icons, canvas), Claude Vision reads the screenshot |
| **Crash recovery** | Checkpoint every 5 steps + on every save. Restart picks up where it stopped |
| **Structured reflection** | Per-step self-evaluation, working memory, declared intent |

---

## Architecture

```
User Input (prompt or CSV)
    │
    ▼
┌─────────────────┐
│  TASK PLANNER   │  Claude converts plain English → task spec + sample URLs
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  ORCHESTRATOR   │  Load samples, skip completed, launch N workers
└────────┬────────┘
         │
    ┌────┼────┐
    ▼    ▼    ▼
┌──────┐┌──────┐┌──────┐
│WORKER││WORKER││WORKER│  Isolated BrowserContexts
└──┬───┘└──┬───┘└──┬───┘
   │       │       │
   ▼       ▼       ▼
┌──────────────────────────────────────────┐
│           AGENT LOOP (per sample)         │
│                                           │
│  OBSERVE → REFLECT → DECIDE → ACT → CHECK│
│                                           │
│  DOM Extractor: a11y tree → pruned text   │
│  Vision Module: screenshot → Claude Vision│
│  12 typed actions (click, type, extract, download…) │
│  Structured reflection per step           │
│  Escalating recovery on stagnation        │
│  Run-scoped memory within the current run │
└───────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│  EVIDENCE       │  result.json + screenshots + action_log + combined.csv
└─────────────────┘
```

**Modular Python layout:** the loop lives in `agent_loop.py`; prompts, recovery, merge, dispatch, and LLM retry logic sit in small sibling modules so the core file stays reviewable.

```
playwright_agent/
├── main.py              # Orchestrator
├── agent_loop.py        # The brain: ReAct cycle (orchestration)
├── agent_prompt.py      # History fitting + message builder
├── agent_recovery.py    # Termination, stagnation, final consolidation
├── agent_merge.py       # Checkpoint deep merge (id-aware)
├── agent_llm_retry.py   # Transient LLM error detection
├── agent_navigation.py  # Pagination + batch safety
├── agent_dispatch.py    # One action → Playwright
├── memory.py            # Long-term memory (patterns + failures)
├── config.py            # All settings from .env
├── task_planner.py      # Natural language → task spec
├── discover.py          # URL discovery via pagination
├── worker.py            # Isolated browser context per sample
├── log_setup.py         # Structured logging
├── core/
│   ├── dom_extractor.py # A11y tree → LLM-digestible text
│   └── vision.py        # Screenshot → Claude Vision
├── tools/
│   ├── browser.py       # Playwright wrappers + rate limiting
│   └── output.py        # Evidence packaging, SHA-256, checkpoints
└── models/
    ├── task.py          # TaskSpec (Pydantic)
    └── actions.py       # AgentAction + ActionResult + tool schema
```

---

## Quick Start

```bash
cd playwright_agent

# 1. Create and activate virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install browser
playwright install chromium

# 4. Configure
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY
```

### Run with natural language

```bash
# Single profile extraction
python main.py --prompt "Go to torvalds GitHub profile and extract display name, bio, followers, pinned repos"

# Multi-page audit
python main.py --prompt "Go to microsoft/vscode on GitHub. Visit the top 3 contributors' profiles. \
  For each, extract name, company, followers. Take a screenshot of each. Use save_progress after each."

# Any website
python main.py --prompt "Go to https://news.ycombinator.com and extract the top 5 post titles and scores"
```

### Run with task spec + input CSV

```bash
# Pre-built task with sample list
python main.py --task tasks/github_profile.json --input samples.csv

# Single URL
python run_single.py --task tasks/github_profile.json --url https://github.com/torvalds --id torvalds
```

### Watch it work

```bash
# Run with visible browser
python main.py --prompt "..." --no-headless

# Monitor checkpoint in real-time (another terminal)
watch -n 2 cat evidence/run_*/sample_id/checkpoint.json
```

---

## How the Agent Loop Works

Each sample goes through a **ReAct cycle**: Observe → Reflect → Decide → Act → Check.

### 1. Observe

The DOM extractor reads the page's accessibility tree (2000+ nodes) and prunes it to ~40 task-relevant elements:

```
[0] [heading]  "Linus Torvalds"
[1] [link]     "linux" → https://github.com/torvalds/linux
[2] [button]   "Follow"
[3] [text]     "231k followers · 0 following"
```

If the page is SVG/canvas-heavy (confidence < 0.6), Vision activates — takes a screenshot and asks Claude a targeted question about what the DOM can't see.

### 2. Reflect + Decide

Claude receives the pruned DOM, recent history, accumulated data, run state, and memory hints. Returns a typed action with structured reflection:

```json
{
  "action": "extract",
  "selector": "3",
  "evaluation_previous_step": "Screenshot saved successfully",
  "memory_update": "Profile has 231k followers, location Portland OR",
  "next_goal": "Extract follower count then call done"
}
```

### 3. Act

12 actions available, each wrapped in a 60-second timeout:

| Action | What it does |
|--------|-------------|
| `goto(url)` | Navigate to URL |
| `click(selector)` | Click element by index, text, or CSS |
| `type(selector, text)` | Fill an input field |
| `scroll(direction)` | Scroll up or down |
| `screenshot(label)` | Full-page evidence screenshot (SHA-256 hashed) |
| `extract(selector)` | Read text content into memory |
| `wait(selector)` | Wait for element to appear |
| `download(selector)` | Click a download trigger and save the file as an evidence artifact |
| `select_option(selector, value)` | Pick a value from a native `<select>` dropdown |
| `save_progress(data)` | Checkpoint partial data, keep going |
| `done(extracted)` | Task complete — write structured result |
| `fail(note)` | Unrecoverable — stop with reason |

### 4. Check

When the agent calls `done`, the loop machine-checks required fields and artifacts. Missing something? Bounced back with a notice. Everything present? `result.json` written.

---

## Long-Horizon Tasks (30-50+ steps)

Most browser agents break after 10 steps. This one is built for extended multi-page workflows:

- **Incremental checkpointing** — `save_progress` deep-merges partial data without stopping. Crash at step 40? All data from steps 1-39 is in `checkpoint.json`.
- **LLM-powered summaries** — every 10 steps, Claude Haiku compresses old history into structured FOUND/GAPS/NEXT format. Cost: < $0.001 per summary.
- **Dynamic history window** — 5-25 recent actions kept based on importance scoring and token budget (8% of context window, capped at 24K tokens). Research-backed: stays below the "Lost in the Middle" degradation zone.
- **Auto-pagination** — clicks "Next page" / "Load more"? +3 bonus steps added automatically.
- **Structured run state** — tracks pages visited, failed URLs, blocked selectors, dead ends, exhausted pages. Injected into every prompt so the agent knows what to skip.
- **Escalating stagnation detection** — same page + no new data → gentle nudge (3 steps) → forceful demand (5 steps) → forced consolidation (8 steps).
- **Budget pressure** — warnings at 75% and 90% of step budget. Final step restricts tools to `done`/`fail` only.
- **Final consolidation** — when max steps exhausted with accumulated data, one last LLM call produces best-effort structured output.

---

## Memory System

The agent learns **within a run**.

| Type | File | Learned from | Purpose |
|------|------|-------------|---------|
| **Procedural patterns** | `evidence/run_XXXX/memory/patterns.json` | Successful samples in this run | Navigation tips, efficient action sequences |
| **Episodic warnings** | `evidence/run_XXXX/memory/failures.json` | Failed/partial samples in this run | Dead URLs, broken selectors, failure reasons |

After a successful sample, Claude Haiku distills the action log into abstract navigation patterns. These are domain-keyed and task-aware within the current run — `get_hints()` ranks patterns by keyword overlap with the current goal.

Failure signals (dead URLs, broken selectors, dead-end actions) are stored and injected into later samples in the same run so the agent doesn't repeat the same mistakes.

---

## Self-Correction

Multiple layers prevent the agent from getting stuck:

| Mechanism | Trigger | Response |
|-----------|---------|----------|
| Structured reflection | Every step | Agent evaluates last action, updates working memory, declares intent |
| Stagnation L1 | 3 stagnant steps | "Try a different approach" |
| Stagnation L2 | 5 stagnant steps | "CHANGE YOUR STRATEGY NOW" + checkpoint |
| Stagnation L3 | 8 stagnant steps | "MUST call done or fail" |
| Budget 75% | 75% steps used | "Start consolidating results" |
| Budget 90% | 90% steps used | "Save/finalize NOW" |
| Spam detection | 4+ identical actions | Forced stop |
| Failure recovery | 3+ consecutive fails | Inject visible element list |
| Final consolidation | Max steps + data exists | One last LLM call for best-effort output |
| Fallback LLM | Primary model fails | Retry on secondary model (optional) |

---

## Configuration

All settings via `.env` (see `.env.example`):

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-xxxxx

# Model selection
LLM_MODEL=claude-sonnet-4-6              # primary model
LLM_FAST_MODEL=claude-haiku-4-5          # summaries & memory distillation

# Agent behavior
REFLECTION_MODE=light                     # default: lower cost; use "full" for long-horizon
ENABLE_MEMORY_DISTILLATION=true           # false = skip post-run Haiku pattern distillation
FINALIZE_ON_FAILURE=true                  # best-effort consolidation on failure
ENABLE_FALLBACK_LLM=false                 # try secondary model on primary failure
ENABLE_MULTI_ACTIONS=false                # experimental multi-action batching
```

---

## Adding a New Site

Write one JSON file. No code changes.

```json
{
  "task_id": "my_extraction",
  "system_prompt": "You are a browser agent. Extract the requested data. Set missing fields to null.",
  "goal": "Visit the profile page and extract name, title, and company.",
  "keywords": ["name", "title", "company", "profile"],
  "output_schema": {
    "name": "string",
    "title": "string | null",
    "company": "string | null"
  },
  "max_steps": 25,
  "required_fields": ["name"],
  "required_artifacts": ["profile_screenshot"]
}
```

```bash
python main.py --task tasks/my_extraction.json --url https://example.com/profile --id sample_001
```

---

## Evidence Output

Every sample produces audit-ready evidence:

```
evidence/run_20260327_140000/
├── torvalds/
│   ├── 01_profile.png           # SHA-256 hashed screenshot
│   ├── result.json              # Extracted data + artifact manifest
│   ├── action_log.json          # Full step-by-step audit trail
│   └── checkpoint.json          # Live progress (for long-horizon tasks)
├── gvanrossum/
│   └── ...
└── combined.csv                 # Merged results across all samples
```

`result.json` includes extracted fields, artifact hashes, timestamps, and status. `action_log.json` includes every action the agent took with thinking, reflection, and outcomes — fully replayable.

---

## Tests

```bash
cd playwright_agent
python -m pytest tests/ -v

# 114 tests covering:
# - Tools + models (test_phase1.py)
# - DOM extractor + vision (test_phase2.py)
# - Agent loop + reflection + recovery + batching (test_phase3.py)
# - Orchestrator + discovery (test_phase4.py)
# - Memory system (test_memory.py)
```

---

## Research Foundations

The design is informed by recent research on long-horizon web agents:

- **[CoALA](https://openreview.net/forum?id=1i6ZCvflQJ)** (Princeton, TMLR 2024) — modular memory architecture (working, episodic, procedural)
- **[Lost in the Middle](http://export.arxiv.org/abs/2307.03172)** (Stanford, 2023) — prompt fill below 20% of context window
- **[ReSum](https://arxiv.org/abs/2509.13313)** (Alibaba, 2025) — structured goal-oriented summaries for long exploration

---

## Documentation

| Document | Description |
|----------|-------------|
| [HOW_IT_WORKS.md](playwright_agent/HOW_IT_WORKS.md) | Full technical overview — every component explained |
| [ARCHITECTURE.md](playwright_agent/ARCHITECTURE.md) | System design, layer-by-layer breakdown |
| [LONG_HORIZON.md](playwright_agent/LONG_HORIZON.md) | Long-horizon task support — memory, checkpointing, recovery |
| [BUILD.md](playwright_agent/BUILD.md) | Build & run guide, environment setup, project structure |

---

## Tech Stack

| Layer | Library | Why |
|-------|---------|-----|
| Browser | `playwright` (async) | Direct control, a11y tree, screenshot, download |
| LLM | `anthropic` SDK | Tool use, structured output, prompt caching |
| Primary model | `claude-sonnet-4-6` | Fast, accurate, 1M context window |
| Fast model | `claude-haiku-4-5` | Summaries, memory distillation (< $0.001/call) |
| Schemas | `pydantic` v2 | Task spec + action schema + result validation |
| Logging | `loguru` | Per-sample structured logs (human + JSONL) |
| Progress | `rich` | Live terminal dashboard |


---

## License

MIT
