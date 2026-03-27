# Browser Agent Study Guide

This document is the fastest way to understand what this project is, what the underlying browser agent does, how the code is structured, how to run it, what it is good at, and how to explain it clearly in a presentation.

It is written for a human reader, not for the runtime.

---

## 1. One-Sentence Summary

This project is a **general browser evidence agent**: given a YAML task definition and a set of sample URLs or inputs, it opens websites, navigates through them, extracts structured data, captures screenshots, optionally downloads artifacts, makes audit-style judgments, and saves everything in a consistent evidence folder structure.

---

## 2. The Two Layers You Need to Distinguish

There are really **two things** here:

### A. The `browser-use` library

This is the underlying browser automation + LLM agent framework.

It gives us:
- an `Agent` that can reason step by step in a browser
- browser sessions and profiles
- built-in browser actions like navigation, clicking, typing, scrolling, extraction
- provider-specific chat model classes
- Playwright-backed browser control under the hood

In this repo, we use `browser-use` as the lower-level browser agent runtime.

### B. Our project on top of `browser-use`

This repo adds the audit/evidence system that `browser-use` does not give us out of the box:
- task definitions in YAML
- typed schemas for output fields
- evidence checkpoints
- structured judgments
- deterministic file naming
- SHA-256 hashing for artifacts
- CSV packaging
- per-sample manifests
- batch orchestration

Short version:

> `browser-use` gives the browser brain and hands.  
> This repo gives the audit workflow, structure, outputs, and repeatability.

---

## 3. What Problem This Project Solves

The Andera-style problem is:

- auditors get access to client systems
- they manually open links
- they inspect records
- they take screenshots
- they copy values into spreadsheets
- they download supporting files
- they document judgments about whether something was reviewed, changed, approved, or complete

This project is meant to automate that style of work in a **system-agnostic browser-first way**.

Examples:
- GitHub commit review audit
- GitHub issue extraction
- ticket screenshot collection
- form fill and report generation
- cross-page evidence gathering
- future LinkedIn enrichment or blame/materiality tasks

---

## 4. Core Design Idea

The most important architectural idea in this repo is:

> **Agentic navigation, deterministic packaging**

That means:

### The LLM / browser agent decides:
- where to go next
- what page matters
- what to click
- what to extract
- how to interpret the page
- what judgment to make

### Pure Python decides:
- how files are named
- where they are stored
- how hashes are computed
- how CSV columns are ordered
- how results are validated
- how evidence is packaged

This split is the reason the project is credible for audit work.  
You want flexible reasoning during navigation, but consistent outputs after collection.

---

## 5. High-Level Architecture

The runtime flow is:

1. `main.py` parses CLI arguments
2. task YAML is loaded into `TaskConfig`
3. sample CSV is loaded into `SampleInput` rows
4. the correct strategy is selected
5. `BatchOrchestrator` processes samples concurrently
6. one `EvidenceAgent` is created per sample
7. `EvidenceAgent` creates a `browser-use` `Agent`
8. the agent runs in the browser using built-in actions + our custom actions
9. results are validated
10. artifacts and manifests are written to `evidence/run_TIMESTAMP/...`

Main code modules:

- [main.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/main.py)
- [agent/evidence_agent.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/evidence_agent.py)
- [agent/orchestrator.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/orchestrator.py)
- [agent/actions.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/actions.py)
- [agent/task_loader.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/task_loader.py)
- [agent/llm.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/llm.py)
- [strategies/base.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/strategies/base.py)
- [output/file_manager.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/output/file_manager.py)
- [output/csv_writer.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/output/csv_writer.py)

---

## 6. What the Underlying Browser Agent Can Do

At a practical level, the underlying browser agent can:

- open URLs
- navigate through websites
- click elements
- type into fields
- scroll
- inspect page content
- extract structured information from the page
- follow links across pages
- operate over multiple steps with memory of what happened so far

In this repo, we then extend it with custom evidence actions:

- `screenshot_evidence`
- `record_fields`
- `mark_checkpoint`
- `make_judgment`
- `download_file`

These are defined in [actions.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/actions.py).

These custom actions are the bridge from "general browser agent" to "audit evidence agent".

---

## 7. What Our Custom Actions Actually Mean

### `screenshot_evidence`

Takes a named screenshot of the current page and saves it as a deterministic artifact.

Why it matters:
- screenshots are evidence
- filenames are stable
- SHA-256 hash is recorded

### `record_fields`

Stores extracted values as structured `FieldExtraction` objects.

Each field tracks:
- `field_name`
- `value`
- `source_url`
- `source_selector`
- `artifact_ref`

Why it matters:
- every extracted value can be traced back to where it came from

### `mark_checkpoint`

Marks a required evidence milestone as satisfied.

Examples:
- `commit_page_screenshot`
- `issue_fields_extracted`
- `filled_form_screenshot`

Why it matters:
- prevents false completions

### `make_judgment`

Stores a structured audit judgment:
- question
- answer: `yes`, `no`, or `inconclusive`
- confidence
- reasoning
- evidence references
- source URLs

Why it matters:
- many audit tasks require interpretation, not just extraction

### `download_file`

Downloads an explicit file URL and saves it into the sample folder.

Why it matters:
- some Andera-style tasks need reports or attachments, not just screenshots

---

## 8. What a Strategy Is

A strategy is the project's way of saying:

> "What navigation pattern does this task belong to?"

This repo currently has 3 strategies:

### `single_page`

Use this when the task is:
- one URL
- one page
- extract fields
- take a screenshot

Examples:
- GitHub issue extraction
- simple page capture

File:
- [single_page.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/strategies/single_page.py)

### `graph_traversal`

Use this when the task requires:
- following links
- moving across related pages
- collecting evidence at each node

Examples:
- commit -> PR -> review page
- future GitHub -> Jira traversal
- future LinkedIn search -> profile
- future blame view -> commit -> judgment

File:
- [graph_traversal.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/strategies/graph_traversal.py)

### `form_fill`

Use this when the task requires:
- filling fields
- verifying entered values
- submitting forms
- capturing result pages
- possibly downloading outputs

Examples:
- Workday-like workflows
- public form demos

File:
- [form_fill.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/strategies/form_fill.py)

Important architectural idea:

> New task in an existing family should usually be just a new YAML file.  
> New task family should require a new strategy class.

---

## 9. What the YAML Task Files Do

The task YAML files are the job descriptions for the agent.

They define:
- task name
- strategy
- instructions
- input file
- expected output fields
- required checkpoints
- runtime limits
- judgment question

Current tasks:

- [tasks/demo.yaml](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/tasks/demo.yaml)
- [tasks/github_commits.yaml](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/tasks/github_commits.yaml)
- [tasks/github_issues.yaml](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/tasks/github_issues.yaml)
- [tasks/form_fill_demo.yaml](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/tasks/form_fill_demo.yaml)

The input CSV provides the sample rows.

For simple tasks:
- columns may just be `sample_id,url`

For richer tasks:
- extra columns become `extra_fields`
- strategies can inject those into prompts

That is how the form-fill demo works.

---

## 10. What the Pydantic Models Are Doing

The models are the strict contracts of the system.

Important ones:

### `TaskConfig`

Loaded from YAML.  
Defines the task itself.

### `SampleInput`

One row of work.

### `FieldSpec`

Declares expected output fields and types.

Examples:
- `str`
- `int`
- `bool`
- `date`
- `url`

### `FieldExtraction`

Stores one extracted field plus provenance.

### `EvidenceArtifact`

Stores one saved file plus metadata and SHA-256.

### `SampleResult`

The full output for one sample.

### `BatchResult`

Summary across all samples in a run.

Why this matters:

> The models are what turn agent behavior into a reliable data pipeline.

---

## 11. What Happens During One Sample Run

Example: GitHub issue extraction

1. load `github_issues.yaml`
2. create one `SampleInput` with issue URL
3. select `single_page` strategy
4. strategy builds the prompt
5. `EvidenceAgent` creates a `browser-use` agent
6. agent opens issue page
7. agent uses `screenshot_evidence`
8. agent uses `record_fields`
9. agent marks checkpoints
10. agent uses `make_judgment`
11. strategy validates result
12. files are written:
   - screenshot(s)
   - `result.json`
   - `action_log.json`
13. orchestrator writes:
   - `results.csv`
   - `run_summary.json`

This means the visible browser is only part of the story.  
The real deliverable is the evidence package on disk.

---

## 12. What Gets Written to Disk

Each run creates:

```text
evidence/run_TIMESTAMP/
  results.csv
  run_summary.json
  samples/
    sample_001/
      01_something.png
      result.json
      action_log.json
```

### `result.json`

This is the most important file.

It contains:
- status
- extracted fields
- artifacts
- checkpoints met
- judgment
- action log
- errors
- timestamps

### `results.csv`

This is the flat batch output for spreadsheet-style review.

### `run_summary.json`

This contains:
- total samples
- completed / failed / needs_review
- duration
- checkpoint pass rates
- needs_review breakdown
- top errors

Why this matters:

> The project is not trying to return one final paragraph of text.  
> It is trying to produce a reviewable evidence package.

---

## 13. LLM Providers and How They Work Here

This repo is provider-agnostic.

Configured in:
- [config.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/config.py)
- [llm.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/llm.py)

Supported providers in code today:
- Anthropic
- OpenAI
- Gemini
- browser-use cloud

This means:
- the architecture is not locked to one model vendor
- the chosen provider comes from `.env`

Examples:
- `LLM_PROVIDER=anthropic`
- `LLM_PROVIDER=openai`

### Important distinction

This project mostly uses the **open-source/self-hosted path**.

Optional browser-use cloud support exists through `ChatBrowserUse` in [llm.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/llm.py), but you do **not** need Browser Use Cloud to understand or run the core project structure.

---

## 14. `browser-use` Open Source vs Cloud

This came up before and is worth understanding.

### Open-source `browser-use`

You install the Python package and run it locally in your own project.

That is what this repo mainly does.

### Browser Use Cloud

This is an optional hosted service.

Why it exists:
- managed infra
- cloud browsers
- possibly easier scaling
- optional hosted model path

In this repo, cloud support exists only as one provider option. It is not required for the project architecture.

---

## 15. What Is Actually Proven in This Repo Today

As of the current project state:

### Strongly proven

- YAML-driven task loading
- evidence packaging
- dry-run validation
- issue extraction flow
- batch output generation
- strategy-based architecture

### Partially proven

- graph traversal commit audit
- form fill demo

### Architecturally prepared but not fully proven against Andera yet

- deep commit audit with checks/CI/Jira follow-through
- LinkedIn enrichment
- blame/materiality workflow
- Workday-like report download + attachment collection

This distinction matters in a presentation.  
Be confident, but do not oversell.

---

## 16. What This Project Is Good At

This project is especially strong at:

- turning browser work into repeatable filesystem evidence
- making tasks configurable via YAML
- separating reasoning from packaging
- handling multiple task families without rewriting the engine
- producing outputs that humans can review later

That is the right direction for audit/compliance automation.

---

## 17. Current Limitations

These are the important limitations to know honestly:

1. Not all Andera example task families are fully demonstrated yet.
2. Complex browser tasks can still be slow when the agent reasons too freely.
3. The `no_auth` vision mode is documented as limited and not the main focus today.
4. Hard workflows like LinkedIn matching and blame/materiality need more task-specific verification.
5. A framework being good does not automatically mean every example task is already solved.

Knowing these limitations actually helps in a presentation, because it lets you speak precisely.

---

## 18. How to Use the Project

### Validate a task without spending model/browser cost

```bash
python main.py --task tasks/github_commits.yaml --dry-run
```

### Run one sample directly

```bash
python main.py --task tasks/github_issues.yaml \
  --sample-id issue_001 \
  --url "https://github.com/microsoft/vscode/issues/305609"
```

### Run a batch from CSV

```bash
python main.py --task tasks/github_commits.yaml --max-concurrent 2
```

### Helpful flags

- `--dry-run`
- `--headless`
- `--max-concurrent`
- `--output`
- `--verbose`

CLI entrypoint:
- [main.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/main.py)

---

## 19. How to Explain This in a Presentation

Use this structure:

### 1. Start with the problem

Auditors manually collect evidence from many systems by opening pages, taking screenshots, copying values, and downloading files.

### 2. Explain the product in one line

This project turns that manual browser work into a configurable evidence agent.

### 3. Explain the architecture simply

- YAML defines **what** to collect
- strategies define **how** to navigate
- the LLM/browser agent performs the browser reasoning
- deterministic Python packages the results

### 4. Explain why this is good engineering

- modular
- extensible
- typed outputs
- reviewable artifacts
- evidence provenance

### 5. Explain current proof points honestly

- issue extraction is verified
- core engine is complete
- harder Andera tasks are the next coverage targets

That is a much stronger presentation than pretending everything is fully solved.

---

## 20. Good Talking Points

You can say:

> "We did not build a one-off scraper. We built a browser evidence platform."

> "The key design choice is that the LLM handles navigation and interpretation, while deterministic Python handles evidence packaging and validation."

> "That separation is why the project is extensible without becoming prompt spaghetti."

> "A new task in an existing family is mostly configuration, not new framework code."

> "The output is designed for reviewability: every extracted value can be traced back to a source URL and supporting artifact."

---

## 21. Questions You Should Be Ready For

### "Why use an LLM agent at all?"

Because browser UIs differ widely. Hard-coding every workflow is brittle. The LLM gives flexible navigation and interpretation.

### "Why not just scrape APIs?"

Because the brief explicitly assumes many workflows must work from the visible browser UI, even without integrations.

### "How do you keep outputs reliable if the agent is non-deterministic?"

By making the output layer deterministic and validating required checkpoints and field types after the run.

### "Can this handle new task types?"

Yes, if they fit an existing navigation family. Then it is mostly a new YAML task. If the navigation pattern is genuinely new, add a new strategy.

### "Does this already solve all Andera examples?"

No. The framework is strong, but some example task families still need dedicated proof runs and task configs.

---

## 22. Best Honest Summary

If you want one honest, strong summary to memorize, use this:

> This codebase is a configurable browser evidence agent built on top of `browser-use`. The underlying agent handles browser navigation and interpretation. Our project adds task schemas, strategies, checkpoints, judgments, deterministic evidence packaging, CSV output, and batch orchestration. The architecture is strong and reusable, and it already proves one task family well. The remaining Andera work is not building a new framework, but extending and verifying more task families on top of the same engine.

---

## 23. What To Read Next

If you want to learn the project in the best order:

1. [README.md](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/README.md)
2. [docs/ARCHITECTURE_AND_IMPLEMENTATION_PLAN.md](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/docs/ARCHITECTURE_AND_IMPLEMENTATION_PLAN.md)
3. [docs/BUILD_LOG.md](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/docs/BUILD_LOG.md)
4. [main.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/main.py)
5. [agent/evidence_agent.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/evidence_agent.py)
6. [agent/actions.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/agent/actions.py)
7. [strategies/base.py](/mnt/c/Users/Humblebee/Documents/GitHub/browser_use_agent/strategies/base.py)

That sequence will give you the clearest mental model.
