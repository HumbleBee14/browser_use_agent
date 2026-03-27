# Architecture Diagrams — Browser Evidence Agent

Visual explanations of how the application works, for demos and code reviews.

---

## 1. High-Level Flow — What Happens When You Run a Task

```mermaid
flowchart TD
    A["python main.py --task tasks/commits.yaml"] --> B[Load Task YAML]
    B --> C[Load Samples from CSV]
    C --> D{--dry-run?}
    D -->|Yes| E[Show Config Tables & Exit]
    D -->|No| F[Create LLM Instance]
    F --> G[BatchOrchestrator]
    G --> H["Spawn EvidenceAgent per sample<br/>(bounded by semaphore)"]
    H --> I["browser-use Agent<br/>navigates, extracts, screenshots"]
    I --> J[Strategy validates result<br/>checkpoints + field types]
    J --> K[Save to evidence folder<br/>result.json + screenshots + action_log]
    K --> L[Write master results.csv]
    L --> M[Write run_summary.json]
    M --> N["Print batch summary table"]

    style A fill:#1a73e8,color:#fff
    style E fill:#34a853,color:#fff
    style I fill:#ea4335,color:#fff
    style N fill:#34a853,color:#fff
```

---

## 2. Agentic vs Deterministic Split — The Core Design Decision

```mermaid
flowchart LR
    subgraph AGENTIC["🤖 LLM Decides (Non-deterministic)"]
        direction TB
        A1[Which page to visit next]
        A2[What fields to extract]
        A3[Which links to follow]
        A4[How to fill form fields]
        A5[Audit judgment reasoning]
    end

    subgraph DETERMINISTIC["⚙️ Python Handles (Deterministic)"]
        direction TB
        D1[File naming: 01_commit_page.png]
        D2[SHA-256 hash computation]
        D3[Folder structure creation]
        D4[CSV column ordering]
        D5[Checkpoint verification]
        D6[Field type coercion]
    end

    AGENTIC -->|"raw data"| DETERMINISTIC
    DETERMINISTIC -->|"audit-grade output"| OUT[Evidence Folder]

    style AGENTIC fill:#fff3e0,stroke:#ff9800
    style DETERMINISTIC fill:#e8f5e9,stroke:#4caf50
    style OUT fill:#e3f2fd,stroke:#2196f3
```

**Why this matters:** Same evidence collected twice = same files, same names, same hashes. The LLM can be creative in HOW it gets there, but the output is always consistent. This is critical for audit work.

---

## 3. Strategy Pattern — How Task Types Are Handled

```mermaid
flowchart TD
    YAML["Task YAML<br/>strategy: graph_traversal"] --> REG["Strategy Registry<br/>strategies/__init__.py"]

    REG --> SP["SinglePageStrategy<br/>Visit → Extract → Screenshot"]
    REG --> GT["GraphTraversalStrategy<br/>Follow links across pages"]
    REG --> FF["FormFillStrategy<br/>Fill → Submit → Capture"]

    SP --> |"demo.yaml<br/>github_issues.yaml"| AGENT[EvidenceAgent]
    GT --> |"github_commits.yaml"| AGENT
    FF --> |"form_fill_demo.yaml"| AGENT

    AGENT --> RESULT[SampleResult]

    style YAML fill:#e8eaf6,stroke:#3f51b5
    style SP fill:#e3f2fd,stroke:#2196f3
    style GT fill:#e8f5e9,stroke:#4caf50
    style FF fill:#fff3e0,stroke:#ff9800
    style RESULT fill:#fce4ec,stroke:#e91e63
```

**Key point:** New task in existing family = YAML only. New task family = one strategy class + YAML.

---

## 4. Evidence Collection Pipeline — One Sample, End to End

```mermaid
sequenceDiagram
    participant CLI as main.py
    participant Orch as Orchestrator
    participant Agent as EvidenceAgent
    participant BU as browser-use Agent
    participant LLM as Claude API
    participant FM as FileManager

    CLI->>Orch: run(samples)
    Orch->>Agent: run() [per sample]
    Agent->>Agent: strategy.build_prompt()
    Agent->>BU: Agent(task=prompt, llm=claude)

    loop Each Step (max 12)
        BU->>LLM: Page DOM + conversation → what to do?
        LLM-->>BU: Actions: screenshot, record_fields, etc.
        BU->>FM: screenshot_evidence("commit_page")
        FM-->>BU: 01_commit_page.png (SHA-256: abc...)
        BU->>FM: record_fields({"author": "benibenj"})
        FM-->>BU: Field recorded with source_url provenance
    end

    BU-->>Agent: AgentHistoryList
    Agent->>Agent: strategy.validate_result()
    Agent->>FM: save_result_manifest()
    Agent-->>Orch: SampleResult
    Orch->>Orch: Write CSV + run_summary.json
    Orch-->>CLI: BatchResult
```

---

## 5. Data Flow — From YAML to Evidence Folder

```
INPUT                          PROCESSING                      OUTPUT
─────                          ──────────                      ──────

tasks/commits.yaml             ┌─────────────┐
  ├─ name                      │  TaskConfig  │
  ├─ strategy ─────────────────│  (Pydantic)  │
  ├─ instructions              └──────┬───────┘
  ├─ output_fields                    │
  └─ checkpoints                      │
                                      ▼
tasks/inputs/commits.csv       ┌─────────────┐        evidence/run_TIMESTAMP/
  ├─ sample_id                 │  Orchestrator│        ├─ results.csv
  └─ url ──────────────────────│  (asyncio)   │────────├─ run_summary.json
                               └──────┬───────┘        └─ samples/
                                      │                    └─ commit_001/
.env                                  │                       ├─ 01_commit_page.png
  ├─ LLM_PROVIDER              ┌──────▼───────┐              ├─ 02_pr_review.png
  └─ API_KEY ──────────────────│ EvidenceAgent │              ├─ result.json
                               │ + browser-use │              └─ action_log.json
                               └───────────────┘
```

---

## 6. Provenance Chain — How Every Field Traces Back to Source

```mermaid
flowchart LR
    CSV["results.csv<br/>author = benibenj"] --> RJ["result.json<br/>FieldExtraction"]
    RJ --> SU["source_url<br/>github.com/commit/9986a4..."]
    RJ --> SS["source_selector<br/>commit page body"]
    RJ --> AR["artifact_ref<br/>01_commit_page.png"]
    AR --> FILE["01_commit_page.png<br/>SHA-256: e14a1d01..."]

    style CSV fill:#e8eaf6,stroke:#3f51b5
    style FILE fill:#e8f5e9,stroke:#4caf50
```

**Auditor can trace:** CSV value → result.json field → source URL + DOM selector → supporting screenshot (integrity-verified by SHA-256 hash).

---

## 7. ASCII Overview — The Complete System

```
┌─────────────────────────────────────────────────────────────────────┐
│                        BROWSER EVIDENCE AGENT                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  INPUT LAYER                                                         │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────────┐     │
│  │  Task YAML   │  │  Input CSV     │  │  .env (API keys)     │     │
│  │  (what to do) │  │  (sample URLs) │  │  (LLM provider)     │     │
│  └──────┬───────┘  └──────┬─────────┘  └──────────┬───────────┘     │
│         └────────────┬─────┘                       │                 │
│                      ▼                             │                 │
│  ORCHESTRATION LAYER                               │                 │
│  ┌──────────────────────────────────┐              │                 │
│  │  BatchOrchestrator               │              │                 │
│  │  • Concurrent samples (semaphore)│              │                 │
│  │  • Fault isolation per sample    │              │                 │
│  │  • Progress logging (Rich)       │              │                 │
│  └──────────────┬───────────────────┘              │                 │
│                 ▼                                   │                 │
│  AGENT LAYER                                       │                 │
│  ┌──────────────────────────────────┐    ┌─────────▼──────────┐     │
│  │  EvidenceAgent (one per sample)  │    │  LLM Factory       │     │
│  │  • Strategy-driven prompts       │◄───│  Anthropic/OpenAI/ │     │
│  │  • Timeout + retry logic         │    │  Gemini/browser-use│     │
│  │  • Checkpoint tracking           │    └────────────────────┘     │
│  └──────────────┬───────────────────┘                               │
│                 ▼                                                     │
│  BROWSER LAYER                                                       │
│  ┌──────────────────────────────────┐                               │
│  │  browser-use Agent               │    CUSTOM ACTIONS:             │
│  │  • DOM extraction (accessibility │    • screenshot_evidence       │
│  │    tree, not raw HTML)           │    • record_fields             │
│  │  • Navigation (click, scroll,    │    • mark_checkpoint           │
│  │    form fill, follow links)      │    • make_judgment             │
│  │  • ReAct reasoning loop          │    • download_file             │
│  └──────────────┬───────────────────┘                               │
│                 ▼                                                     │
│  OUTPUT LAYER (Deterministic — no LLM)                               │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  FileManager          │  CSVWriter         │  Summaries      │    │
│  │  • Sequential naming  │  • Batch write     │  • run_summary  │    │
│  │  • SHA-256 hashing    │  • Sorted by ID    │  • Checkpoint   │    │
│  │  • result.json        │  • Typed columns   │    pass rates   │    │
│  │  • action_log.json    │                    │  • Error breakdown│   │
│  └───────────────────────┴────────────────────┴─────────────────┘    │
│                                                                      │
├─────────────────────────────────────────────────────────────────────┤
│  STRATEGY PATTERN                                                    │
│  ┌─────────────┐  ┌──────────────────┐  ┌────────────────┐         │
│  │ single_page │  │ graph_traversal  │  │  form_fill     │         │
│  │ Visit+Extract│  │ Follow links     │  │  Fill+Submit   │         │
│  │             │  │ across pages     │  │  +Download     │         │
│  └─────────────┘  └──────────────────┘  └────────────────┘         │
│  New task = YAML only | New family = 1 strategy class + YAML        │
└─────────────────────────────────────────────────────────────────────┘
```
