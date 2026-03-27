# Build & Run Guide

## Quick Start

```bash
cd playwright_agent

# 1. Create virtual environment
python -m venv .venv

# 2. Activate it
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers
playwright install chromium

# 5. Set up environment
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Your Anthropic API key |
| `LLM_MODEL` | No | `claude-sonnet-4-6` | Primary LLM model |
| `MAX_CONCURRENT` | No | `5` | Max parallel browser contexts |
| `HEADLESS` | No | `false` | Set `true` for CI/server |
| `AUTH_STORAGE_STATE` | No | — | Path to Playwright auth state JSON |

## Project Structure

```
playwright_agent/
├── main.py              # Orchestrator: discovery → execution → CSV merge
├── discover.py          # Phase 1: paginate → samples.csv
├── worker.py            # Phase 2: one BrowserContext per sample
├── agent_loop.py        # THE core: observe → decide → act → repeat
├── config.py            # Environment config
│
├── core/
│   ├── dom_extractor.py # A11y tree → pruned text for LLM
│   └── vision.py        # Screenshot → Claude multimodal
│
├── tools/
│   ├── browser.py       # Thin Playwright wrappers
│   └── output.py        # File I/O, SHA-256, evidence packaging
│
├── models/
│   ├── task.py          # TaskSpec + SampleInput (Pydantic)
│   └── actions.py       # AgentAction + ActionResult + tool schema
│
├── tasks/               # Task specs (JSON) — all site-specific config
│   └── _template.json
│
├── logs/                # Structured logs — one pair per run
│   ├── run_2026-03-27_140000.log
│   └── run_2026-03-27_140000.jsonl
│
└── evidence/            # Output — generated at runtime
    └── run_2026-03-27_140000/
        └── {sample_id}/
            ├── 01_{label}.png
            ├── result.json
            └── action_log.json
```

## Running Tests

```bash
cd playwright_agent

# Run all regression tests
python tests/test_phase1.py

# Or with pytest (if installed)
python -m pytest tests/ -v
```

## Adding a New Task

1. Copy `tasks/_template.json` to `tasks/your_task.json`
2. Fill in: `task_id`, `system_prompt`, `goal`, `keywords`, `output_schema`
3. Run: `python main.py --task tasks/your_task.json --input samples.csv`

No Python code changes needed.

## Logs

Every run produces two log files in `logs/`, named after the run timestamp:

| File | Format | Purpose |
|------|--------|---------|
| `run_YYYY-MM-DD_HHMMSS.log` | Human-readable text | Quick debugging, `grep`-friendly |
| `run_YYYY-MM-DD_HHMMSS.jsonl` | JSON lines | Machine parsing, analysis, replay |

Log lines are tagged with a `sample_id` column so parallel agents are distinguishable:

```
14:00:01.500 | INFO     |             torvalds | Worker started
14:00:01.501 | INFO     |            gvanrossum | Worker started
14:00:02.100 | INFO     |             torvalds | Step 1 | goto → OK: Navigated to ...
14:00:03.200 | INFO     |            gvanrossum | Step 1 | goto → OK: Navigated to ...
14:00:04.800 | INFO     |             torvalds | Completed | status=done | steps=3 | fields=6
14:00:07.000 | INFO     |               system | Batch complete | samples=3 | duration=5.5s
```

**Common log queries:**

```bash
# View a specific agent's full trace
grep "torvalds" logs/run_2026-03-27_140000.log

# View only errors and warnings across all agents
grep -E "ERROR|WARNING" logs/run_2026-03-27_140000.log

# View system-level events (batch start/end, init)
grep "system" logs/run_2026-03-27_140000.log

# View discovery phase logs
grep "discovery" logs/run_2026-03-27_140000.log

# Parse structured JSON logs (e.g., filter by sample with jq)
jq 'select(.record.extra.sample_id == "torvalds")' logs/run_2026-03-27_140000.jsonl
```

Console output (via `rich`) is unaffected — logs are file-only and never duplicate to stdout.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design document.

## Phase Docs

- [PHASE_1.md](PHASE_1.md) — Tools + Models foundation
- [PHASE_2.md](PHASE_2.md) — DOM Extractor + Vision module
- [PHASE_3.md](PHASE_3.md) — Agent Loop (the brain)
- [PHASE_4.md](PHASE_4.md) — Orchestrator + Workers + Discovery
