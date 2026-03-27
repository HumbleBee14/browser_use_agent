# Browser Evidence Agent — Architect Explanation

This document is the single reference for explaining the project like a senior architect in a meeting, design review, or presentation.

It is written to help you speak from first principles:
- what problem we are solving
- why a browser agent is the right approach
- why this architecture was chosen
- how the system works end to end
- how it maps to Andera's task requirements
- what is already strong
- what still needs tuning

Use this as both:
- a presentation script
- a technical explanation reference

---

## 1. The 30-Second Opening

If you need to start strong, say this:

> We built a general browser evidence agent for audit-style workflows. The core idea is simple: let the LLM handle navigation and interpretation inside the browser, but keep evidence packaging deterministic in Python. That gives us flexibility across many systems while still producing consistent sample folders, screenshots, CSVs, hashes, and reviewable manifests. The result is not a one-off scraper. It is a reusable browser evidence platform.

That opening frames:
- the problem
- the design principle
- the difference between agentic behavior and audit-grade outputs

---

## 2. What Problem We Are Solving

The Andera-style problem is:

- auditors get read-only access to systems like GitHub, Workday, Jira, Linear, HRIS/ERP tools
- evidence collection is manual
- humans open links, inspect pages, take screenshots, copy values into spreadsheets, and download files
- many tasks require judgment, not just extraction
- the workflow must work across many systems, not one rigid integration

That is why the requirement is not:

> "Build a GitHub scraper."

It is:

> "Build a general browser agent that can perform evidence collection workflows reliably across many systems."

That distinction matters.

If you build a site-specific scraper, you may succeed on one demo but fail the real requirement.
If you build a browser evidence platform, you can add new workflows with configuration and targeted helpers instead of rebuilding the whole system.

---

## 3. Why A Browser Agent Is The Right Primitive

There are three broad approaches:

### 1. Traditional RPA / hardcoded automation

Strengths:
- fast when the UI is stable
- deterministic

Weaknesses:
- brittle when labels, layouts, or flows change
- expensive to maintain across many systems
- poor fit for ambiguous reasoning tasks

### 2. API-only / integration-only approach

Strengths:
- efficient when APIs exist
- structured data

Weaknesses:
- many enterprise systems do not expose the needed data cleanly
- often not available in trials
- does not prove system-agnostic capability

### 3. Browser agent

Strengths:
- works through the UI like a human
- system-agnostic
- can navigate, screenshot, extract, and judge
- can still take advantage of integrations later

Weaknesses:
- slower than API-only
- needs careful prompting and runtime controls
- requires strong packaging and validation discipline

This project deliberately chooses the browser-agent route because it best matches Andera's brief:
- browser-first
- ERP/HRIS-agnostic
- evidence-heavy
- capable of both extraction and judgment

---

## 4. The Most Important Architectural Decision

The single most important design idea is:

> Agentic navigation, deterministic packaging.

That means:

### The LLM/browser agent decides:
- where to go next
- what page matters
- what links to follow
- what fields to extract
- what the page means
- what judgment to make

### Deterministic Python decides:
- folder structure
- file naming
- SHA-256 hashing
- CSV column ordering
- field validation
- checkpoint verification
- result manifests

This split exists because audit workflows need both:

- flexibility during navigation
- consistency after collection

If the LLM also named files, structured outputs, or decided CSV layout, the system would be too inconsistent for audit work.

So the architecture says:

> use intelligence where ambiguity exists  
> use deterministic code where consistency matters

That is the right engineering tradeoff.

---

## 5. Why We Built On Top Of `browser-use`

We did not build a browser runtime from scratch.
We built on top of `browser-use`.

### What `browser-use` gives us

- browser session management
- browser automation over Playwright
- an LLM-driven agent loop
- built-in browser actions like navigate, click, type, scroll, extract
- model/provider integration
- memory across steps

### What our project adds

- YAML task definitions
- typed output schemas
- checkpoint-based completion rules
- structured judgments
- deterministic evidence folder creation
- artifact hashing
- CSV packaging
- per-sample manifests
- batch orchestration
- runtime reporting

So the clean way to explain it is:

> `browser-use` is the browser brain and hands.  
> This repo is the evidence platform built around it.

---

## 6. High-Level System Overview

The runtime flow is:

1. User runs `main.py` with a task YAML.
2. YAML is loaded into a typed `TaskConfig`.
3. CSV input is loaded into `SampleInput` rows.
4. Strategy is selected based on the task's navigation pattern.
5. `BatchOrchestrator` processes samples concurrently.
6. One `EvidenceAgent` runs per sample.
7. `EvidenceAgent` creates a `browser-use` agent with our custom actions.
8. The browser agent navigates and collects evidence.
9. Strategy validation checks checkpoints and field types.
10. Deterministic writers save screenshots, manifests, CSVs, and summaries.

In one sentence:

> YAML defines what to collect, strategies define how to navigate, the agent performs the browser reasoning, and Python packages the evidence.

---

## 7. Main Modules And Their Responsibilities

### `main.py`

Responsibility:
- CLI entrypoint
- parse arguments
- load task and inputs
- support `--dry-run`
- create orchestrator and start batch

Why it exists:
- gives one operational interface for all workflows

### `agent/task_loader.py`

Responsibility:
- load YAML task definitions
- load CSV/JSON inputs
- validate required columns

Why it exists:
- converts unstructured files into typed runtime objects

### `agent/orchestrator.py`

Responsibility:
- concurrent batch execution
- per-sample fault isolation
- batch-level CSV and summary writing

Why it exists:
- the system is designed for many samples, not just one URL at a time

### `agent/evidence_agent.py`

Responsibility:
- create one browser agent per sample
- attach strategy prompt
- register custom evidence actions
- run with timeout/retry
- package results

Why it exists:
- central wrapper around the lower-level browser runtime

### `agent/actions.py`

Responsibility:
- custom actions:
  - `screenshot_evidence`
  - `record_fields`
  - `mark_checkpoint`
  - `make_judgment`
  - `download_file`

Why it exists:
- converts general browser actions into audit/evidence actions

### `strategies/`

Responsibility:
- navigation-family-specific prompting and validation

Why it exists:
- different task families have different navigation patterns

### `models/`

Responsibility:
- typed contracts for tasks, evidence, judgments, results

Why it exists:
- the system needs strong contracts between agent behavior and output packaging

### `output/`

Responsibility:
- folder creation
- deterministic filenames
- hashing
- `result.json`
- `action_log.json`
- `results.csv`
- `run_summary.json`

Why it exists:
- evidence packaging must be deterministic and auditable

---

## 8. Why The Strategy Pattern Matters

A key design choice was not to create one giant "do everything" agent.

Instead, we grouped tasks by navigation pattern.

Current strategies:

### `single_page`

Use when the task is:
- open one page
- extract fields
- take a screenshot

Examples:
- ticket extraction
- issue extraction
- simple page capture

### `graph_traversal`

Use when the task is:
- start on one page
- follow related links
- collect evidence across multiple nodes

Examples:
- commit -> PR -> checks -> CI -> ticket
- Google -> LinkedIn profile
- file -> blame view -> recent commit

### `form_fill`

Use when the task is:
- fill inputs
- submit
- capture result
- optionally download artifacts

Examples:
- Workday-style form workflows
- report generation
- public-form demos

Why this is the right split:

- not too generic
- not too task-specific
- matches real differences in browser behavior
- lets new tasks in an existing family be mostly YAML/config

The important idea is:

> tasks differ less by business label and more by navigation shape

That is a strong architecture decision.

---

## 9. Data Model — The Contracts That Hold The System Together

If you want to sound senior in a design conversation, talk about contracts.

This system is strong because the core data models are explicit.

### `TaskConfig`

This defines:
- task name
- strategy
- instructions
- input file
- input columns
- output fields
- checkpoints
- evidence types
- runtime limits
- judgment question

This is the "WHAT" layer.

### `SampleInput`

This defines one row of work:
- `sample_id`
- `url` if applicable
- `extra_fields` for task-specific inputs

This is how CSV rows become runtime units.

### `FieldSpec`

This declares:
- field name
- expected type
- whether required
- description

This gives typed validation after extraction.

### `Checkpoint`

This defines:
- a required evidence milestone
- evidence type
- required vs optional

This prevents fake completion.

### `FieldExtraction`

This stores:
- field name
- value
- source URL
- source selector
- artifact reference

This is the provenance chain.

### `JudgmentResult`

This stores:
- question
- answer
- confidence
- reasoning
- evidence refs
- source URLs

This is important because many workflows are not pure extraction.

### `Artifact`

This stores:
- filename
- type
- source URL
- SHA-256 hash

This makes the output auditable.

### `SampleResult`

This is the final per-sample truth:
- status
- extracted fields
- artifacts
- checkpoints met
- judgment
- errors
- action log

This is the object that represents one finished unit of work.

---

## 10. What The Custom Actions Mean In Practice

These are the real bridge between "general browser agent" and "evidence platform."

### `screenshot_evidence`

Purpose:
- capture named screenshots as proof

Why it matters:
- screenshots are not decorative
- they are part of the evidence package

### `record_fields`

Purpose:
- persist extracted values with provenance

Why it matters:
- extracted values must point back to where they came from

### `mark_checkpoint`

Purpose:
- mark required sub-steps as complete

Why it matters:
- the agent should not be trusted just because it says "done"

### `make_judgment`

Purpose:
- persist a structured interpretation

Why it matters:
- many Andera tasks ask "was this reviewed?" or "could this be material?"

### `download_file`

Purpose:
- save downloaded artifacts with hashing

Why it matters:
- audit evidence may include reports and attachments, not just screenshots

---

## 11. How A Normal Runtime Flow Works

Here is the clearest way to explain the runtime:

### Step 1: Task is defined in YAML

Example:
- what fields to extract
- what checkpoints are required
- which strategy to use
- how much time to allow

### Step 2: Inputs come from a CSV

Each row becomes one `SampleInput`.

Examples:
- one commit URL
- one ticket URL
- one person name
- one form URL plus form data

### Step 3: Strategy builds the prompt

The strategy converts:
- task definition
- sample input

into:
- a browser-agent instruction set tailored to that navigation pattern

### Step 4: Browser agent runs

The agent:
- opens the page
- navigates
- clicks
- extracts
- screenshots
- records fields
- marks checkpoints
- makes judgment

### Step 5: Validation runs

After the agent finishes, deterministic validation checks:
- required checkpoints met?
- required fields present?
- field types valid?

### Step 6: Outputs are written

The system writes:
- sample screenshots
- `result.json`
- `action_log.json`
- `results.csv`
- `run_summary.json`

That is the full loop.

---

## 12. Why YAML Is Important

YAML is not just convenience.
It is a product decision.

It means:
- new workflow in an existing family does not require a code rewrite
- task logic can be made visible and reviewable
- requirements stay close to business language

YAML should define:
- what evidence matters
- what fields matter
- what success means

Python should define:
- the reusable engine

This is why adding new Andera tasks in Phase 5 was mostly task-definition work.

That is not laziness.
That is the architecture doing its job.

---

## 13. How This Maps To Andera's Requirements

Andera asked for a general browser agent with:
- system-agnostic browser operation
- screenshot capture
- CSV extraction
- judgment support
- one folder per sample
- consistent file naming
- support for many task shapes

They also gave example task families.

### 1. Ticket extraction

Requirement:
- open ticket
- take screenshot
- extract assignee / due date style fields

Mapping:
- `single_page`

### 2. Commit audit

Requirement:
- commit page
- open PR
- inspect checks / CI
- inspect linked ticket
- produce CSV + screenshots

Mapping:
- `graph_traversal`

### 3. LinkedIn enrichment

Requirement:
- start from a CSV of names
- search
- identify the right profile
- extract profile data

Mapping:
- `graph_traversal`

### 4. Blame/materiality

Requirement:
- inspect code history
- see if recent changes fall in threshold
- assess whether the change could materially alter logic

Mapping:
- `graph_traversal`

### 5. Form fill + report download

Requirement:
- fill form
- screenshot filled state
- submit
- capture result
- download report / attachments

Mapping:
- `form_fill`

This is why the 3-strategy model works well.

---

## 14. What We Mean By "Internal Requirements"

If asked about internal engineering requirements, explain them like this:

### Accuracy

We prioritize:
- correct extraction
- correct evidence packaging
- truthful `needs_review` outcomes when blocked

Example:
- LinkedIn auth wall should become "login required," not a guessed profile

### Generability

New workflows should be easy to define.

That is why:
- YAML task definitions exist
- strategies are reusable

### Scalability

The system is batch-oriented:
- one sample per folder
- concurrent orchestration
- fault isolation
- consolidated CSV

### Consistency

We enforce:
- deterministic filenames
- stable manifests
- hashes
- typed outputs

### Speed

We optimize speed, but not by sacrificing correctness.
That is why speed is treated as:
- prompt tuning
- timeout tuning
- domain-specific helper actions
- session reuse

not as:
- blind reduction of evidence collection

---

## 15. Performance And Reliability — How To Explain Them

This is the section that makes you sound experienced.

### Browser agents are not just "make it work"

Performance comes from:

### 1. Tight prompts

Loose prompts cause:
- repeated screenshots
- unnecessary revisits
- wasted steps

So prompts should say:
- take one screenshot
- record fields immediately
- stop when required checkpoints are satisfied

### 2. Concurrency

`BatchOrchestrator` runs multiple samples concurrently with bounded parallelism.

Why:
- better throughput
- no single sample blocks the whole batch

### 3. Session reuse

For real enterprise flows, reusing authenticated browser state per domain matters.

Why:
- faster than logging in every sample
- fewer auth interruptions
- more stable batch behavior

### 4. DOM-first operation

Prefer structured page extraction over vision when possible.

Why:
- cheaper
- faster
- more precise

### 5. Fail-fast configuration

Each task should have:
- realistic `max_steps`
- realistic `timeout_seconds`
- limited retries

Why:
- one bad sample should not burn 10 minutes unnecessarily

### 6. Domain-specific helper actions

Generic browser reasoning is powerful but slower.
For heavy recurring domains, targeted helper actions improve:
- speed
- consistency
- extraction quality

Examples:
- GitHub commit metadata helper
- PR checks helper
- ticket field helper

That is the next stage of optimization after the architecture is proven.

---

## 16. What Is Working Well Right Now

Speak honestly but positively:

- the engine architecture is real and clean
- task addition is genuinely config-driven
- deterministic evidence packaging is strong
- one task family can already complete cleanly end-to-end
- other task families are functionally viable
- failure states are being surfaced as `needs_review`, not silent nonsense

That is a good sign.

---

## 17. What Still Needs Refinement

Be honest here. Senior engineers do not oversell.

Current remaining refinements:

### 1. Timeout tuning on slow GitHub workflows

Observed issue:
- agent reaches the right pages
- captures evidence
- times out before final packaging/judgment

Meaning:
- workflow is broadly correct
- runtime limits are too tight

### 2. Record earlier, not only at the end

For multi-page tasks, fields should be recorded as soon as they are known.

Why:
- protects against timeout before final write

### 3. LinkedIn fallback semantics

Ambiguous/login-required cases must be consistent with field typing and output expectations.

### 4. More domain-specific helpers for heavy workflows

This is how to move from:
- "good architecture"
to:
- "fast and reliable on difficult production flows"

---

## 18. How To Explain The Current Project State Honestly

Say this:

> The framework is strong and reusable. We have proven the architecture, the evidence model, and the task system. Some workflows already complete cleanly end-to-end, while the slower GitHub-heavy tasks are reaching the right pages and collecting evidence but still need timeout and prompt tuning. That means the remaining work is optimization and hardening, not re-architecture.

That is the correct senior framing.

---

## 19. Suggested Meeting / Presentation Structure

If you are presenting live, use this order:

### 1. Problem

Start with:
- manual audit evidence collection is slow and repetitive
- systems are heterogeneous
- APIs are not always available
- browser-first automation is the right abstraction

### 2. Design principle

Then say:

> agentic navigation, deterministic packaging

This is your key architecture insight.

### 3. Architecture

Walk through:
- YAML task
- strategy
- browser agent
- custom actions
- validation
- evidence outputs

### 4. Data model

Show:
- task config
- sample input
- field extraction
- checkpoints
- judgment
- sample result

This shows rigor.

### 5. Task coverage

Then map the 5 Andera task families to the 3 strategies.

### 6. Runtime results

Show:
- what completed
- what reached the right pages but timed out
- why that is a tuning problem, not an architecture failure

### 7. Roadmap

End with:
- timeout tuning
- earlier field recording
- helper actions for high-frequency domains
- more reference runs

---

## 20. A Strong 2-Minute Talk Track

If you need a compact version, say this:

> We approached this as a browser evidence platform, not a one-off scraper. The underlying runtime uses `browser-use` to operate a browser with an LLM agent, but we wrapped that in our own architecture for audit-grade outputs. The key design is agentic navigation with deterministic packaging: the LLM decides where to go and what to interpret, while Python enforces stable filenames, SHA-256 hashes, typed outputs, checkpoint validation, and per-sample evidence folders.
>
> The system is driven by YAML task definitions and CSV inputs. Each task declares its required fields, evidence checkpoints, runtime limits, and judgment question. We grouped workflows into three navigation families: `single_page`, `graph_traversal`, and `form_fill`. That lets us support tasks like ticket extraction, commit audits, LinkedIn enrichment, blame analysis, and form/report workflows without rebuilding the engine each time.
>
> Architecturally, the flow is: CLI loads task config and samples, the orchestrator runs samples concurrently, one `EvidenceAgent` executes each sample in the browser, custom actions capture screenshots and structured fields, and deterministic writers package everything into `result.json`, `results.csv`, and `run_summary.json`. This matches the audit need for consistency, traceability, and reviewability.
>
> The current state is strong: the architecture is proven, task coverage is broad, and some workflows already complete end-to-end. The remaining work is mainly runtime tuning on slower GitHub-heavy flows, not redesign.

---

## 21. If They Ask "Why Is This Better Than A Simple Scraper?"

Answer:

> Because the requirement is not one site with one layout. The requirement is a general browser workflow agent across many enterprise systems. A scraper hardcodes assumptions. This design keeps the navigation layer flexible while making the outputs auditable and consistent. That is the right balance for Andera's domain.

---

## 22. If They Ask "Why Not Build More Task-Specific Code?"

Answer:

> We deliberately separated the reusable engine from task-specific definitions. If every workflow became custom code, the system would not generalize. We only add task-specific helpers when they materially improve speed or reliability for high-value workflows. That keeps the architecture modular instead of turning into prompt spaghetti or site-specific scripts everywhere.

---

## 23. If They Ask "What Would You Improve Next?"

Answer:

1. Tighten runtime configs for GitHub-heavy workflows.
2. Record fields earlier in multi-page tasks.
3. Add deterministic helper actions for frequent domains like GitHub.
4. Strengthen the LinkedIn fallback semantics.
5. Build more reference runs and lightweight evals per workflow.

That answer sounds grounded and practical.

---

## 24. Final Framing

If you want a strong closing line, use this:

> The important thing we built is not just browser automation. We built a configurable evidence-collection system where workflows can change, sites can vary, and the outputs still remain structured, reviewable, and consistent. That is the architectural core needed for Andera-style automation.

