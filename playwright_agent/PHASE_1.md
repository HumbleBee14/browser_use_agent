# Phase 1: Tools + Models — Foundation

**Status:** Complete
**Files created:** 9

---

## What Was Built

### Models (data contracts)

| File | What | Key Types |
|------|------|-----------|
| `models/task.py` | Task spec + sample input | `TaskSpec`, `SampleInput`, `load_task_spec()` |
| `models/actions.py` | Agent actions + evidence | `AgentAction` (9 actions), `ActionResult`, `StepRecord`, `EvidenceArtifact`, `SampleResult`, `action_tool_schema()` |

### Tools (browser + output)

| File | What | Key Functions |
|------|------|---------------|
| `tools/browser.py` | Playwright wrappers | `goto`, `click`, `type_text`, `scroll`, `wait_for`, `take_screenshot`, `extract_text`, `get_page_info` |
| `tools/output.py` | Evidence file I/O | `OutputManager` (per-sample), `merge_results_to_csv` |

### Config + Project Setup

| File | What |
|------|------|
| `config.py` | Environment config via python-dotenv |
| `requirements.txt` | 8 dependencies (playwright, anthropic, pydantic, etc.) |
| `.env.example` | Template with all config vars documented |
| `.gitignore` | Ignores evidence/, .env, auth state, __pycache__ |
| `tasks/_template.json` | Template task spec with all fields |

---

## Design Decisions

### AgentAction — 9 typed actions, no free-form prose
Claude returns exactly one of: `goto`, `click`, `type`, `scroll`, `screenshot`, `extract`, `wait`, `done`, `fail`. Enforced via `tool_choice={"type":"any"}`. The `action_tool_schema()` function generates the Anthropic tool_use format.

### Element resolution — 3 strategies
`click`, `type_text`, `extract_text` all try: index-based → text-based → CSS selector. Index-based uses the DOM extractor's element map (built in Phase 2). Text-based uses `page.get_by_text()`. CSS selector is last resort.

### OutputManager — per-sample isolation
One instance per sample. Manages screenshot counter, artifact list, action log. Writes `result.json` + `action_log.json` atomically at task end. SHA-256 computed at write time.

### Rate limiting — per-domain
LinkedIn: 3s, GitHub: 0.5s, default: 0.2s. Applied before every `goto()`.

### combined.csv — single merge at batch end
No concurrent writes. `main.py` reads all `result.json` files after workers finish, sorts by sample_id, writes one CSV. Deterministic.

---

## Test Results

```
TaskSpec: unique_name | phase=execution | max_steps=25
SampleInput: test_001 | url=https://github.com/torvalds | extra={'name': 'Linus'}
Tool schema: 9 tools: ['goto', 'click', 'type', 'scroll', 'screenshot', 'extract', 'wait', 'done', 'fail']
Config: model=claude-sonnet-4-6 | evidence=.../playwright_agent/evidence
OutputManager: saved 01_test_page.png | sha256=93dd79311f0abf48...
Result written: done | test_sample | artifacts=1
All output files verified on disk

=== ALL PHASE 1 TESTS PASSED ===
```

---

## How to Test

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
"
```

---

## What's Next (Phase 2)

- `core/dom_extractor.py` — a11y tree snapshot + 4-pass pruning + dom_confidence
- `core/vision.py` — screenshot → Claude multimodal analysis
- Test against a real GitHub page
