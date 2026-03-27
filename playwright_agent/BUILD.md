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
└── evidence/            # Output — generated at runtime
    └── {sample_id}/
        ├── 01_{label}.png
        ├── result.json
        └── action_log.json
```

## Running Tests

### Phase 1 — Models + Tools
```bash
cd playwright_agent
python -c "
import sys; sys.path.insert(0, '.')
from models.task import load_task_spec
from models.actions import action_tool_schema
from tools.output import OutputManager
import tempfile, pathlib

spec = load_task_spec('tasks/_template.json')
print(f'TaskSpec OK: {spec.task_id}')

tools = action_tool_schema()
print(f'Tool schema OK: {len(tools)} tools')

with tempfile.TemporaryDirectory() as tmp:
    om = OutputManager(pathlib.Path(tmp), 'test')
    om.save_screenshot(b'test', 'page', 'https://example.com')
    om.write_result(status='done', extracted={'field': 'value'}, steps=1)
    print('OutputManager OK')

print('Phase 1 PASSED')
"
```

## Adding a New Task

1. Copy `tasks/_template.json` to `tasks/your_task.json`
2. Fill in: `task_id`, `system_prompt`, `goal`, `keywords`, `output_schema`
3. Run: `python main.py --task tasks/your_task.json --input samples.csv`

No Python code changes needed.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design document.

## Phase Docs

- [PHASE_1.md](PHASE_1.md) — Tools + Models foundation
