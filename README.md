# Browser Evidence Agent

> AI-powered browser automation for audit-grade evidence collection. Navigate any website, extract structured data, take evidence screenshots, make audit judgments — all from a YAML task definition.

Built on [browser-use](https://github.com/browser-use/browser-use) for browser automation and Claude for reasoning.

---

## Architecture

```
YAML Task Definition → Strategy Selection → Browser Agent → Evidence Output
     (what)              (how)              (navigate)       (package)
```

**Core idea:** The LLM decides WHERE to navigate and WHAT to extract. Pure Python decides HOW to name files, compute hashes, write CSVs, and validate results. This split matters for audit work where consistency beats cleverness.

```
┌──────────────────────────────────────────────────────────┐
│                      CLI (main.py)                        │
│  --task YAML  --dry-run  --headless  --max-concurrent    │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│                   Orchestrator                            │
│  Concurrent sample processing, fault isolation, CSV/JSON  │
└────────────────────────┬─────────────────────────────────┘
                         │ (one per sample, async)
┌────────────────────────▼─────────────────────────────────┐
│                   EvidenceAgent                           │
│  browser-use Agent + custom actions + strategy prompt     │
└──────────┬─────────────────────────────┬─────────────────┘
           │                             │
┌──────────▼──────────┐    ┌─────────────▼─────────────────┐
│   Custom Actions     │    │      Output (Deterministic)    │
│  screenshot_evidence │    │  FileManager: folders, SHA-256 │
│  record_fields       │    │  CSVWriter: master results     │
│  mark_checkpoint     │    │  result.json: full provenance  │
│  make_judgment       │    │  action_log.json: agent steps  │
│  download_file       │    │  run_summary.json: stats       │
└──────────────────────┘    └────────────────────────────────┘
```

### Strategy Pattern — 3 Strategies Cover All Task Types

| Strategy | Navigation Pattern | Example Tasks |
|----------|-------------------|---------------|
| `single_page` | Visit one URL, extract, screenshot | Issue extraction, page capture |
| `graph_traversal` | Follow links across pages, collect at each node | Commit → PR → review audit |
| `form_fill` | Fill forms, submit, capture results | Audit form submission, data entry |

**New task in existing family = YAML file only. New task family = one strategy class (~50 LOC) + YAML.**

### Project Structure

```
browser_use_agent/
├── agent/
│   ├── evidence_agent.py    # Core agent wrapper (one per sample)
│   ├── orchestrator.py      # Batch processing with concurrency
│   ├── actions.py           # 5 custom browser actions
│   ├── llm.py               # Provider-agnostic LLM factory
│   └── task_loader.py       # YAML + CSV loading
├── models/
│   ├── task.py              # TaskConfig, FieldSpec, Checkpoint
│   ├── evidence.py          # SampleResult, FieldExtraction, provenance
│   └── judgment.py          # Structured audit judgments
├── strategies/
│   ├── base.py              # BaseTaskStrategy ABC + validation
│   ├── single_page.py       # Visit → extract → screenshot
│   ├── graph_traversal.py   # Multi-page link following
│   └── form_fill.py         # Form interaction + submission
├── output/
│   ├── file_manager.py      # Per-sample folders, SHA-256 hashing
│   └── csv_writer.py        # Deterministic batch CSV output
├── tasks/                   # Task definitions (YAML + input CSVs)
├── config.py                # Environment-based configuration
└── main.py                  # CLI entry point
```

---

## Setup

```bash
# 1. Clone and enter the project
cd browser_use_agent

# 2. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers
playwright install chromium

# 5. Create your .env file
cp .env.example .env
# Edit .env → set ANTHROPIC_API_KEY (or OPENAI_API_KEY with LLM_PROVIDER=openai)
```

---

## Usage

### Dry Run — Validate config instantly (no browser, no LLM, $0)
```bash
python main.py --task tasks/github_commits.yaml --dry-run
python main.py --task tasks/form_fill_demo.yaml --dry-run
```

### Single Sample
```bash
python main.py --task tasks/github_issues.yaml \
  --sample-id issue_001 --url "https://github.com/microsoft/vscode/issues/305609"
```

### Batch from CSV
```bash
python main.py --task tasks/github_commits.yaml --max-concurrent 2
```

### All Options
```bash
python main.py --task YAML_PATH \
  [--sample-id ID --url URL]   # Single sample mode
  [--dry-run]                   # Validate without running
  [--headless]                  # No visible browser
  [--max-concurrent N]          # Parallel samples (default: 3)
  [--output DIR]                # Custom output directory
  [--verbose]                   # Debug logging
```

---

## Output

Each run creates a timestamped folder under `evidence/`:

```
evidence/run_2026-03-27_033157/
├── results.csv                  # Master CSV — all samples, typed columns
├── run_summary.json             # Stats: pass rates, error breakdown, durations
└── samples/
    └── commit_001/
        ├── 01_commit_page.png   # Evidence screenshot (SHA-256 hashed)
        ├── 02_pr_review.png     # PR review screenshot
        ├── result.json          # Full provenance: every field → source URL + artifact
        └── action_log.json      # Step-by-step agent actions
```

### Provenance in result.json

Every extracted field traces back to its source:
```json
{
  "field_name": "author",
  "value": "benibenj",
  "source_url": "https://github.com/microsoft/vscode/commit/9986a43...",
  "source_selector": "commit page",
  "artifact_ref": "01_commit_page.png"
}
```

---

## Available Tasks

| Task | Strategy | What It Does |
|------|----------|-------------|
| `tasks/github_commits.yaml` | graph_traversal | Audit commits: navigate commit → PR → review, extract fields, judge review compliance |
| `tasks/github_issues.yaml` | single_page | Extract issue details: number, title, state, author, labels, dates |
| `tasks/form_fill_demo.yaml` | form_fill | Fill forms with CSV data, submit, capture before/after screenshots |
| `tasks/demo.yaml` | single_page | Simple page capture: screenshot + title extraction |

---

## Adding New Tasks

**New task in existing family** — just create a YAML file:

```yaml
name: my_new_task
strategy: single_page           # or graph_traversal, form_fill
instructions: |
  1. Navigate to the URL.
  2. Extract the page title using record_fields.
  3. Take a screenshot using screenshot_evidence.
input_file: "inputs/my_data.csv"
input_columns: ["sample_id", "url"]
output_fields:
  - name: page_title
    type: str
    required: true
checkpoints:
  - name: page_captured
    evidence_type: screenshot
    required: true
max_steps: 10
timeout_seconds: 60
```

**New task family** — add a strategy class (~50 lines) in `strategies/`, register in `__init__.py`.

---

## LLM Provider Support

Switch providers by changing `.env` — no code changes:

| Provider | `.env` Setting | Default Model |
|----------|---------------|---------------|
| Anthropic | `LLM_PROVIDER=anthropic` | claude-sonnet-4-6 |
| OpenAI | `LLM_PROVIDER=openai` | gpt-5.4 |
| Gemini | `LLM_PROVIDER=gemini` | gemini-3.1-pro-preview |
