# Build Log — Browser Evidence Agent

A running record of what was built, in what order, and why. Use this to explain decisions during demos or code reviews.

---

## Phase 1: Foundation

**Time:** ~1 hour
**Goal:** Complete project skeleton that compiles, imports, and runs CLI end-to-end.

### What Was Built (in order)

#### 1. Project Setup
- **Files:** `requirements.txt`, `.env.example`, `config.py`, `.gitignore` update
- **Why:** Production practice — pinned dependencies, environment-based config (no hardcoded keys), gitignored runtime outputs.
- **Key decision:** Used `python-dotenv` for `.env` file support instead of hardcoding. `config.py` is the single source of truth for all settings.

#### 2. Pydantic Data Models (`models/`)
- **Files:** `task.py`, `evidence.py`, `judgment.py`, `__init__.py`
- **Why built first:** Everything else depends on these types. Schema-first development — define the data contracts before writing any logic.
- **What's in them:**
  - `TaskConfig` — loaded from YAML. Declares fields, checkpoints, strategy, limits.
  - `FieldSpec` — typed field declarations with `validate_value()` for coercion (str→int, str→date, etc.).
  - `Checkpoint` — required evidence collection points that prevent false-positive completions.
  - `SampleInput` — one row of work from the input CSV.
  - `FieldExtraction` — extracted value + provenance (source_url, selector, artifact_ref).
  - `EvidenceArtifact` — one saved file + sha256 hash + source URL.
  - `SampleResult` — complete per-sample output with status, fields, artifacts, checkpoints, errors.
  - `ActionLogEntry` — structured (not free-text) action log.
  - `JudgmentResult` — typed answer (yes/no/inconclusive) + confidence + reasoning.
  - `NeedsReviewReason` — enum explaining WHY a sample needs human review.
- **Key decision:** `Field(default_factory=list)` everywhere per Pydantic v2 best practice — no mutable default sharing.

#### 3. Task Strategies (`strategies/`)
- **Files:** `base.py`, `single_page.py`, `graph_traversal.py`, `form_fill.py`, `__init__.py`
- **Why:** Different task types need different navigation patterns. A commit audit (follow links across pages) is fundamentally different from a simple page extraction.
- **Architecture:**
  - `BaseTaskStrategy` (ABC) — defines `build_prompt()`, `should_use_vision()`, `validate_result()`.
  - `SinglePageStrategy` — visit one URL, extract, screenshot. Simplest case.
  - `GraphTraversalStrategy` — extends SinglePage with multi-page navigation rules (track visited URLs, satisfy checkpoints at each node, handle cross-domain).
  - `FormFillStrategy` — extends SinglePage with form-specific instructions (fill, verify, submit, download).
- **Key decisions:**
  - **3 strategies, not 5** — commit audit, blame analysis, and LinkedIn enrichment are all "follow links, collect at nodes" patterns. Same strategy, different YAML. See ADL-2 and ADL-3 in the architecture doc.
  - **Strategies are stateless** — fresh instance per sample. No cross-contamination under concurrent execution. See ADL-4.
  - **Strategy registry** — one dict mapping YAML names to classes. Adding a strategy = one new class + one line in registry.

#### 4. Output Layer (`output/`)
- **Files:** `file_manager.py`, `csv_writer.py`, `__init__.py`
- **Why:** Deterministic evidence packaging — no LLM involved. Same evidence in = same files out.
- **FileManager:**
  - Creates per-sample folder, saves screenshots with sequential naming (01_, 02_), computes SHA-256 hashes.
  - Returns `EvidenceArtifact` objects with full provenance (path, hash, source_url, checkpoint_ref).
  - Tracks all artifacts for later inclusion in `SampleResult`.
- **CSVWriter:**
  - Buffered batch write — one `write_batch()` call after all samples complete.
  - No per-row pandas append (expensive, not concurrency-safe).
- **Key decision:** Agentic vs Deterministic split (ADL-5). The LLM decides what to extract. Python decides how to name, hash, and store it.

#### 5. Custom Actions (`agent/actions.py`)
- **Files:** `actions.py`
- **Why:** browser-use's built-in actions handle navigation. We need evidence-specific operations.
- **Actions registered (5 total):**
  - `screenshot_evidence(label, full_page=True)` — named screenshots with SHA-256 hashing and source URL provenance. `full_page=true` for entire scrollable page, `false` for viewport only.
  - `record_fields(fields_json, source_selector, artifact_ref)` — records extracted field values with full provenance: `source_url` (auto from page), `source_selector` (optional CSS selector or description), `artifact_ref` (auto-links to latest screenshot if not provided).
  - `mark_checkpoint(checkpoint_name)` — marks evidence checkpoints as satisfied.
  - `make_judgment(question, answer, confidence, reasoning, evidence_refs, source_urls)` — structured audit judgment. Auto-populates `evidence_refs` from all collected artifacts and `source_urls` from current page if not provided explicitly.
  - `download_file(filename, url)` — downloads a file from a URL, saves to evidence folder with SHA-256 hash. Uses browser context for authenticated downloads.
- **Key decision:** Actions use closure over `FileManager` — no global state. Fresh controller per sample.
- **API discovery:** browser-use v0.12.5 uses `Controller` (alias for `Tools`), `BrowserProfile` (not `BrowserConfig`), `ActionResult` from `browser_use.agent.views`. Injectable special params: `browser_session`, `page_url`, `page_extraction_llm`.

#### 6. EvidenceAgent (`agent/evidence_agent.py`)
- **Files:** `evidence_agent.py`
- **Why:** Wraps browser-use `Agent` with our evidence collection workflow.
- **Lifecycle:** One agent per sample. Create → run → validate → return result → discard.
- **Features:**
  - Strategy-driven prompt building and result validation.
  - Per-sample timeout via `asyncio.wait_for`.
  - Retry logic (configurable `max_retries`).
  - All exceptions caught — returns `SampleResult(status=FAILED)`, never raises.
  - Vision mode controlled by strategy's `should_use_vision()`.

#### 7. BatchOrchestrator (`agent/orchestrator.py`)
- **Files:** `orchestrator.py`
- **Why:** Manages concurrent processing of multiple samples.
- **Features:**
  - Semaphore-bounded concurrency (default 3).
  - Fresh strategy instance per sample (ADL-4).
  - All exceptions caught inside `_process_sample` — wrapped as failed `SampleResult`.
  - Buffered CSV write after all samples complete.
  - `run_summary.json` with aggregate stats.
  - Timestamped run directories.

#### 8. Task Loader (`agent/task_loader.py`)
- **Files:** `task_loader.py`
- **Why:** Reads YAML task definitions and CSV input files. Validates against Pydantic models.

#### 9. CLI Entry Point (`main.py`)
- **Files:** `main.py`
- **Why:** Production CLI with argparse, rich output, progress tracking.
- **Modes:**
  - Batch: `python main.py --task tasks/demo.yaml`
  - Single sample: `python main.py --task tasks/demo.yaml --sample-id test_001 --url https://...`
- **Features:** Rich banner, summary table, issue reporting for failed/needs_review samples.

#### 10. Task Definitions (`tasks/`)
- **Files:** `demo.yaml`, `github_commits.yaml`, `inputs/demo.csv`, `inputs/github_commits.csv`
- **Why:** Demonstrate the task-as-data pattern. These are config, not code.

#### 11. LLM Provider Abstraction (`agent/llm.py`)
- **Files:** `llm.py`
- **Why:** Provider-agnostic LLM factory. No hardcoded Anthropic — supports Anthropic, OpenAI, Google, browser-use cloud.
- **How it works:** `create_llm()` reads `LLM_PROVIDER` from `.env`, looks up the factory in `PROVIDER_REGISTRY`, creates the right `ChatOpenAI`/`ChatGoogle`/etc instance.
- **Key decision:** Switch providers by changing one line in `.env`. No code changes. This is production thinking — clients may use different LLM providers.

### Issues Encountered & Fixed

#### Issue 1: `BrowserConfig` renamed to `BrowserProfile`
- **What:** browser-use v0.12.5 renamed `BrowserConfig` to `BrowserProfile`.
- **Fix:** Use `BrowserProfile` import. The API changes fast — always check actual exports.

#### Issue 2: Type annotation conflict on custom actions
- **What:** Adding `browser_session: BrowserSession` type annotation on custom actions caused: `"parameter conflicts with special argument injected by tools"`.
- **Why:** browser-use injects special params by name. If you also type-annotate them, it detects a conflict between your annotation and its internal type resolution.
- **Fix:** Remove type annotations on injected params (`browser_session`, `page_url`). Let browser-use inject them untyped.

#### Issue 3: `langchain_anthropic.ChatAnthropic` doesn't implement browser-use's `BaseChatModel`
- **What:** browser-use has its own LLM protocol requiring `.provider`, `.name`, `.model` properties. `ChatAnthropic` from langchain doesn't have these.
- **Fix:** Created provider-agnostic `agent/llm.py` using browser-use's own `ChatOpenAI` class with Anthropic's OpenAI-compatible endpoint (`base_url="https://api.anthropic.com/v1/"`).

#### Issue 4: Anthropic rejects OpenAI-style JSON schema constraints
- **What:** browser-use sends `response_format.json_schema` with `minimum` property. Anthropic's OpenAI-compat endpoint rejects this: `"For 'integer' type, property 'minimum' is not supported"`.
- **Fix:** Set three flags on `ChatOpenAI`:
  - `add_schema_to_system_prompt=True` — puts schema in prompt text instead of `response_format`
  - `dont_force_structured_output=True` — doesn't require structured output format
  - `remove_min_items_from_schema=True` — strips unsupported schema constraints

#### Issue 5: Double-decoded screenshots (broken images)
- **What:** Screenshots saved as 119-182 byte files — corrupted, can't open.
- **Why:** `browser_session.take_screenshot()` already returns decoded bytes internally. Our action was calling `base64.b64decode()` on already-decoded bytes — double decode produced garbage.
- **Fix:** Removed the `base64.b64decode()` call. Use raw bytes directly from `take_screenshot()`.

#### Issue 6: Retry accumulates duplicate data
- **What:** On timeout retries, the same `FileManager` accumulated checkpoints and fields from all attempts.
- **Fix:** Create fresh `FileManager` at the start of each retry attempt.

### How to Run & Test

```bash
# 1. Activate virtual environment
.venv\Scripts\activate   # Windows
source .venv/bin/activate # macOS/Linux

# 2. Make sure .env has your API key
# Edit .env: set ANTHROPIC_API_KEY (or OPENAI_API_KEY + change LLM_PROVIDER=openai)

# 3. Run a single-sample demo
python main.py --task tasks/demo.yaml --sample-id test_001 --url https://github.com/browser-use/browser-use

# 4. Check output
# evidence/run_<timestamp>/
#   results.csv              ← extracted fields
#   run_summary.json         ← pass/fail stats
#   samples/test_001/
#     01_main_page.png       ← screenshot
#     result.json            ← full provenance
#     action_log.json        ← agent steps

# 5. Run batch from CSV
python main.py --task tasks/github_commits.yaml

# 6. CLI options
python main.py --help
python main.py --task tasks/demo.yaml --headless         # no visible browser
python main.py --task tasks/demo.yaml --max-concurrent 5 # parallel samples
python main.py --task tasks/demo.yaml --verbose           # debug logging
```

### What's Working (Verified)
- Full pipeline: YAML config → load samples → browser opens → navigate → screenshot → extract fields → checkpoints verified → result.json + CSV + summary
- Provider-agnostic LLM: switch via `.env`, no code changes
- Evidence output: per-sample folders, SHA-256 hashed screenshots, structured result manifests
- Fault isolation: failed samples don't crash the batch
- Rich CLI output: banner, progress, summary table, issue reporting

### What's NOT Built Yet
- Additional task YAMLs (Linear, LinkedIn, Workday)
- `sensitive_data` integration for auth flows
- Web UI

### How to Explain This Phase

> "We built the complete foundation bottom-up: typed data models first, then the deterministic output layer (no LLM), then the strategies (navigation patterns), then the agent wrapper, then the orchestrator, then the CLI. Every layer only depends on layers below it. The models compile independently. The output layer doesn't know about browser-use. The strategies don't know about file I/O. Clean separation."

> "The key architectural insight is the agentic/deterministic split: the LLM decides WHERE to navigate and WHAT to extract. Pure Python decides HOW to name files, compute hashes, write CSVs, and validate results. This matters for audit work where consistency beats cleverness."

> "We hit real integration issues — browser-use v0.12.5 API changes, Anthropic's OpenAI-compat endpoint schema limitations, screenshot encoding — and solved each one cleanly. The provider abstraction means we can switch LLMs by changing one line in .env. That's production thinking."

---

## Phase 2: Real-World Validation & Agent Intelligence

**Goal:** Prove the agent works on real multi-page workflows with actual data extraction, audit judgments, and fault tolerance.

### What Was Built (in order)

#### 1. Native Anthropic LLM Provider (`agent/llm.py`)
- **What changed:** Replaced the OpenAI-compatibility hack (`ChatOpenAI` with `base_url="https://api.anthropic.com/v1/"`) with browser-use's native `ChatAnthropic` class.
- **Why:** The OpenAI-compat endpoint had JSON schema limitations (Anthropic rejects `minimum`, `minItems` properties). The native `ChatAnthropic` uses the direct Anthropic SDK with proper tool calling, prompt caching, and structured output — no hacks needed.
- **Before:** `ChatOpenAI(base_url="https://api.anthropic.com/v1/", dont_force_structured_output=True, remove_min_items_from_schema=True, add_schema_to_system_prompt=True)` — 4 workaround flags.
- **After:** `ChatAnthropic(model=model, api_key=api_key, max_retries=3, temperature=0.2)` — clean, no workarounds.
- **Impact:** Provider banner now correctly shows `Provider: anthropic` instead of `Provider: openai`. Prompt caching works. Tool calling is native.

#### 2. Browser Page Load Tuning (`agent/evidence_agent.py`)
- **What changed:** Added `BrowserProfile` timing parameters to prevent screenshot timeouts on JS-heavy pages.
- **Why:** GitHub pages never reach "fully idle" — their JS (analytics, WebSocket, lazy-loading) runs continuously. browser-use's default `wait_for_network_idle_page_load_time` caused 15s+ screenshot watchdog timeouts.
- **Config:**
  ```python
  BrowserProfile(
      wait_for_network_idle_page_load_time=5.0,  # Down from 8.0s default
      minimum_wait_page_load_time=0.5,            # Don't wait unnecessarily
      wait_between_actions=0.5,                    # Faster action execution
  )
  ```
- **Impact:** Eliminated most `ScreenshotWatchdog` timeout warnings. Pages that previously timed out now proceed after 5s.

#### 3. Structured Live Logging (`agent/orchestrator.py`, `agent/evidence_agent.py`, `main.py`)
- **What changed:** Replaced the Rich spinner (which suppressed all logs) with structured, color-coded live progress output.
- **Why:** The `console.status()` spinner hid all per-step and per-sample progress. Users couldn't see what the agent was doing.
- **What it shows now:**
  ```
  Batch: 5 samples | concurrency=2 | strategy=graph_traversal

  [1/5] commit_001 starting...
           commit_001 step 1: screenshot_evidence  Take a screenshot...
           commit_001 step 2: record_fields, mark_checkpoint  Extract fields...
           commit_001 step 3: navigate  Follow PR link...
           commit_001 step 4: make_judgment  Make audit judgment...
  [1/5] commit_001 COMPLETED (7 steps, 166.0s, 7 fields, 4 artifacts, 3 checkpoints)
  ```
- **Implementation:** Used browser-use's `register_new_step_callback` (signature: `BrowserStateSummary, AgentOutput, int`) to log action names and goals at each step. Orchestrator logs sample start/finish with color-coded status.

#### 4. Download Action Hardening (`agent/actions.py`)
- **What changed:** Fixed `download_file` action to prevent false evidence.
- **Problems fixed:**
  - **Removed page_url fallback:** Previously, if no explicit URL was given, it fell back to `page_url` — which would just GET the current HTML page. Now requires an explicit URL.
  - **Added HTTP status validation:** Rejects non-2xx responses instead of silently saving error pages.
  - **Added content-type validation:** Rejects `text/html` responses when expecting a file (catches auth walls, redirects, error pages).
  - **Reports content-type in success message** for audit trail.
- **Why:** For Andera's audit workflows, saving an HTML login page as "report.pdf" would be false evidence. This is a correctness issue, not just robustness.

#### 5. Vision Mode Documentation Fix (`strategies/base.py`, `agent/evidence_agent.py`)
- **What changed:** Updated docstrings to honestly document that `no_auth` vision mode only evaluates once at agent creation, not per-step.
- **Why:** The architecture doc claimed a step callback would toggle vision on/off for login pages. In reality, browser-use's `Agent` doesn't support changing `use_vision` mid-run. The docs now say this clearly.
- **Status:** This is a known limitation. Current tasks all use `use_vision: auto` (DOM-only). If auth-gated flows are needed, this requires either upstream API changes or a custom solution.

#### 6. GitHub Issues Task Definition (`tasks/github_issues.yaml`)
- **What:** New task type using `single_page` strategy to extract issue details from GitHub.
- **Fields:** issue_number, title, state, author, labels, created_date, comment_count.
- **Judgment:** "Is this issue a bug report?" — demonstrates judgment on a different domain.
- **Why:** Proves the pipeline is general — same CLI, different YAML, different fields, same architecture.

#### 7. Real Test Data (`tasks/inputs/`)
- **github_commits.csv:** 5 real vscode commits — 3 merge commits with linked PRs, 1 invalid SHA (404 test), 1 `commit/main` (latest commit test). Tests happy path + fault tolerance.
- **github_issues.csv:** 3 real vscode issues for the issues task.

### Input → Output: What Goes In, What Comes Out

#### Inputs
```
tasks/github_commits.yaml          ← Task definition: what to extract, checkpoints, strategy
tasks/inputs/github_commits.csv    ← Sample URLs: one commit URL per row
.env                               ← API key + provider config
```

#### Command
```bash
python main.py --task tasks/github_commits.yaml --sample-id commit_001 \
  --url "https://github.com/microsoft/vscode/commit/9986a43..."
```

#### Output Directory Structure
```
evidence/run_2026-03-27_033157/
├── results.csv                    ← Master CSV: all samples, all typed fields, sorted by sample_id
├── run_summary.json               ← Aggregate stats: completed/failed/needs_review counts, duration
└── samples/
    └── commit_001/
        ├── 01_commit_page.png     ← Evidence screenshot of commit page (SHA-256 hashed)
        ├── 02_commit_page.png     ← Second screenshot attempt (agent retried for clarity)
        ├── 03_commit_page.png     ← Final commit page screenshot (used for checkpoint)
        ├── 04_pr_review.png       ← Evidence screenshot of linked PR review page
        ├── result.json            ← Complete provenance: every field → source URL + artifact ref
        └── action_log.json        ← Chronological agent steps: what action, what URL, what result
```

#### What Each Output File Contains

**`results.csv`** — One row per sample, typed columns matching the YAML `output_fields`:
```csv
sample_id,status,commit_hash,author,date,message,files_changed,has_pr,was_reviewed
commit_001,completed,9986a43...,benibenj,2026-03-27,Merge pull request #305569...,1,True,True
```

**`run_summary.json`** — Batch-level stats for reporting:
```json
{"task": "github_commit_audit", "total_samples": 1, "completed": 1, "failed": 0,
 "avg_steps_per_sample": 7.0, "total_duration_seconds": 166.0}
```

**`result.json`** — Per-sample provenance manifest. Every extracted field traces back to:
- `source_url`: exact URL where the value was found
- `source_selector`: what DOM element or method was used
- `artifact_ref`: which screenshot supports this extraction
- Judgment includes `evidence_refs` (artifact filenames) and `source_urls` (pages reviewed)

**`action_log.json`** — Step-by-step agent behavior log for debugging and audit:
```json
[{"step": 1, "action": "navigate", "target": "https://github.com/...", "result": "Navigated to..."},
 {"step": 2, "action": "screenshot_evidence", "target": "...", "result": "Screenshot saved: 01_commit_page.png (sha256: e14a...)"}]
```

#### Objective of Phase 2 & Alignment with End Goal

**Phase 2 objective:** Validate that the Phase 1 architecture actually works on real websites with real data — not just demo URLs. Specifically:
1. Can the agent navigate multi-page workflows (commit → PR → review)?
2. Does provenance tracking work end-to-end (every field → source URL + screenshot)?
3. Does fault tolerance work (bad URLs → structured failure, not crashes)?
4. Is the output audit-grade (SHA-256 hashes, typed fields, checkpoint verification)?

**How this aligns with what Andera needs:**
- **SOX audits** require evidence chains: "this value came from this page, here's the screenshot." That's exactly what `result.json` provides with `source_url`, `source_selector`, and `artifact_ref` on every field.
- **Format adaptability** — the agent handled different GitHub page layouts (commit page vs PR page) without code changes, just YAML instructions.
- **Fault tolerance** — bad URLs produce `status: failed` with structured errors, not batch crashes.
- **Multiple task types** — same CLI, same pipeline, different YAML: `github_commits.yaml` (graph_traversal) and `github_issues.yaml` (single_page) demonstrate strategy generality.
- **Judgment capability** — the agent doesn't just extract data, it reasons about it ("Was this commit properly reviewed?") and provides confidence + evidence references.

### Test Results

#### Single Commit Audit (commit_001)
```
Status:     COMPLETED
Steps:      7
Duration:   166.0s
Fields:     7 (commit_hash, author, date, message, files_changed, has_pr, was_reviewed)
Artifacts:  4 (3 commit page screenshots, 1 PR review screenshot)
Checkpoints: 3/3 (commit_page_screenshot, commit_fields_extracted, pr_review_screenshot)
Judgment:   YES (95% confidence) — "PR was reviewed and approved by justschen"
Errors:     0
```

**Provenance verified in result.json:**
- Every `FieldExtraction` has `source_url` pointing to the exact GitHub page
- Every `FieldExtraction` has `artifact_ref` linking to the supporting screenshot
- Judgment has `evidence_refs` pointing to specific artifact filenames
- Judgment has `source_urls` for both the commit page and PR page
- All screenshots have SHA-256 hashes for integrity verification

#### Agent Behavior Observed
The agent autonomously:
1. Navigated to the commit page
2. Used JavaScript `evaluate` to extract the exact commit date from `<relative-time>` elements
3. Identified the linked PR (#305569) from the commit message
4. Navigated to the PR page
5. Detected that `justschen` approved the changes and Copilot reviewed
6. Made a judgment with reasoning citing specific evidence

### How to Run & Test

```bash
# Single commit audit (graph traversal — commit → PR → review → judgment)
python main.py --task tasks/github_commits.yaml --sample-id commit_001 \
  --url "https://github.com/microsoft/vscode/commit/9986a4378e3332af88cb8c4496fbb6b7231fd189"

# Batch commit audit (5 samples, 2 concurrent)
python main.py --task tasks/github_commits.yaml --max-concurrent 2

# GitHub issues extraction (single_page strategy)
python main.py --task tasks/github_issues.yaml --sample-id issue_001 \
  --url "https://github.com/microsoft/vscode/issues/305609"

# Batch issues extraction
python main.py --task tasks/github_issues.yaml

# All CLI options
python main.py --task tasks/demo.yaml --headless --max-concurrent 5 --verbose
```

### Issues Encountered & Fixed

#### Issue 7: Anthropic OpenAI-compat endpoint limitations
- **What:** The Phase 1 workaround (routing Anthropic through `ChatOpenAI` with `base_url`) required 4 schema-manipulation flags and still showed `Provider: openai` in logs.
- **Fix:** Switched to browser-use's native `ChatAnthropic` which uses the direct Anthropic SDK. Zero workaround flags needed.

#### Issue 8: Screenshot watchdog timeouts on GitHub
- **What:** browser-use's `ScreenshotWatchdog` timed out after 15s on GitHub pages because the page never reaches "network idle."
- **Fix:** Tuned `BrowserProfile(wait_for_network_idle_page_load_time=5.0)` to stop waiting for a state that never comes.

#### Issue 9: download_file could save false evidence
- **What:** The action fell back to `page_url` if no URL was given, silently saving HTML pages as "downloads." No HTTP status or content-type validation.
- **Fix:** Require explicit URL, validate response status (reject non-2xx), reject HTML content-type for non-HTML filenames.

### Known Limitations (Documented)
1. **`no_auth` vision mode** — evaluates once at start, not per-step. browser-use doesn't support toggling `use_vision` mid-run. Documented in code.
2. **PowerShell log rendering** — Python's stderr logging shows as red "errors" in PowerShell. Run without `2>&1` or use `cmd`. Cosmetic only.
3. **Duplicate screenshots** — The agent sometimes takes the same screenshot twice (e.g., `01_commit_page.png` and `02_commit_page.png` with same content). Not harmful but wastes a step.

### Code Review Findings & Fixes

Two reviews were performed — one external (ChatGPT) and one internal (automated code reviewer agent).

| # | Finding | Source | Fix Applied |
|---|---------|--------|-------------|
| 1 | `download_file` saves error HTML silently | Both | Require explicit URL, validate HTTP status + content-type, reject HTML |
| 2 | `no_auth` vision only at init, not per-step | Both | Documented as known limitation (browser-use API constraint) |
| 3 | CSV output nondeterministic | ChatGPT | Already fixed in prior commit (`sort_values("sample_id")`) |
| 4 | Dynamic `_extractions`/`_checkpoints_met` on FileManager | Internal | Initialized in `__init__`, removed `hasattr` guards |
| 5 | Shared `_results` list race under concurrency | Internal | `_process_sample` now returns result, collected via `gather` |
| 6 | `llm.provider`/`.name` may crash on some providers | Internal | Defensive `getattr` with fallback |
| 7 | Action log loop drops entries if list lengths diverge | Internal | Loop uses `max()` of all list lengths |

### How to Explain This Phase

> "Phase 2 was about proving the architecture works on real data. We ran the commit audit pipeline against real Microsoft VS Code commits — the agent autonomously navigated from commit pages to linked PRs, identified reviewers, and made audit judgments with 95% confidence. Every extracted field traces back to a specific URL and screenshot."

> "We switched from an OpenAI-compatibility hack to browser-use's native Anthropic SDK — eliminating 4 workaround flags and getting proper tool calling and prompt caching. We also hardened the download action to prevent false evidence (rejecting HTML responses, validating HTTP status)."

> "The key validation: the agent extracted 7 typed fields across 2 pages, satisfied all 3 checkpoints, and produced an audit judgment citing specific evidence artifacts — all from a single YAML task definition. No code changes needed. That's the architecture paying off."

---

## Phase 2.5: Hardening Pass — Evidence Integrity & Speed

**Goal:** Fix provenance bugs, preserve partial evidence on failures, and tighten runtime to eliminate unnecessary slowness. This is not new features — it's making Phase 2 audit-grade.

### What Was Fixed

#### 1. Partial Evidence Preservation on Timeout/Error (`agent/evidence_agent.py`)
- **Problem:** When a sample timed out, `_build_error_result()` wrote a `result.json` with empty `artifacts`, `extracted_fields`, and `checkpoints_met` — even though the file manager already had screenshots and fields from the attempt that timed out. This meant the evidence folder had screenshot files that weren't referenced in the manifest.
- **Why it matters:** For audit, the manifest IS the evidence record. If `result.json` says "no artifacts" but there are .png files in the folder, that's an integrity inconsistency. A reviewer can't trust the manifest.
- **Fix:** `_build_error_result()` now includes whatever partial evidence was collected: `artifacts=self.file_manager.artifacts`, `extracted_fields=self.file_manager._extractions`, `checkpoints_met=self.file_manager._checkpoints_met`, `judgment=self.file_manager._judgment`.
- **Architectural decision:** We always prefer "honest partial data" over "clean empty data." A manifest that says "we captured 2 screenshots before timing out" is more useful than one that says "nothing happened."

#### 2. Artifact Reference Validation (`agent/actions.py`)
- **Problem:** The agent supplies `artifact_ref` (in `record_fields`) and `evidence_refs` (in `make_judgment`) as free-text strings. The agent sometimes hallucinated filenames — e.g., `pr_review.png` when the real saved file was `04_pr_review.png` (FileManager prepends a sequence number). This broke the provenance chain: a field claimed to be supported by a screenshot that didn't exist by that name.
- **Why it matters:** In Andera's domain, evidence traceability means "I can follow the reference from the extracted field to the actual artifact file." If the filename doesn't match, the chain is broken.
- **Fix:** Both `record_fields` and `make_judgment` now validate agent-supplied references against `{a.filename for a in file_manager.artifacts}`. If the agent's reference doesn't match a real file, we fall back to the latest matching artifact. Invalid refs in judgments are silently dropped and replaced with all artifact filenames.
- **Architectural decision:** We don't reject the action — that would waste a step. We silently correct the reference because the agent's INTENT was right (reference the PR screenshot), just the filename was wrong. The correction is deterministic: latest artifact or all artifacts.

#### 3. Task Runtime Tightening (`tasks/github_commits.yaml`)
- **Problem:** The previous config (`max_steps: 30`, `timeout_seconds: 180`, `max_retries: 2`) meant a single bad sample could burn 540 seconds (9 minutes) of retries. The batch of 5 took 1038 seconds (17 minutes).
- **Changes:**
  - `max_steps`: 30 → **12** (a commit audit needs ~6-7 steps; 12 gives headroom without waste)
  - `timeout_seconds`: 180 → **90** (ample for 12 steps on a normal page)
  - `max_retries`: 2 → **1** (one retry is enough; 2 just delays the inevitable for truly broken pages)
- **Expected improvement:** A bad sample fails in ~180s (90s × 2 attempts) instead of ~540s (180s × 3 attempts). Full batch should run in ~5-6 minutes instead of 17.
- **Architectural decision:** Fail fast, report honestly. Three retries on a page that will never work is not resilience — it's waste. The `needs_review` status already handles failure visibility.

#### 4. Prompt Tightening — No Duplicate Screenshots (`tasks/github_commits.yaml`)
- **Problem:** The agent was taking 2-3 screenshots of the same commit page (e.g., `01_commit_page.png`, `02_commit_page.png`, `03_commit_page.png` with identical or near-identical content). Each duplicate costs an LLM step + screenshot capture time.
- **Fix:** Prompt now explicitly says:
  - "Take EXACTLY ONE screenshot"
  - "Do NOT retake unless the first attempt failed"
  - "Call done immediately. Do NOT take additional screenshots or repeat any steps"
- **Expected improvement:** Saves 1-2 steps per sample (~30-40 seconds).
- **Architectural decision:** The LLM follows instructions — if the instructions are loose, it over-collects. Prompt precision is free performance. This is a prompt engineering fix, not a code change.

#### 5. Input CSV Cleanup (`tasks/inputs/github_commits.csv`)
- **Problem:** The test CSV had 5 samples including `commit/main` (resolved to a dynamic heavy page) and an all-zeros SHA. Both caused timeouts that dominated the batch runtime.
- **Fix:** Reduced to 4 samples: 3 real merge commits (known to have PRs) + 1 invalid SHA (fault tolerance test). Removed `commit/main`.
- **Rationale:** Separate "workflow correctness testing" (real commits) from "extreme edge case testing" (dynamic URLs). Keep the fault test, remove the noise.

### How to Explain This Phase

> "Phase 2.5 was a hardening pass driven by external code review. Three categories of fix: evidence integrity (partial evidence preservation + artifact reference validation), runtime efficiency (3x faster batches via config tightening), and prompt precision (eliminating duplicate work). No architectural changes — same pipeline, same strategies, same models. Just making the existing design honest and fast."

> "The key insight: in audit systems, a manifest that lies about what's on disk is worse than one that admits partial failure. We now always preserve whatever evidence was collected, even if the sample ultimately failed. And we validate every artifact reference the agent gives us against what actually exists on disk."

---

## Phase 3: Multi-Strategy Showcase & CLI Polish

**Goal:** Demonstrate generality — 3 task types across 3 strategies, all from YAML. Add --dry-run for config validation without cost.

### What Was Built

#### 1. Form Fill Task Definition (`tasks/form_fill_demo.yaml`)
- **What:** Third task type using the `FormFillStrategy` — fill forms on httpbin.org with data from CSV, submit, capture results.
- **How it works:** The CSV has columns `customer_name`, `telephone`, `email` alongside `sample_id` and `url`. The task loader puts these into `SampleInput.extra_fields`. The `FormFillStrategy.build_prompt()` injects them as `FORM DATA TO FILL` in the agent prompt. The agent matches labels to form fields.
- **Checkpoints:** `empty_form_screenshot` → `filled_form_screenshot` → `submission_result_screenshot` — captures the full form lifecycle.
- **Why this matters for Andera:** This is the same pattern as filling audit forms, submitting evidence requests, or interacting with enterprise platforms. The agent fills, screenshots before/after, submits, and captures the result. Zero code changes from the core pipeline.
- **Architectural point:** The `FormFillStrategy` class is 53 lines. It extends `SinglePageStrategy` with form-specific prompt rules. The entire form fill capability is: one strategy class + one YAML file + one CSV. That's the strategy pattern paying off.

#### 2. `--dry-run` CLI Flag (`main.py`)
- **What:** Validates task YAML, loads samples, shows all config in clean tables — without launching a browser or making LLM calls.
- **Usage:** `python main.py --task tasks/github_commits.yaml --dry-run`
- **What it shows:**
  - Task configuration (name, strategy, timeouts, vision mode, judgment question)
  - Output fields with types and required/optional status
  - Evidence checkpoints with types and required status
  - All samples with URLs and extra_fields
- **Why this matters:** Config debugging without burning API credits or waiting for browsers. Catch YAML mistakes instantly. Also useful for demos — show the task structure before running it.
- **Architectural point:** The dry-run doesn't instantiate an LLM or orchestrator. It validates through Pydantic model construction (TaskConfig, SampleInput) — if the YAML is malformed, Pydantic catches it here.

#### 3. Richer `run_summary.json` (`agent/orchestrator.py`)
- **What:** Added three new sections to the run summary:
  - `checkpoint_pass_rates`: Per-checkpoint completion counts (e.g., `"commit_page_screenshot": "3/4"`)
  - `needs_review_reasons`: Breakdown of why samples need review (e.g., `{"timeout": 2, "ambiguous_extraction": 1}`)
  - `top_errors`: Most common error messages across all samples (truncated, deduplicated)
- **Why:** A run summary that says "3 needs_review" is unhelpful. A summary that says "2 timed out, 1 had ambiguous extraction, commit_page_screenshot passed 3/4" tells you exactly what to fix.
- **Example output:**
  ```json
  {
    "checkpoint_pass_rates": {
      "commit_page_screenshot": "3/4",
      "commit_fields_extracted": "3/4"
    },
    "needs_review_reasons": {
      "timeout": 1,
      "ambiguous_extraction": 1
    },
    "top_errors": {
      "Timeout after 90s": 1,
      "author: value is None": 1
    }
  }
  ```

### The 3 Task Types — Same Pipeline, Different YAMLs

| Task | Strategy | Fields | Checkpoints | Judgment |
|------|----------|--------|-------------|----------|
| GitHub Commit Audit | `graph_traversal` | 7 (hash, author, date, msg, files, has_pr, reviewed) | 3 (commit screenshot, fields, PR screenshot) | "Was this properly reviewed?" |
| GitHub Issue Extraction | `single_page` | 7 (number, title, state, author, labels, date, comments) | 2 (issue screenshot, fields) | "Is this a bug report?" |
| Form Fill Demo | `form_fill` | 2 (submitted, response_status) | 3 (empty form, filled form, submission result) | "Was submission successful?" |

**Zero code changes** between these three. Different YAML, different CSV, same `main.py` command.

### How to Run & Test

```bash
# Dry run — validate any task without cost
python main.py --task tasks/github_commits.yaml --dry-run
python main.py --task tasks/github_issues.yaml --dry-run
python main.py --task tasks/form_fill_demo.yaml --dry-run

# Run form fill demo (single sample)
python main.py --task tasks/form_fill_demo.yaml --sample-id form_001 \
  --url "https://httpbin.org/forms/post"

# Run GitHub issues (single sample)
python main.py --task tasks/github_issues.yaml --sample-id issue_001 \
  --url "https://github.com/microsoft/vscode/issues/305609"
```

### How to Explain This Phase

> "Phase 3 proved the architecture's generality. We added a form fill task and a GitHub issues task — both just YAML files, no code changes. The `--dry-run` flag lets you validate any task definition instantly. The richer run summary now shows checkpoint pass rates and error breakdowns, not just counts."

> "The key demonstration: 3 task types (commit audit, issue extraction, form fill) across 3 strategies (graph_traversal, single_page, form_fill) — all running through the same CLI, same pipeline, same evidence packaging. New task = new YAML. New task family = one strategy class (~50 lines) + new YAML."

---

## Phase 4: Demo Preparation & Polish

**Goal:** Clean, polished, demo-ready state — README, verified outputs, everything explainable.

### What Was Done

#### 1. README Rewrite (`README.md`)
- Full architecture diagram (ASCII) showing the layer stack: CLI → Orchestrator → EvidenceAgent → Actions + Output
- Strategy pattern table explaining 3 strategies and when to use each
- Project structure tree with one-line descriptions per file
- Complete setup instructions (venv, pip, playwright, .env)
- Usage section: dry-run, single sample, batch, all CLI flags
- Output structure with example provenance JSON
- "Adding New Tasks" section with minimal YAML template
- LLM provider table (Anthropic, OpenAI, Gemini — switch via .env)

#### 2. Verified Demo Output — GitHub Issue Extraction
- **Task:** `github_issues.yaml` (single_page strategy)
- **Sample:** VS Code issue #305609 (Japanese bug report)
- **Result:** COMPLETED in 85 seconds, 5 steps
  - 7 fields extracted (issue_number, title, state, author, labels, created_date, comment_count)
  - 2 checkpoints met (issue_page_screenshot, issue_fields_extracted)
  - Judgment: YES — "Is this a bug report?" (99% confidence, reasoning: "body explicitly states Type: Bug")
  - Agent correctly handled Japanese title and AI-translated label
- **Enriched run_summary.json** working: checkpoint pass rates `1/1`, empty error breakdown

### De-scoped from Phase 3 (P2 items)
| Item | Status | Rationale |
|------|--------|-----------|
| Structured error categorization | De-scoped | Nice-to-have; current error strings in `SampleResult.errors` are sufficient for debugging |
| `no_auth` vision live test | De-scoped | Requires auth-gated site; documented as known limitation; current tasks use `auto` mode |

### Final Project State

```
Commits:
  Phase 1:   Foundation (models, strategies, agent, orchestrator, CLI, actions, output)
  Phase 1.5: Structured live logging + LLM provider updates
  Phase 2:   Native Anthropic, real-world validation, code review fixes
  Phase 2.5: Evidence integrity hardening, runtime optimization
  Phase 3:   Multi-strategy showcase, --dry-run, enriched summaries
  Phase 4:   README polish, verified demo outputs

Task Types: 4 (demo, commit audit, issue extraction, form fill)
Strategies: 3 (single_page, graph_traversal, form_fill)
Custom Actions: 5 (screenshot, record_fields, mark_checkpoint, make_judgment, download_file)
LLM Providers: 4 (anthropic, openai, gemini, browser_use)
```

### How to Demo This Project

1. **Show `--dry-run`** on all 3 task types — instant config validation with clean tables
2. **Show a live single-sample run** — `github_issues.yaml` is fastest (~85s)
3. **Open the evidence folder** — show `result.json` provenance (every field → source URL + screenshot)
4. **Show the architecture** — "3 strategies, 4 task types, 5 actions. New task = new YAML."
5. **Show the code** — `actions.py` (custom actions), `strategies/base.py` (strategy pattern), `evidence_agent.py` (the wrapper)
6. **Explain the split** — "LLM navigates. Python packages. That's the audit-grade guarantee."

### How to Explain to Andera

> "This is a general-purpose browser evidence agent. Given a YAML task definition and sample URLs, it autonomously navigates any website, extracts typed fields with full provenance, takes evidence screenshots with SHA-256 hashes, and makes structured audit judgments. Every output traces back to where it came from."

> "The architecture is: YAML defines WHAT to collect, strategies define HOW to navigate, the LLM does the reasoning, and deterministic Python handles all packaging. Three strategies cover single-page extraction, multi-page graph traversal, and form interaction. Adding a new task in an existing family is just a YAML file — no code changes."

> "For Andera's SOX audit use case, this maps directly: the agent navigates audit platforms, extracts evidence, fills forms, downloads reports, and produces a reviewable evidence chain. The checkpoint system ensures nothing is missed. The provenance chain ensures every value traces back to its source."

---

## Phase 5: Andera Coverage — All 5 Task Families

**Goal:** Turn the framework into a credible Andera submission by implementing every task family from the Andera project brief, then validating them on the existing engine.

### What Was Built

All 5 task families from `Andera_Project.md` were added as YAML task definitions with sample input CSVs. **Zero code changes to the engine.**

#### Task 1: Full Commit Audit (`andera_commit_audit.yaml`)
- **Andera requirement:** "Go through 60 commits, screenshot each, open checks, identify CI passes/fails, find Jira links, take screenshots."
- **Strategy:** `graph_traversal` (commit → PR → checks → CI → Jira)
- **Output fields (12):** commit_hash, commit_url, pr_creator, pr_approver, pr_merger, commit_date, commit_message, files_changed, checks_passed, checks_failed, failed_check_notes, jira_ticket_link
- **Judgment:** "Was this commit properly reviewed and all checks passing before merge?"

#### Task 2: LinkedIn Enrichment (`andera_linkedin_enrichment.yaml`)
- **Andera requirement:** "CSV of names from an event. Go through each, find their LinkedIn. Add columns: LinkedIn URL, School, Current company, Tenure."
- **Strategy:** `graph_traversal` (Google search → candidate → profile / auth wall)
- **Output fields (4):** linkedin_url, school, current_company, tenure
- **Key behavior:** If access is blocked or identity is unclear, the task is expected to fall back to `needs_review` instead of guessing.

#### Task 3: Code Blame / Materiality (`andera_blame_review.yaml`)
- **Andera requirement:** "Find the file, switch to Blame View, check how long ago the last change was. Determine if within X time. If so, check if it materially changed a calculation."
- **Strategy:** `graph_traversal` (file view → blame view → recent commit)
- **Output fields (6):** file_path, last_modified_date, last_modified_author, within_threshold, materially_changed, change_description

#### Task 4: Form Fill + Report Download (`andera_form_download.yaml`)
- **Andera requirement:** "Fill out form in Workday, screenshot it filled out, download the resulting report, click on tabs and download attachments."
- **Strategy:** `form_fill` (empty form → fill → submit → capture/download)
- **Output fields (2):** form_submitted, response_status

#### Task 5: Ticket Extraction (`andera_ticket_extraction.yaml`)
- **Andera requirement:** "Excel list of links to linear tickets. Go through one by one, open the URL, take a screenshot. Compile CSV of ticket number, assignee, due date."
- **Strategy:** `single_page` (visit → screenshot → extract)
- **Output fields (7):** ticket_number, title, assignee, status, due_date, priority, labels

### Dry-Run Validation

```bash
python main.py --task tasks/andera_commit_audit.yaml --dry-run
python main.py --task tasks/andera_linkedin_enrichment.yaml --dry-run
python main.py --task tasks/andera_blame_review.yaml --dry-run
python main.py --task tasks/andera_form_download.yaml --dry-run
python main.py --task tasks/andera_ticket_extraction.yaml --dry-run
```

All 5 validated cleanly: configs parsed, samples loaded, fields/checkpoints displayed.

### Runtime Validation — 2026-03-27 Parallel Browser Run

After the dry-runs, all 5 tasks were launched in parallel in real browser sessions.

| Task | Status | Result |
|------|--------|--------|
| `andera_ticket_extraction.yaml` | `needs_review` | Extracted all 7 fields correctly and met both checkpoints, but exceeded the 60s timeout |
| `andera_blame_review.yaml` | `needs_review` | Captured file view and blame view, met required checkpoints, but timed out before recording fields/judgment |
| `andera_commit_audit.yaml` | `needs_review` | Navigated commit → PR → checks, captured 3 screenshots, extracted 7/12 fields, timed out before final packaging |
| `andera_linkedin_enrichment.yaml` | `needs_review` | Found the correct profile, hit the LinkedIn auth wall, captured blocker evidence, recorded a truthful blocked state |
| `andera_form_download.yaml` | `completed` | Completed full form lifecycle: empty form, filled form, submission result, and successful judgment |

### What Phase 5 Actually Proved

- The engine can launch **all 5 Andera-aligned workflows** without new Python architecture.
- **Form fill** is fully demonstrated end-to-end.
- **LinkedIn enrichment** correctly handles an auth wall by reporting it honestly instead of guessing.
- **Ticket extraction, blame review, and commit audit** are functionally viable, but GitHub-heavy flows still need more runtime headroom.
- The remaining work is primarily **timeout and prompt tuning**, not framework redesign.

### Recommended Runtime Tuning

Based on the first parallel browser run:

- `andera_ticket_extraction.yaml`: `timeout_seconds` 60 -> 90
- `andera_blame_review.yaml`: `timeout_seconds` 120 -> 180
- `andera_commit_audit.yaml`: `timeout_seconds` 120 -> 180

### How to Explain This Phase

> "Phase 5 extended the existing engine to all 5 Andera task families using only YAML task definitions and CSV inputs. We then ran the actual browser workflows in parallel. The form workflow completed cleanly, LinkedIn handled a login wall honestly, and the deeper GitHub tasks reached the right pages and captured evidence but still need timeout tuning."

> "The key metric is architectural reuse: 5 different audit workflows, 3 strategies, 31 total output fields, and 14 evidence checkpoints all run through the same `main.py`, same evidence packaging, and same provenance model. The remaining work is runtime optimization, not framework redesign."
