# Browser Evidence Agent — Architecture & Implementation Plan

**Date:** March 26, 2026
**Objective:** Build a production-grade, general-purpose browser evidence collection agent for audit workflows
**Time Budget:** 4–6 hours
**Priority Order:** Accuracy > Generability > Scalability > Consistency > Speed

---

## 1. Mission Statement

> Given any task definition and a set of sample inputs, autonomously navigate any website, collect structured evidence (screenshots, extracted data, judgments), and package outputs into reviewable per-sample folders with a consolidated CSV.

This is not a "browser-use demo." This is an **evidence-grade collection pipeline** built with the rigor expected of audit infrastructure.

---

## 2. Tech Stack & Library Responsibilities

| Library | Version | Role | Why This Library |
|---------|---------|------|------------------|
| **browser-use** | 0.12.5 | Browser automation, DOM extraction, agent loop | 84.7k stars, hybrid DOM+vision, 89% WebVoyager, built-in ReAct loop, custom actions API |
| **browser-use LLM** | built-in | LLM provider abstraction (Anthropic, OpenAI, Gemini, browser-use cloud) | Provider-agnostic via `agent/llm.py` factory, supports 4 providers |
| **pydantic** | v2 | Data models, validation, serialization | Type-safe configs, automatic JSON schema, `.model_dump()` for clean serialization |
| **pandas** | latest | CSV read/write, data aggregation | Industry standard for tabular data, robust CSV handling |
| **asyncio** | stdlib | Concurrency, batch orchestration | Native Python async, semaphore-based parallelism |
| **rich** | latest | CLI output, progress bars, live tables | Beautiful terminal UI, progress tracking, structured logging |
| **Pillow** | latest | Screenshot processing, image manipulation | Full-page screenshot stitching, format conversion |
| **python-dotenv** | latest | Environment config, API keys | Secure credential management, `.env` file support |
| **pathlib** | stdlib | File/folder management | Cross-platform paths, clean API |

### What Each Library Does in Our Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                        INPUT LAYER                          │
│  pandas: read task CSV/JSON → pydantic: validate samples    │
└─────────────────┬───────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────┐
│                    ORCHESTRATION LAYER                       │
│  asyncio: parallel sample processing (semaphore-bounded)    │
│  rich: progress bars, live status table                     │
└─────────────────┬───────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────┐
│                     AGENT LAYER                             │
│  browser-use: navigate, interact, extract DOM               │
│  anthropic: LLM reasoning (plan, decide, judge)             │
│  Custom Actions: screenshot, download, judgment, CSV write  │
└─────────────────┬───────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────┐
│                     OUTPUT LAYER                             │
│  pathlib: create per-sample evidence folders                │
│  Pillow: process/save screenshots                           │
│  pandas: write consolidated results CSV                     │
│  pydantic: serialize result.json manifests                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Architecture — Layered & Modular

### 3.1 Design Principles

1. **Task = Config + Strategy** — Simple tasks need only YAML (prompt + schema). Complex tasks (graph traversal, cross-system joins) get a lightweight Python strategy class that defines navigation hooks, validators, and checkpoints. New task *families* need a strategy; new task *instances* within a family need only YAML.
2. **Separation of Concerns** — Navigation logic, evidence packaging, and orchestration are independent layers.
3. **Agentic vs Deterministic Split** — LLM handles navigation/reasoning; pure Python handles file I/O, naming, hashing, validation.
4. **Pluggable Actions** — Custom browser actions registered via decorators, injectable into any task.
5. **Schema-First Outputs** — Every output has a Pydantic model. Every extracted field tracks its source URL, selector, and supporting artifact.
6. **Fail-Safe by Default** — Every sample gets a structured status. Failures become `SampleResult` objects with `needs_review_reason`, never raw exceptions.
7. **Checkpoint-Driven Collection** — Tasks declare required evidence checkpoints. The agent verifies all checkpoints are satisfied before marking a sample complete.

### 3.2 Project Structure

```
browser_use_agent/
├── agent/
│   ├── __init__.py
│   ├── evidence_agent.py      # Core agent wrapper around browser-use
│   ├── orchestrator.py        # Batch processing with concurrency control
│   ├── actions.py             # Custom browser-use actions (@tools.action)
│   └── prompts.py             # System prompt templates (per task type)
│
├── models/
│   ├── __init__.py
│   ├── task.py                # TaskConfig, SampleInput — what to do
│   ├── evidence.py            # EvidenceArtifact, FieldExtraction, SampleResult
│   └── judgment.py            # JudgmentResult — audit decisions
│
├── strategies/                # Task family strategies (stateless, per-sample)
│   ├── __init__.py            # STRATEGY_REGISTRY mapping
│   ├── base.py                # BaseTaskStrategy ABC + validate_result + should_use_vision
│   ├── single_page.py         # Simple: visit one URL, extract, screenshot
│   ├── graph_traversal.py     # Multi-page: follow links, collect at each node
│   └── form_fill.py           # Interactive: fill forms, submit, download
│
├── output/
│   ├── __init__.py
│   ├── file_manager.py        # Per-sample folder creation, artifact saving
│   └── csv_writer.py          # Buffered CSV writer, flush per batch
│
├── tasks/                     # Task definition files (YAML/JSON)
│   ├── linear_tickets.yaml
│   ├── github_commits.yaml
│   ├── linkedin_enrichment.yaml
│   └── README.md              # How to create new task definitions
│
├── config.py                  # Global settings, env loading
├── main.py                    # CLI entry point (argparse or typer)
├── requirements.txt
├── .env.example
└── README.md
```

### 3.3 Layer Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                          CLI / Entry Point                       │
│  main.py — parse args, load task config, invoke orchestrator     │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                       Orchestrator                               │
│  orchestrator.py                                                 │
│  - Reads sample inputs from CSV/JSON                             │
│  - Creates run directory (timestamped)                           │
│  - Spawns EvidenceAgent per sample (bounded by semaphore)        │
│  - Collects SampleResults                                        │
│  - Writes master CSV + run summary                               │
│  - Reports progress via rich                                     │
└──────────────────────────┬───────────────────────────────────────┘
                           │ (one per sample, async)
┌──────────────────────────▼───────────────────────────────────────┐
│                      EvidenceAgent                               │
│  evidence_agent.py                                               │
│  - Wraps browser-use Agent with task-specific config             │
│  - Injects custom actions (screenshot, download, judge)          │
│  - Builds system prompt from task template + sample context      │
│  - Runs agent loop with max_steps and guardrails                 │
│  - Returns structured SampleResult                               │
└───────────┬──────────────────────────────────┬───────────────────┘
            │                                  │
┌───────────▼───────────────┐    ┌─────────────▼───────────────────┐
│    Custom Actions          │    │       Output Manager            │
│  actions.py                │    │  file_manager.py + csv_writer   │
│  - screenshot_evidence()   │    │  - create sample folder         │
│  - download_file()         │    │  - save screenshots with names  │
│  - extract_to_csv()        │    │  - write result.json manifest   │
│  - make_judgment()         │    │  - append to master CSV         │
│  - save_page_content()     │    │  - generate run_summary.json    │
└────────────────────────────┘    └─────────────────────────────────┘
```

---

## 4. Data Models (Pydantic v2)

### 4.1 Task Configuration

```python
class FieldSpec(BaseModel):
    """Declares one output field with its expected type and validation."""
    name: str                           # e.g., "assignee"
    type: Literal["str", "int", "float", "bool", "date", "url"]
    required: bool = True
    description: str = ""               # Helps the agent know what to look for
    pattern: str | None = None          # Optional regex for validation (e.g., r"^\d{4}-\d{2}-\d{2}$")

    def validate_value(self, value: Any) -> tuple[Any, list[str]]:
        """Coerce and validate a value. Returns (coerced_value, errors)."""
        errors = []
        if value is None:
            return None, ["value is None"] if self.required else []
        try:
            if self.type == "int":
                value = int(value)
            elif self.type == "float":
                value = float(value)
            elif self.type == "bool":
                value = str(value).lower() in ("true", "yes", "1")
            elif self.type == "date":
                from dateutil.parser import parse as parse_date
                value = parse_date(str(value)).date().isoformat()
            elif self.type == "url":
                if not str(value).startswith(("http://", "https://")):
                    errors.append(f"Invalid URL: {value}")
            else:
                value = str(value)
        except (ValueError, TypeError) as e:
            errors.append(f"Type coercion failed for {self.name}: {e}")
        if self.pattern and not re.match(self.pattern, str(value)):
            errors.append(f"Pattern mismatch for {self.name}: expected {self.pattern}")
        return value, errors

class Checkpoint(BaseModel):
    """A required evidence collection point — agent must satisfy all."""
    name: str                           # e.g., "commit_page_screenshot"
    evidence_type: EvidenceType         # screenshot | download | extraction
    description: str                    # What this checkpoint proves
    required: bool = True

class TaskConfig(BaseModel):
    """Defines WHAT to do — loaded from YAML/JSON."""
    name: str                          # e.g., "github_commit_audit"
    description: str                   # Human-readable task description
    strategy: str = "single_page"      # Which strategy class to use
    instructions: str                  # Agent system prompt template
    input_file: str                    # Path to CSV/JSON with samples
    input_columns: list[str]           # Required columns in input
    output_fields: list[FieldSpec]     # Typed, validated field declarations
    checkpoints: list[Checkpoint]      # Required evidence collection points
    evidence_types: list[EvidenceType] # screenshot, csv, judgment, download
    max_steps: int = 25               # Agent step limit per sample
    max_retries: int = 2              # Retry on failure
    timeout_seconds: int = 120        # Per-sample timeout
    allowed_domains: list[str] = []   # Domain allowlist (empty = any)
    requires_login: bool = False      # Whether auth is needed
    use_vision: str = "auto"          # "auto" | "always" | "never" | "no_auth"
    judgment_question: str | None = None
```

### 4.2 Sample Input & Evidence Models

```python
class SampleInput(BaseModel):
    """One row of work — parsed from input CSV."""
    sample_id: str
    url: str | None = None
    task_type: str | None = None
    extra_fields: dict[str, Any] = {}

class EvidenceArtifact(BaseModel):
    """One piece of evidence collected — with full provenance."""
    type: EvidenceType                 # screenshot | download | extraction | judgment
    filename: str                      # e.g., "01_ticket_page.png"
    path: str                          # Relative path within sample folder
    description: str                   # What this artifact shows
    source_url: str                    # URL where this was captured
    sha256: str | None = None          # Hash for downloaded files
    timestamp: datetime
    checkpoint_ref: str | None = None  # Which checkpoint this satisfies

class FieldExtraction(BaseModel):
    """One extracted field — tracks WHERE it came from."""
    field_name: str                    # e.g., "assignee"
    value: Any                         # The extracted value
    source_url: str                    # URL where extracted
    source_selector: str | None = None # DOM selector or description
    artifact_ref: str | None = None    # Which artifact supports this
    confidence: float = 1.0            # 0.0–1.0

class NeedsReviewReason(str, Enum):
    """Structured reasons for needs_review status."""
    LOGIN_REQUIRED = "login_required"
    MFA_CAPTCHA = "mfa_or_captcha"
    AMBIGUOUS_EXTRACTION = "ambiguous_extraction"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    CHECKPOINT_NOT_MET = "checkpoint_not_met"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    UNEXPECTED_DOMAIN = "unexpected_domain"
    TIMEOUT = "timeout"

class SampleResult(BaseModel):
    """Complete result for one sample — written as result.json."""
    sample_id: str
    status: SampleStatus               # completed | failed | needs_review | skipped
    needs_review_reasons: list[NeedsReviewReason] = []
    input: SampleInput
    extracted_fields: list[FieldExtraction]  # Typed, with provenance
    artifacts: list[EvidenceArtifact]
    checkpoints_met: list[str]          # Which checkpoints were satisfied
    checkpoints_missed: list[str]       # Which checkpoints were NOT satisfied
    judgment: JudgmentResult | None = None
    action_log: list[ActionLogEntry]    # Structured, not just strings
    errors: list[str]
    started_at: datetime
    completed_at: datetime
    steps_taken: int
    retries_used: int

class ActionLogEntry(BaseModel):
    """Structured action log — not free-text."""
    step: int
    action: str                         # e.g., "navigate", "click", "extract", "screenshot"
    target: str                         # URL or element description
    result: str                         # outcome
    timestamp: datetime
```

### 4.3 Judgment Model

```python
class JudgmentResult(BaseModel):
    """Audit judgment — structured, not free-text."""
    question: str                       # What was asked
    answer: Literal["yes", "no", "inconclusive"]
    confidence: float                   # 0.0–1.0
    reasoning: str                      # Why this judgment
    evidence_refs: list[str]            # Which artifact filenames support this
    source_urls: list[str]              # URLs reviewed to make this judgment
```

---

## 5. Key Components — Detailed Design

### 5.1 Task Strategies (strategies/)

The key insight from review: simple tasks (visit URL, extract fields) and complex tasks (commit→PR→CI graph traversal) need different control logic. Strategies provide that without bloating the core agent.

**Strategies are stateless and instantiated per-sample** to prevent cross-contamination under concurrent execution.

```python
# strategies/base.py
class BaseTaskStrategy(ABC):
    """Defines HOW a task family navigates and validates.

    IMPORTANT: Strategies MUST be stateless. A fresh instance is created
    per sample by the orchestrator. Do not store sample-specific state
    as class attributes.
    """

    @abstractmethod
    def build_prompt(self, task: TaskConfig, sample: SampleInput) -> str:
        """Build the agent prompt for this sample."""

    def should_use_vision(self, task: TaskConfig, current_url: str) -> bool:
        """Whether to use vision for current page.
        Called by EvidenceAgent before each step via register_step_callback."""
        if task.use_vision == "never":
            return False
        if task.use_vision == "always":
            return True
        if task.use_vision == "no_auth":
            # Disable vision on login/auth pages to avoid sending credentials
            login_patterns = ["login", "signin", "auth", "sso", "oauth"]
            return not any(p in current_url.lower() for p in login_patterns)
        # "auto" — DOM-only in v1, no vision fallback
        return False

    def validate_result(self, result: SampleResult, task: TaskConfig) -> SampleResult:
        """Post-process: check checkpoints, validate field types, verify completeness."""
        # 1. Checkpoint verification
        missed = [c.name for c in task.checkpoints
                  if c.required and c.name not in result.checkpoints_met]
        if missed:
            result.checkpoints_missed = missed
            result.status = SampleStatus.NEEDS_REVIEW
            result.needs_review_reasons.append(NeedsReviewReason.CHECKPOINT_NOT_MET)

        # 2. Field presence check
        extracted_names = {e.field_name for e in result.extracted_fields}
        missing_fields = [f.name for f in task.output_fields
                         if f.required and f.name not in extracted_names]
        if missing_fields:
            result.status = SampleStatus.NEEDS_REVIEW
            result.needs_review_reasons.append(NeedsReviewReason.MISSING_REQUIRED_FIELD)

        # 3. Field type validation and coercion
        field_specs = {f.name: f for f in task.output_fields}
        has_required_validation_failure = False
        for extraction in result.extracted_fields:
            spec = field_specs.get(extraction.field_name)
            if spec:
                coerced, errs = spec.validate_value(extraction.value)
                if errs:
                    result.errors.extend(errs)
                    if spec.required:
                        has_required_validation_failure = True
                else:
                    extraction.value = coerced  # coerce in place

        # Invalid required fields = not clean completion
        if has_required_validation_failure and result.status == SampleStatus.COMPLETED:
            result.status = SampleStatus.NEEDS_REVIEW
            result.needs_review_reasons.append(NeedsReviewReason.AMBIGUOUS_EXTRACTION)

        return result

# strategies/single_page.py
class SinglePageStrategy(BaseTaskStrategy):
    """Visit one URL, extract fields, take screenshots. No link following."""

# strategies/graph_traversal.py
class GraphTraversalStrategy(BaseTaskStrategy):
    """Follow links across pages (commit→PR→checks→CI→Jira).
    Prompt instructs agent to track visited URLs and collect evidence at each node.
    State lives in the agent's context window, NOT in the strategy object."""

    def build_prompt(self, task: TaskConfig, sample: SampleInput) -> str:
        base = task.instructions
        graph_context = (
            "\n\nNAVIGATION RULES:\n"
            "- Track every URL you visit. Do not revisit the same page.\n"
            "- At each page, check if any checkpoints can be satisfied.\n"
            "- Follow links to related pages (PRs, reviews, CI) as needed.\n"
            "- If a link leads to an unexpected domain, note it but do not follow.\n"
            f"- Required checkpoints: {[c.name for c in task.checkpoints if c.required]}\n"
        )
        return f"{base}{graph_context}\n\nSample: {sample.model_dump_json()}"

# strategies/form_fill.py
class FormFillStrategy(BaseTaskStrategy):
    """Fill forms, trigger downloads, verify submissions."""
```

**Strategy selection is declared in YAML:**
```yaml
strategy: "graph_traversal"  # maps to GraphTraversalStrategy
```

This means: **simple tasks = YAML only. Complex task families = one strategy class + YAML instances.** Strategies are stateless — all per-sample state lives in the agent's context window and the `SampleResult`.

### 5.2 EvidenceAgent (agent/evidence_agent.py)

The core wrapper — now strategy-aware and checkpoint-tracking.

```python
class EvidenceAgent:
    """Wraps browser-use Agent with evidence collection capabilities."""

    def __init__(self, task_config: TaskConfig, sample: SampleInput,
                 output_dir: Path, llm: BaseChatModel,
                 strategy: BaseTaskStrategy):
        self.task = task_config
        self.sample = sample
        self.llm = llm
        self.strategy = strategy
        self.file_manager = FileManager(output_dir / sample.sample_id)
        self.actions = create_evidence_actions(self.file_manager)

    async def run(self) -> SampleResult:
        """Execute evidence collection with strategy-driven validation."""
        prompt = self.strategy.build_prompt(self.task, self.sample)

        # Vision mode: strategy decides based on task config.
        # "auto" → DOM-only (False), no vision fallback in v1.
        # "no_auth" → False on login pages, True elsewhere.
        # For "no_auth", we register a step callback that re-evaluates per page.
        initial_vision = self.strategy.should_use_vision(
            self.task, self.sample.url or ""
        )

        agent = Agent(
            task=prompt,
            llm=self.llm,
            controller=self.actions,
            max_steps=self.task.max_steps,
            use_vision=initial_vision,
        )

        # For "no_auth" mode: register step callback to toggle vision
        # based on current URL (disable on login pages)
        if self.task.use_vision == "no_auth":
            agent.register_step_callback(self._vision_gate_callback)

        try:
            result = await asyncio.wait_for(
                self._run_with_retries(agent),
                timeout=self.task.timeout_seconds,
            )
        except asyncio.TimeoutError:
            result = self._build_timeout_result()

        # Strategy validates: checkpoints, field types, completeness
        return self.strategy.validate_result(result, self.task)

    async def _vision_gate_callback(self, agent_state):
        """Toggle vision off on login pages, back on elsewhere."""
        current_url = agent_state.current_url or ""
        agent_state.use_vision = self.strategy.should_use_vision(
            self.task, current_url
        )
```

**Key design decisions:**
- Strategy injected per-sample (fresh instance, no shared state)
- Vision mode wired through strategy's `should_use_vision()`, not hardcoded
- `"no_auth"` mode uses step callback to re-evaluate vision per page
- `"auto"` defaults to DOM-first (`False`) — cheaper, faster, no credential leak risk
- Timeout per sample via `asyncio.wait_for`
- `FieldExtraction` with provenance, not `dict[str, Any]`
- Structured `ActionLogEntry`, not `list[str]`

### 5.3 Orchestrator (agent/orchestrator.py)

Fixed: `llm` properly initialized, exceptions caught and wrapped, CSV buffered.

```python
# Strategy registry — maps YAML strategy name to class
STRATEGY_REGISTRY: dict[str, type[BaseTaskStrategy]] = {
    "single_page": SinglePageStrategy,
    "graph_traversal": GraphTraversalStrategy,
    "form_fill": FormFillStrategy,
}

class BatchOrchestrator:
    """Process multiple samples with concurrency control."""

    def __init__(self, task_config: TaskConfig, llm: BaseChatModel,
                 max_concurrent: int = 3, output_base: Path = Path("evidence")):
        self.task = task_config
        self.llm = llm
        self.strategy_cls = STRATEGY_REGISTRY[task_config.strategy]
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.run_dir = self._create_run_dir(output_base)
        self._results: list[SampleResult] = []

    async def run(self, samples: list[SampleInput]) -> BatchResult:
        """Process all samples, return aggregated results."""
        tasks = [self._process_sample(s) for s in samples]
        await asyncio.gather(*tasks)  # exceptions handled inside _process_sample
        # Buffered write: one CSV write for all results, not per-row
        csv_writer = CSVWriter(self.run_dir / "results.csv", self.task.output_fields)
        csv_writer.write_batch(self._results)
        self._write_run_summary()
        return BatchResult(results=self._results, run_dir=self.run_dir)

    async def _process_sample(self, sample: SampleInput) -> None:
        """Process one sample — fresh strategy instance, exceptions wrapped."""
        async with self.semaphore:
            try:
                # Fresh strategy per sample — no cross-contamination
                strategy = self.strategy_cls()
                agent = EvidenceAgent(
                    self.task, sample, self.run_dir / "samples",
                    self.llm, strategy,
                )
                result = await agent.run()
            except Exception as e:
                result = SampleResult(
                    sample_id=sample.sample_id,
                    status=SampleStatus.FAILED,
                    input=sample,
                    extracted_fields=[],
                    artifacts=[],
                    checkpoints_met=[], checkpoints_missed=[],
                    action_log=[], errors=[str(e)],
                    started_at=datetime.now(), completed_at=datetime.now(),
                    steps_taken=0, retries_used=0,
                )
            self._results.append(result)
```

**Key fixes:**
- `self.strategy_cls` stored (the class), not an instance — **fresh instance per sample**
- No shared strategy state between concurrent samples
- `self.llm` explicitly passed in constructor
- Exceptions caught inside `_process_sample`, wrapped as `SampleResult(status=FAILED)`
- CSV written as one batch after all samples complete

### 5.3 Custom Actions (agent/actions.py)

Registered via browser-use's `@controller.action()` decorator.

```python
@controller.action(description="Take evidence screenshot of current page")
async def screenshot_evidence(
    label: str,              # e.g., "ticket_detail_page"
    browser_session: BrowserSession,
) -> ActionResult:
    """Captures screenshot and saves to sample's evidence folder."""
    screenshot = await browser_session.take_screenshot(full_page=True)
    # FileManager returns (path, sha256) for audit provenance
    path, sha256 = file_manager.save_screenshot(screenshot, label)
    return ActionResult(
        extracted_content=f"Screenshot saved: {path} (sha256: {sha256})"
    )

@controller.action(description="Extract structured data from current page")
async def extract_fields(
    fields: dict[str, str],   # {"assignee": "CSS selector or description"}
    browser_session: BrowserSession,
) -> ActionResult:
    """Extracts specified fields from the DOM."""
    # Agent uses DOM tree to locate and extract values
    ...

@controller.action(description="Make audit judgment based on evidence")
async def make_judgment(
    question: str,
    evidence_summary: str,
) -> ActionResult:
    """Returns structured judgment with confidence and reasoning."""
    ...

@controller.action(description="Download file from current page")
async def download_file(
    url: str,
    browser_session: BrowserSession,
) -> ActionResult:
    """Downloads file and saves to evidence folder."""
    ...
```

### 5.4 File Manager (output/file_manager.py)

Pure Python, deterministic, no LLM involved.

```python
import hashlib

class FileManager:
    """Manages per-sample evidence folders. Deterministic, not agentic."""

    def __init__(self, sample_dir: Path):
        self.sample_dir = sample_dir
        self.sample_dir.mkdir(parents=True, exist_ok=True)
        self._artifact_counter = 0

    def save_screenshot(self, data: bytes, label: str) -> tuple[Path, str]:
        """Save screenshot, return (path, sha256_hash)."""
        self._artifact_counter += 1
        filename = f"{self._artifact_counter:02d}_{label}.png"
        path = self.sample_dir / filename
        path.write_bytes(data)
        sha256 = hashlib.sha256(data).hexdigest()
        return path, sha256

    def save_download(self, data: bytes, filename: str) -> tuple[Path, str]:
        """Save downloaded file, return (path, sha256_hash)."""
        path = self.sample_dir / filename
        path.write_bytes(data)
        sha256 = hashlib.sha256(data).hexdigest()
        return path, sha256

    def save_result_manifest(self, result: SampleResult) -> Path:
        path = self.sample_dir / "result.json"
        path.write_text(result.model_dump_json(indent=2))
        return path

    def save_action_log(self, entries: list[ActionLogEntry]) -> Path:
        """Save structured action log entries."""
        path = self.sample_dir / "action_log.json"
        data = [e.model_dump(mode="json") for e in entries]
        path.write_text(json.dumps(data, indent=2, default=str))
        return path
```

### 5.6 CSV Writer (output/csv_writer.py)

```python
class CSVWriter:
    """Buffered CSV writer — writes all results in one pass, not row-by-row."""

    def __init__(self, csv_path: Path, field_specs: list[FieldSpec]):
        self.csv_path = csv_path
        self.columns = ["sample_id", "status"] + [f.name for f in field_specs]

    def write_batch(self, results: list[SampleResult]):
        """Write all results to CSV in one operation."""
        rows = []
        for r in results:
            row = {"sample_id": r.sample_id, "status": r.status.value}
            for field in r.extracted_fields:
                row[field.field_name] = field.value
            rows.append(row)
        df = pd.DataFrame(rows, columns=self.columns)
        df.to_csv(self.csv_path, index=False)
```

**Fix from review:** No more per-row pandas append (expensive, not concurrency-safe). Single `write_batch()` call after all samples complete.

---

## 6. Task Definition System

Tasks are defined as YAML files that reference a **strategy** (the navigation pattern). Simple tasks within an existing strategy family need only YAML. New task families need a strategy class (one-time Python).

### Task Family → Strategy Mapping

| Task Family | Strategy | YAML Only? | Why |
|-------------|----------|------------|-----|
| Linear ticket extraction | `single_page` | Yes | Visit URL, extract, screenshot |
| GitHub commit audit | `graph_traversal` | Yes | Commit → PR → checks → CI (multi-page) |
| Code blame analysis | `graph_traversal` | Yes | File → blame → author (multi-page, different checkpoints) |
| LinkedIn enrichment | `graph_traversal` | Yes | Search → select candidate → extract profile (multi-step lookup) |
| Workday form fill | `form_fill` | Yes | Fill fields, submit, download artifacts |

**3 strategy classes cover all 5 Andera task types.**

### Why 3 Strategies, Not 5 — Decision Rationale

This was a deliberate architectural choice, not a shortcut. Here's the reasoning:

**The core insight:** `graph_traversal` with sub-hooks (configurable via YAML checkpoints and instructions) handles commit audit, blame analysis, AND LinkedIn enrichment — they're all "follow links, collect at each node" patterns with different checkpoints. The navigation *pattern* is identical: start at a URL, follow links to related pages, extract data at each stop, take evidence screenshots along the way. What differs is *which* links to follow and *what* to collect — and that's exactly what YAML checkpoints and instructions control.

**Specific examples:**
- **Commit audit:** commit page → linked PR → review status → CI checks. Each stop has different checkpoints, but the agent is always "follow link, collect, move on."
- **LinkedIn enrichment:** search page → candidate list → select profile → extract fields. Same pattern — the agent follows links through a sequence of pages, collecting at each node.
- **Code blame analysis:** file page → blame view → author lookup. Again: navigate, collect, follow link, collect.

**Why not a `cross_system_lookup` strategy?** A cross-system lookup (e.g., GitHub commit → Jira ticket) is just `graph_traversal` where links happen to cross domains. The navigation logic is identical — the agent follows a link. Whether that link stays on github.com or jumps to jira.atlassian.net is irrelevant to the strategy; it's just a different URL. The `allowed_domains` YAML field already handles domain scoping.

**Why not 5 thin strategies?** Creating one strategy per task would mean each strategy is ~90% identical to the others, with the only differences being task-specific prompt fragments — which already live in YAML. That's task-specific code wearing a strategy hat, not real abstraction. It violates DRY and makes the codebase harder to reason about, not easier.

**The escape hatch:** If during implementation we discover a task that genuinely needs navigation logic that `graph_traversal` can't express via YAML checkpoints and prompt instructions alone, we split out a new strategy. The strategy registry makes this a clean addition — add a new class, register it, reference it in YAML. No refactoring needed. But we don't do this speculatively.

**For the 4-6 hour time constraint:** 3 well-tested strategies with rich YAML configurability is strictly better than 5 thin, undertested strategies. We can always split later; we can't easily merge prematurely-split strategies back together.

**How to expand later if we have more time:**
1. If a task needs fundamentally different navigation (e.g., pagination, infinite scroll, tab management), add a new strategy class (~50 LOC)
2. If a `graph_traversal` task needs task-specific validation beyond what `FieldSpec.validate_value()` provides, override `validate_result()` in a subclass
3. If we find 3+ tasks sharing the same non-trivial navigation sub-pattern, extract it as a new strategy family
4. The strategy registry is just a dict — adding a new entry is one line

### Example: `tasks/github_commits.yaml`

```yaml
name: github_commit_audit
description: "Audit GitHub commits for authorship and review compliance"
strategy: graph_traversal          # ← selects navigation pattern

instructions: |
  For each commit URL provided:
  1. Navigate to the commit page on GitHub
  2. Take a screenshot of the commit details
  3. Extract: commit hash, author, date, commit message, files changed count
  4. Check if the commit has an associated PR (look for linked PR reference)
  5. If there is a linked PR, navigate to it and check if it was reviewed
  6. Make a judgment: "Was this commit properly reviewed before merge?"
  7. Take a screenshot of the PR review status (if applicable)

input_file: "inputs/commits.csv"
input_columns: ["sample_id", "url"]

output_fields:
  - name: commit_hash
    type: str
    required: true
  - name: author
    type: str
    required: true
  - name: date
    type: date
    required: true
  - name: message
    type: str
    required: true
  - name: files_changed
    type: int
    required: false
  - name: has_pr
    type: bool
    required: true
  - name: was_reviewed
    type: bool
    required: false

checkpoints:
  - name: commit_page_screenshot
    evidence_type: screenshot
    description: "Screenshot of the commit detail page"
    required: true
  - name: pr_review_screenshot
    evidence_type: screenshot
    description: "Screenshot of PR review status (if PR exists)"
    required: false
  - name: commit_fields_extracted
    evidence_type: extraction
    description: "All required commit fields extracted"
    required: true

evidence_types: ["screenshot", "csv", "judgment"]
max_steps: 30
use_vision: auto
judgment_question: "Was this commit properly reviewed before merge?"
```

### How It Works

```
tasks/github_commits.yaml
         │
         ▼
    TaskConfig (Pydantic)           ← validated, typed fields + checkpoints
         │
         ▼
    resolve_strategy("graph_traversal") → GraphTraversalStrategy
         │
         ▼
    strategy.build_prompt(task, sample) ← task instructions + strategy-specific context
         │
         ▼
    EvidenceAgent.run()             ← browser-use agent with custom actions
         │
         ▼
    strategy.validate_result()      ← checkpoints met? required fields present?
         │
         ▼
    SampleResult                    ← structured, validated, with provenance
```

This means:
- **New task in existing family = YAML file only.** No Python changes.
- **New task family = one strategy class + YAML.** ~50 lines of Python.
- **Output schema is declared and validated**, not inferred.
- **Checkpoints enforce evidence completeness** — the agent can't mark success without collecting what's required.

---

## 7. Evidence Output Structure

```
evidence/
└── run_2026-03-26_153000/
    ├── samples/
    │   ├── sample_001/
    │   │   ├── 01_commit_page.png
    │   │   ├── 02_pr_review.png
    │   │   ├── result.json          ← SampleResult serialized
    │   │   └── action_log.json      ← chronological agent actions
    │   ├── sample_002/
    │   │   ├── 01_commit_page.png
    │   │   ├── result.json
    │   │   └── action_log.json
    │   └── sample_003/
    │       └── ...
    ├── results.csv                  ← master CSV (all samples, all fields)
    └── run_summary.json             ← aggregate stats
```

### run_summary.json

```json
{
  "run_id": "run_2026-03-26_153000",
  "task": "github_commit_audit",
  "total_samples": 25,
  "completed": 22,
  "failed": 2,
  "needs_review": 1,
  "avg_steps_per_sample": 12.4,
  "total_duration_seconds": 340,
  "avg_duration_per_sample_seconds": 13.6
}
```

---

## 8. Error Handling & Fault Tolerance

### Strategy: Capture, Don't Crash

| Failure Type | Handling |
|-------------|----------|
| Page load timeout | Retry up to `max_retries`, then mark `failed` |
| Element not found | Agent retries with alternative selectors via DOM tree |
| Login/MFA required | Mark `needs_review`, save screenshot of blocker |
| Unexpected page structure | Agent reasons about DOM, adapts approach |
| Agent stuck in loop | browser-use `ActionLoopDetector` breaks cycle |
| Max steps exceeded | Mark `needs_review`, save partial results |
| Network error | Retry with exponential backoff |
| LLM API error | Retry once, then fail sample (not entire batch) |

### Key Principle

**A failed sample never kills the batch.** Each sample is independent. Errors are captured in `SampleResult.errors` and the sample gets an appropriate status.

---

## 9. Guardrails & Safety

```python
# Browser-use agent config
agent = Agent(
    max_steps=30,                    # Hard limit per sample
    use_vision=False,                # Default: DOM-only (cheaper, faster)
                                     # Strategy enables vision selectively
    max_actions_per_step=3,          # Prevent action spam
    tool_calling_method="auto",      # Let browser-use optimize
)

# Browser config
browser = Browser(
    config=BrowserConfig(
        headless=False,              # Visible for demo (configurable)
        disable_security=False,      # Keep security defaults
        new_context_config=BrowserContextConfig(
            no_viewport=False,
            save_downloads_path="./downloads",
        ),
    )
)
```

### Vision Mode Strategy

| Mode | Initial `use_vision` | Per-Step Behavior | Use Case |
|------|----------------------|-------------------|----------|
| `"auto"` | `False` | DOM-only for all steps. No vision fallback. | Default — cheapest, fastest, no credential leak risk |
| `"always"` | `True` | Screenshots at every step, no toggling | Visual-heavy / complex layouts |
| `"never"` | `False` | DOM only, no toggling | Cost-sensitive, simple tasks |
| `"no_auth"` | Depends on URL | Step callback re-evaluates: `False` on login URLs, `True` elsewhere | Auth-gated enterprise sites |

**How it's wired:** `strategy.should_use_vision()` is called by `EvidenceAgent` to set the initial value. For `"no_auth"` mode, a step callback (`_vision_gate_callback`) re-evaluates before each agent step based on the current URL, matching against login patterns (`login`, `signin`, `auth`, `sso`, `oauth`).

**Why this matters:** Vision + login = risk of sending credentials in screenshots to the LLM. The step callback ensures vision is disabled specifically during credential entry, not globally.

**Additional guardrails:**
- Domain allowlist per task (optional)
- Timeout per sample (not just per step) — `asyncio.wait_for`
- No automatic form submission without explicit task instruction
- All downloaded files saved to controlled directory
- SHA-256 hash for all downloaded artifacts (audit provenance)

---

## 10. Implementation Plan — Phased Build

### Phase 1: Foundation — COMPLETED

**Goal:** Models, output layer, and skeleton that runs end-to-end for one sample.

| Step | What | File | Status |
|------|------|------|--------|
| 1.1 | Project setup: `requirements.txt`, `.env.example`, `config.py` | config files | DONE |
| 1.2 | Pydantic models: `TaskConfig`, `FieldSpec`, `Checkpoint`, `SampleInput`, `FieldExtraction`, `EvidenceArtifact`, `SampleResult`, `ActionLogEntry`, `JudgmentResult` | `models/` | DONE |
| 1.3 | `BaseTaskStrategy` ABC + `SinglePageStrategy` + `GraphTraversalStrategy` + `FormFillStrategy` + strategy registry | `strategies/` | DONE |
| 1.4 | `FileManager` — create folders, save screenshots with SHA-256, write manifests, retry cleanup | `output/file_manager.py` | DONE |
| 1.5 | `CSVWriter` — buffered batch write, deterministic sort by sample_id | `output/csv_writer.py` | DONE |
| 1.6 | `EvidenceAgent` — wrap browser-use with strategy, retry logic, checkpoint tracking, step callback | `agent/evidence_agent.py` | DONE |
| 1.7 | `main.py` — CLI with `--task`, `--sample-id`, `--url`, `--headless`, `--max-concurrent`, `--verbose` | `main.py` | DONE |
| 1.8 | Custom actions: `screenshot_evidence`, `record_fields`, `mark_checkpoint`, `make_judgment`, `download_file` | `agent/actions.py` | DONE |
| 1.9 | Task YAML loader with `FieldSpec` + `Checkpoint` validation | `agent/task_loader.py` | DONE |
| 1.10 | `BatchOrchestrator` with semaphore, exception wrapping, run summary, structured live logging | `agent/orchestrator.py` | DONE |
| 1.11 | Provider-agnostic LLM factory (Anthropic, OpenAI, Gemini, browser-use) | `agent/llm.py` | DONE |
| 1.12 | Checkpoint verification + field validation in `strategy.validate_result()` | `strategies/base.py` | DONE |

**Milestone:** ACHIEVED — `python main.py --task tasks/demo.yaml --sample-id test_005 --url https://github.com/browser-use/browser-use` creates evidence folder with screenshots, `result.json` with full provenance, `action_log.json`, master CSV, and `run_summary.json`. Batch mode works with concurrency, fault isolation, and color-coded live progress logging.

**What shipped ahead of the original plan:**
- Phase 2 items (actions, strategies, graph traversal, checkpoint verification) were built in Phase 1
- Phase 3 items (orchestrator, progress logging, CSV, run summary) were built in Phase 1
- Structured live logging with per-step callbacks added (not in original plan)
- Provider-agnostic LLM factory with 4 providers added (not in original plan)

---

### Phase 2: Real-World Validation & Graph Traversal Test (NEXT)

**Goal:** Prove the agent works on real multi-page workflows with actual data extraction and audit judgments.

| Step | What | File | Priority |
|------|------|------|----------|
| 2.1 | Run GitHub commit audit end-to-end: commit → PR → review → judgment. Fix any issues with graph traversal on real pages. | `tasks/github_commits.yaml` + manual test | P0 |
| 2.2 | Batch test: 3-5 real commit URLs processed concurrently. Verify fault isolation (mix of public commits, 404s, private repos). | `tasks/inputs/github_commits.csv` | P0 |
| 2.3 | Fix Anthropic provider — currently using OpenAI-compat endpoint (`base_url`) which has JSON schema limitations. Test if direct Anthropic SDK works better with browser-use, or keep current approach if stable. | `agent/llm.py` | P1 |
| 2.4 | Add a second real task: public website data extraction (e.g., Hacker News top stories, Wikipedia infobox, or product page scraping) using `single_page` strategy. | `tasks/` + `tasks/inputs/` | P1 |
| 2.5 | Handle the screenshot timeout issue: browser-use's `ScreenshotWatchdog` times out on JS-heavy pages (GitHub). Either increase timeout via `BrowserProfile` config or catch gracefully. | `agent/evidence_agent.py` | P1 |
| 2.6 | Validate `result.json` provenance chain: every extracted field has `source_url`, `source_selector`, and `artifact_ref` populated correctly. Fix gaps. | manual review | P0 |

**Milestone:** GitHub commit audit runs on 5 real URLs. Agent follows commit → PR links, extracts typed fields, makes audit judgment. At least 3/5 samples complete successfully with full provenance. Failed samples have structured error reasons, not crashes.

---

### Phase 3: Multi-Strategy Showcase & Polish

**Goal:** Demonstrate generality — same pipeline, different task types, different strategies.

| Step | What | File | Priority |
|------|------|------|----------|
| 3.1 | Form fill task YAML + demo: use a public form site (httpbin forms, or a test form) to demonstrate `FormFillStrategy` with `extra_fields` data mapping. | `tasks/form_fill_demo.yaml` + `tasks/inputs/` | P1 |
| 3.2 | Add `--dry-run` flag: validate task YAML, list samples, show strategy selection, but don't launch browser. Useful for config debugging. | `main.py` | P2 |
| 3.3 | Richer `run_summary.json`: add checkpoint pass rates, `needs_review` reason breakdown, per-strategy stats. | `agent/orchestrator.py` | P2 |
| 3.4 | Error categorization: distinguish network errors, LLM errors, browser crashes, and page-not-found in `SampleResult.errors`. Add structured error types. | `models/evidence.py` | P2 |
| 3.5 | Vision mode `"no_auth"` live test: use a site that has a login page to verify vision is disabled during auth and re-enabled after. | `strategies/base.py` + manual test | P2 |

**Milestone:** 3 task types across 3 strategies all work. `--dry-run` validates config without cost. Run summary shows pass rates and error breakdown.

---

### Phase 4: Demo Preparation

**Goal:** Clean, polished, demo-ready state for Andera presentation.

| Step | What | Priority |
|------|------|----------|
| 4.1 | Prepare 3 demo input CSVs with real, working sample URLs per task type | P0 |
| 4.2 | Run full batch for primary demo task (GitHub commit audit, 5 samples) — save output as reference | P0 |
| 4.3 | Run single-page demo (show simplest case works cleanly) | P0 |
| 4.4 | Verify evidence folder structure + provenance in `result.json` for all demos | P0 |
| 4.5 | README with setup instructions, demo walkthrough, and architecture overview | P1 |
| 4.6 | Record or screenshot the terminal output showing structured progress logs, batch summary, and results table | P1 |
| 4.7 | Quick smoke test: all task types, all strategies, headless + headed modes | P1 |

**Milestone:** Ready to present. 3 demo tasks prepared, evidence folders populated, README written, terminal output looks professional.

---

## 11. Extensibility Points

The architecture is designed so that **future capabilities are additions, not modifications:**

| Future Need | Extension Point | What to Add |
|-------------|-----------------|-------------|
| New task (same family) | `tasks/` folder | New YAML file only |
| New task family | `strategies/` + `tasks/` | New strategy class (~50 LOC) + YAML |
| New evidence type | `EvidenceType` enum + action | New action in `actions.py` |
| New output format | `output/` module | New writer class |
| Authentication | `BrowserContextConfig` | Login action + `sensitive_data` |
| Document parsing | `actions.py` | New action wrapping document parser |
| API-based extraction | `actions.py` | Non-browser action for API calls |
| Custom validation | Strategy `validate_result()` | Override in strategy subclass |
| Web UI | Separate service | FastAPI + SSE reading `run_summary.json` |
| MCP integration | MCP server | Expose agent as MCP tool |

---

## 12. What Makes This Exceptional (Andera-Specific)

| Andera Value | How We Deliver |
|-------------|----------------|
| **Fault tolerance** | Per-sample isolation, exceptions wrapped as structured results, never crash the batch |
| **Evidence-grade outputs** | `FieldExtraction` with source URL + selector provenance, SHA-256 on downloads, checkpoint verification |
| **Format adaptability** | Strategy pattern — 3 strategies cover 5 task families; new instances = YAML only |
| **Scalability** | Async orchestration, bounded concurrency, buffered CSV writes, no shared state |
| **Novel eval thinking** | Checkpoint pass rates, `needs_review_reason` breakdown, field completeness metrics |
| **Production mindset** | Typed field specs, structured action logs, vision safety modes, review boundaries |
| **Agentic + deterministic split** | LLM navigates; strategies validate; Python packages — matching audit needs |

---

## 13. Demo Script (for Andera presentation)

1. **Show the task YAML** — "Here's how we define a task: typed fields, checkpoints, strategy selection."
2. **Run single sample** — `python main.py --task tasks/github_commits.yaml --sample sample_001`
3. **Show evidence folder** — Screenshots, `result.json` with field provenance (source URL, selector, artifact ref)
4. **Show checkpoint verification** — "The agent verified it captured all required evidence before marking complete."
5. **Run batch** — `python main.py --task tasks/github_commits.yaml` (5 samples)
6. **Show fault tolerance** — A failed sample has `status: failed` with structured errors, not a crash
7. **Show master CSV** — All extracted fields, all samples, typed columns
8. **Switch strategy** — Same command, different YAML, different strategy → different navigation pattern, same pipeline
9. **Show architecture** — "3 strategy classes cover all 5 task types. This is one subsystem in a larger audit evidence pipeline."

---

## 14. Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| browser-use version issues | Pin exact version in requirements.txt |
| Website blocks automation | Use stealth browser config, realistic user agent |
| LLM costs during demo | Use Claude Sonnet (not Opus) for speed + cost |
| Time overrun | Phases are independent — Phase 1-3 is a complete MVP |
| Auth-gated sites | Demo with public sites; show auth capability with `sensitive_data` + `no_auth` vision mode |
| Flaky navigation | Max retries + `needs_review` status — honest about limitations |

---

## 15. Non-Goals (Explicit Scope Boundaries)

- **Not building:** Login/auth system, user management, or API gateway
- **Not building:** Full document intelligence (Excel parsing, PDF reasoning)
- **Not building:** Multi-agent planner/navigator hierarchy (browser-use handles this internally)
- **Not building:** Custom LLM fine-tuning or model training
- **Not building:** Production deployment infrastructure (Docker, CI/CD)
- **Not building:** Full web UI (CLI-first, UI is a future extension)

---

## 16. Architecture Decision Log (ADL)

Every major decision with the reasoning behind it. This is the section to reference when explaining "why did you do it this way?" in reviews or demos.

### ADL-1: Why browser-use, not raw Playwright/CDP

**Decision:** Use browser-use (v0.12.5) as the browser automation layer, not raw Playwright or Selenium.

**Why:** browser-use gives us a production-tested ReAct agent loop, hybrid DOM+vision extraction, action loop detection, and 89% WebVoyager benchmark accuracy — out of the box. Writing our own agent loop on raw Playwright would consume 60-70% of our time budget and produce something less reliable. We're building an evidence *pipeline*, not a browser automation *framework*.

**Trade-off:** We depend on browser-use's abstractions. If their Agent API changes, we need to adapt. Mitigated by pinning the version and wrapping it behind our own `EvidenceAgent` — we never expose browser-use types to the rest of the codebase.

### ADL-2: Why 3 strategy classes, not 1 or 5

**Decision:** 3 strategies (`single_page`, `graph_traversal`, `form_fill`) cover all 5 Andera task types.

**Why:**
- **Not 1:** A single "do everything" strategy would need massive branching logic to handle single-page extraction vs multi-page graph traversal vs form submission. That's a god-class.
- **Not 5:** One strategy per Andera task (commit audit, blame analysis, LinkedIn, Linear, Workday) would mean 5 classes that are ~90% identical. The only differences would be prompt fragments and checkpoint lists — which already live in YAML. That's task-specific code disguised as strategy code.
- **Why 3 works:** The split maps to genuinely different *navigation patterns*: (a) visit one page and extract, (b) follow links across pages collecting at each node, (c) fill forms and trigger side-effects. These are the three fundamentally different ways you interact with a website. Everything else is configuration — which pages, which fields, which checkpoints.

**Expand later:** If a task needs fundamentally different navigation (infinite scroll, tab management, pagination), add a 4th strategy. The registry makes this a one-line addition. But don't split prematurely.

### ADL-3: Why graph_traversal handles LinkedIn AND commit audit AND blame

**Decision:** These three task types all use the `graph_traversal` strategy with different YAML configurations.

**Why:** They share the identical navigation pattern — start at a URL, follow links to related pages, extract data at each stop, take evidence screenshots along the way:
- **Commit audit:** commit page → linked PR → review status → CI checks
- **LinkedIn enrichment:** search page → candidate list → select profile → extract fields
- **Code blame analysis:** file page → blame view → author lookup → materiality check

What differs is *which* links to follow and *what* to collect at each stop. That's exactly what YAML checkpoints and prompt instructions control. The strategy provides the navigation skeleton; YAML fills in the specifics.

**What about cross-system lookups?** A cross-system lookup (e.g., GitHub → Jira) is just `graph_traversal` where a link happens to cross domains. The agent doesn't care whether a link stays on github.com or jumps to jira.atlassian.net — it follows the link either way. The `allowed_domains` YAML field provides domain scoping if needed.

### ADL-4: Why strategies are stateless and per-sample

**Decision:** Strategy instances are created fresh per sample. They must not store any sample-specific state.

**Why:** The orchestrator runs samples concurrently under a semaphore. If strategies held state (e.g., "visited URLs" for graph traversal), concurrent samples would cross-contaminate each other. A strategy created for sample A tracking URL visits would be corrupted by sample B running in parallel.

**Where does per-sample state live then?** In two places: (a) the browser-use agent's context window (it naturally tracks where it's been via conversation history), and (b) the `SampleResult` being built up during execution. Both are scoped to a single sample by construction.

### ADL-5: Why agentic navigation but deterministic packaging

**Decision:** LLM decides how to navigate and what to extract. Pure Python (no LLM) handles file naming, hashing, folder structure, CSV writing, and result validation.

**Why:** Andera's domain is audit. Auditors need *consistency*. If the same evidence is collected twice, the output must be identical — same folder structure, same file naming convention, same CSV column order, same hash values. LLMs are non-deterministic by nature. Using an LLM to name files or structure output would mean different runs produce different artifact names for the same evidence, which destroys audit traceability.

**The split:** The agent is allowed to be creative in *how it gets there* (retry with different selectors, reason about unexpected page layouts, decide which links to follow). But *what it produces* goes through a deterministic pipeline: `FileManager` handles naming, `CSVWriter` handles tabular output, `SampleResult` enforces a typed schema. The strategy's `validate_result()` is the last gate — it checks completeness and coerces types, deterministically.

### ADL-6: Why checkpoints exist

**Decision:** Tasks declare required checkpoints (screenshots, extractions) that must be satisfied before a sample can be marked "completed."

**Why:** Without checkpoints, the agent marks itself done when it runs out of instructions — but "the agent thinks it's done" is not the same as "all required evidence was collected." An agent might extract 4 of 5 required fields and decide it's finished. Checkpoints make evidence completeness machine-verifiable, not agent-opinion-based.

**How it prevents false positives:** `validate_result()` compares `checkpoints_met` against `task.checkpoints`. If a required checkpoint is missing, the sample is downgraded from `completed` to `needs_review` with a structured reason (`CHECKPOINT_NOT_MET`). This is the "human review boundary" — the system is honest about what it couldn't verify.

### ADL-7: Why field extraction has provenance (source_url, selector, artifact_ref)

**Decision:** Every extracted field (`FieldExtraction`) records which URL it was extracted from, what DOM selector was used, and which artifact supports it.

**Why:** In audit work, "the value is 42" is insufficient. The auditor needs to know "the value is 42, extracted from https://github.com/.../commit/abc123, from the element matching '.commit-author', supported by screenshot 01_commit_page.png." This is the difference between a demo and an audit tool. Provenance lets a human reviewer trace any extracted value back to its source without re-running the agent.

### ADL-8: Why vision defaults to OFF ("auto" = False), not ON

**Decision:** The `"auto"` vision mode defaults to `False` (DOM-only). Vision is only enabled when explicitly requested or when using `"always"` / `"no_auth"` modes.

**Why:**
1. **Cost:** Vision (screenshot analysis) costs ~5-10x more per step than DOM-only extraction. For a batch of 25 samples at 15 steps each, that's 375 vision calls vs 0.
2. **Speed:** Sending screenshots adds latency to every step.
3. **Security:** Screenshots can capture sensitive data (credentials, PII) and send them to the LLM API.
4. **Accuracy:** browser-use's DOM extraction (accessibility tree) is already 89% accurate on WebVoyager. Vision is a fallback, not a default.

**When to turn it on:** Tasks with complex visual layouts (charts, images, non-standard DOM) should use `"always"` in their YAML config. Auth-gated sites should use `"no_auth"` (vision on, but disabled during login flows via step callback). `"auto"` means DOM-only — if a task consistently fails with DOM-only, upgrade it to `"always"` in the task YAML. There is no runtime fallback from DOM to vision in v1; that's a v2 feature if needed.

---

## Ready for Implementation

This plan delivers a clean, modular, production-minded browser evidence agent in 4-6 hours. The architecture separates concerns cleanly, the strategy pattern shows senior engineering judgment, and the evidence-grade output with provenance matches Andera's audit domain perfectly.

Every major decision is documented with reasoning in the ADL (Section 16). When asked "why did you do it this way?" — point to the specific ADL entry.

**Next step:** Your review. Once approved, we begin Phase 1.
