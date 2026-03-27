# Browser Evidence Agent

Automated browser-based evidence collection agent. Given a task definition and a set of sample URLs, it autonomously navigates websites, takes screenshots, extracts structured data, makes audit judgments, and packages everything into per-sample evidence folders with a consolidated CSV.

Built on [browser-use](https://github.com/browser-use/browser-use) for browser automation and Claude for reasoning.

## Setup

```bash
# 1. Clone and enter the project
cd browser_use_agent

# 2. Create virtual environment
python -m venv .venv

# 3. Activate it
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Install Playwright browsers (required by browser-use)
playwright install chromium

# 6. Create your .env file
cp .env.example .env
# Then edit .env and add your ANTHROPIC_API_KEY
```

## Run

```bash
# Dry run — validate task config and list samples instantly (no browser, no LLM, $0 cost)
python main.py --task tasks/github_commits.yaml --dry-run

# Single sample (quick test)
python main.py --task tasks/demo.yaml --sample-id test_001 --url https://github.com/browser-use/browser-use

# Batch from CSV
python main.py --task tasks/github_commits.yaml

# With options
python main.py --task tasks/demo.yaml --headless --max-concurrent 5 --verbose
```

## Output

Each run creates a timestamped folder under `evidence/`:

```
evidence/run_2026-03-27_153000/
  samples/
    test_001/
      01_main_page.png        # Evidence screenshots
      result.json              # Structured result with provenance
      action_log.json          # What the agent did step-by-step
  results.csv                  # Master CSV with all extracted fields
  run_summary.json             # Pass/fail stats
```

## Adding New Tasks

Create a YAML file in `tasks/`. No code changes needed for tasks within existing strategy families (`single_page`, `graph_traversal`, `form_fill`). See `tasks/demo.yaml` for a minimal example.
