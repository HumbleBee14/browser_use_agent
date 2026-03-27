# Andera AI — General Browser Agent: Architecture Blueprint

**Version:** 1.0  
**Date:** 2026-03-27  
**Scope:** Custom browser agent built from scratch using raw Playwright + Anthropic SDK. No browser-use, no LangChain.

---

## Table of Contents

1. [Challenge Summary](#1-challenge-summary)
2. [Task Analysis](#2-task-analysis)
3. [Key Design Requirements](#3-key-design-requirements)
4. [Proposed Architecture](#4-proposed-architecture)
5. [ReAct Agent Loop Design](#5-react-agent-loop-design)
6. [Sub-Agent Architecture](#6-sub-agent-architecture)
7. [DOM Extraction Strategy](#7-dom-extraction-strategy)
8. [Vision Module Design](#8-vision-module-design)
9. [Action System](#9-action-system)
10. [Scalability Design](#10-scalability-design)
11. [Evidence Pipeline](#11-evidence-pipeline)
12. [Technology Stack](#12-technology-stack)
13. [Implementation Plan](#13-implementation-plan)

---

## 1. Challenge Summary

### What Andera Asked

Andera AI is building a **General Browser Agent** to automate audit evidence collection — work that auditors currently do manually across enterprise systems like Workday, GitHub, Jira, Linear, and LinkedIn. The brief defines a system that must:

- Navigate any website purely through its browser UI (no assumptions about APIs or data exports)
- Collect screenshots, extract structured fields, download artifacts, and emit judgments
- Handle both structured input (Excel/CSV of URLs) and natural language instructions
- Produce deterministic, reviewable outputs: one folder per sample, consistent naming, CSV summaries
- Scale to thousands of samples without degradation

**Critically, Andera explicitly prohibits using browser-use or any existing agent framework.** They want to evaluate custom agent design skills — the architecture, not the wrapper.

### What They Are Evaluating

Based on the brief and the nature of the work trial, the evaluation criteria are:

1. **Agent loop design** — does the architecture reflect a real understanding of ReAct reasoning, observation compression, and controlled action execution, or is it just an LLM-in-a-loop?
2. **Sub-agent coordination** — the brief explicitly mentions "many subagents." Can the design decompose complex multi-page tasks without losing context?
3. **Evidence quality** — are outputs deterministic, tamper-evident (hashed), and audit-reviewable?
4. **Scalability** — can the design actually handle 1,000 samples without memory leaks, browser pool exhaustion, or inconsistent output ordering?
5. **System-agnostic generality** — does the design rely on site-specific selectors or brittle heuristics, or does it reason over live DOM state?

### Key Constraints

| Constraint | Implication |
|---|---|
| No browser-use or agent frameworks | Must implement the full ReAct loop, action registry, and tool dispatch from scratch |
| System-agnostic | Cannot use site-specific selectors; must interpret live DOM + vision |
| Input can be natural language | Task parser must convert free-text prompts into structured execution plans |
| Priorities: accuracy first, speed last | Prefer conservative approaches over fast-but-flaky ones |
| Evidence-grade outputs | Every artifact needs provenance: source URL, SHA-256 hash, timestamp, screenshot reference |
| "Likely leverage a file system and many subagents" | Hierarchical planning is an explicit product requirement, not an implementation detail |

---

## 2. Task Analysis

### Task 1: Linear Ticket Screenshots + CSV

**Input format:** Excel file containing a list of Linear ticket URLs, one per row.

**Navigation pattern:** Single-page, batch. The agent visits each URL independently. No cross-page link following required. This is the simplest task family — a tight read-extract-screenshot loop per sample.

**Extraction needs:**
- Ticket number (usually in the URL and page heading)
- Assignee (a name or avatar with a text label)
- Due date (a date field, often in a sidebar panel)

**Output format:**
- One folder per ticket containing at least one screenshot
- A CSV with columns: `ticket_number`, `assignee`, `due_date`, plus `sample_id`, `source_url`, `screenshot_file`

**Key challenges:**
- Linear's UI is heavily React-rendered. The DOM snapshot at page load may be incomplete; the agent needs to wait for the SPA to hydrate before extracting fields.
- Assignee and due date may be `null` if unset — the agent must distinguish "not assigned" from "failed to find."
- Screenshot consistency: the viewport may render differently depending on window state. Full-page screenshots are preferable.

**Strategy family:** `SinglePageExtraction`

---

### Task 2: GitHub Deep Audit (Commit → PR → CI → Jira)

**Input format:** A repository reference and a commit range (e.g., "last 60 commits on main"), or a list of commit SHAs.

**Navigation pattern:** Graph traversal. Each commit is one sample. For each commit:
1. Navigate to the commit page. Screenshot.
2. Find the associated PR. Navigate to it. Screenshot.
3. On the PR: find the check suite. Identify pass/fail/optional status per check.
4. If any check failed and the PR was merged: navigate into the CI provider (e.g., GitHub Actions) and screenshot the failing job.
5. If the PR description or commit references a Jira ticket: follow the link, screenshot the Jira ticket.

**Extraction needs:**
- Commit SHA, author, timestamp
- PR number, creator, approver(s), merger
- Check names + pass/fail/optional status
- CI failure details (job name, error summary)
- Jira ticket URL and title

**Output format:**
- One folder per commit containing all collected screenshots
- CSV with one row per commit: all extracted fields listed above
- Screenshots named by step (e.g., `01_commit_page.png`, `02_pr_page.png`, `03_checks.png`, `04_ci_failure.png`, `05_jira_ticket.png`)

**Key challenges:**
- GitHub's check suite section uses a shadow DOM-adjacent pattern; status icons may be SVG-only with no accessible text. Vision fallback is needed here.
- The PR approval section may require scrolling or expanding a collapsible. The agent must handle both visible and hidden states.
- Cross-domain navigation (GitHub → Jira) requires maintaining context across a domain boundary.
- Some commits may not have an associated PR (direct pushes). The agent must detect this and not block.
- 60 commits × potentially 5 page visits = up to 300 navigation steps per batch. Context compression is critical.

**Strategy family:** `GraphTraversal` with `JudgmentCapable` mixin

---

### Task 3: LinkedIn Enrichment (CSV Combination)

**Input format:** A CSV of names collected from an event. No URLs provided — the agent must discover each person's LinkedIn profile.

**Navigation pattern:** Search + single-profile extraction. For each name:
1. Navigate to LinkedIn (or use a LinkedIn search URL).
2. Search for the person by name.
3. Identify the correct profile from search results (disambiguation using other CSV context fields if available, e.g., company or location).
4. Navigate to the profile.
5. Extract: LinkedIn URL, school (most recent or highest degree), current company, tenure (start date → present).

**Output format:** The original CSV with four new columns appended: `linkedin_url`, `school`, `current_company`, `tenure_months`.

**Key challenges:**
- LinkedIn aggressively blocks bot-like access. Session reuse (loading a saved authenticated browser profile) is essential.
- The same name may match multiple profiles. The agent needs a disambiguation heuristic, not just "take the first result."
- Tenure calculation: LinkedIn displays "3 years 2 months" — the agent must normalize this to a consistent format.
- LinkedIn's DOM changes frequently; field extraction must use semantic reasoning (e.g., "the element labeled 'Current Position'") not brittle CSS selectors.
- Rate limiting: per-session request velocity must be controlled to avoid triggering LinkedIn's bot detection.

**Strategy family:** `SearchAndExtract` with session reuse

---

### Task 4: Code Blame and Materiality Judgment

**Input format:** A code string (a function signature, a formula, a variable name). The target repository is either provided or must be inferred.

**Navigation pattern:** Multi-step lookup with judgment.
1. Search the repository for the code string (using GitHub's code search or the file tree).
2. Navigate to the file containing the match.
3. Switch to Blame View.
4. Identify when the most recent change to the relevant lines was made.
5. If within the audit year: navigate to that commit and assess whether the change materially affected how the function/calculation operates.
6. Also check: is there code below the target that was changed within the audit year that could affect the outcome? If yes, repeat the materiality assessment.
7. Screenshot all relevant pages and annotate findings.

**Output format:**
- Screenshots with consistent naming
- A judgment: `material_change: yes/no/inconclusive`, `last_modified: date`, `change_summary: str`
- A CSV row per code sample with all fields

**Key challenges:**
- This task requires genuine reasoning, not just extraction. The agent must assess whether a code change is "material" — this needs a judgment model with access to the code diff and context.
- GitHub Blame View renders line annotations as a hybrid of DOM and canvas-like rendering. DOM extraction is sufficient but requires careful element targeting.
- The code string input may be ambiguous: the same snippet may appear in multiple files. The agent must report all matches or select the most relevant one.
- The "code below" check requires understanding code scope (function boundaries), which is a reasoning task, not a navigation task.

**Strategy family:** `GraphTraversal` with `JudgmentCapable` mixin

---

### Task 5: Form Fill, Screenshot, Download (Workday-like)

**Input format:** A URL to a form and a structured specification of what to fill in each field.

**Navigation pattern:** Sequential form interaction.
1. Navigate to the form URL.
2. Fill in each field in order (dropdowns, date pickers, text inputs).
3. Screenshot the completed form before submission.
4. Submit the form / click the "Generate Report" button.
5. Wait for the report to load.
6. Download the report file.
7. Navigate through any tabbed views and download all attachments.

**Output format:**
- Screenshot of the filled form (before submission)
- Downloaded report file(s) with SHA-256 hashes
- Screenshots of each tab/attachment downloaded
- A CSV row recording the form fields submitted and the artifact filenames

**Key challenges:**
- Enterprise form UIs (Workday, AuditBoard) have complex custom widgets: multi-select dropdowns, date range pickers, cascading field dependencies. Standard `fill()` is insufficient for many of these.
- Download handling: the agent must detect when a file download triggers (as opposed to a page navigation) and capture the file to the sample folder.
- Tab iteration: some reports have 5–10 tabs each with separate attachments. The agent must enumerate them systematically.
- Cascading form fields: selecting one value may hide or reveal other fields. The agent must re-read the DOM after each interaction.

**Strategy family:** `FormFillAndDownload`

---

## 3. Key Design Requirements

### 3.1 Custom Agent Loop

The brief's prohibition on browser-use is a signal. Andera wants to see that we understand what a browser agent loop actually does — not that we know how to configure an existing framework.

A real ReAct loop requires:
- **Observation serialization**: converting a live browser state (DOM tree + optional screenshot) into a token-efficient text/image representation that fits inside a context window
- **Reasoning**: the LLM's internal chain-of-thought before deciding an action
- **Action dispatch**: translating the LLM's structured output into concrete Playwright API calls
- **Result observation**: reading back the outcome of the action (element found/not found, navigation succeeded, text extracted) and feeding it back as the next observation
- **Termination logic**: knowing when to stop (goal reached, checkpoint satisfied) versus when to retry versus when to escalate

Building this from scratch forces deliberate decisions about every one of these stages. That is what Andera is evaluating.

### 3.2 Sub-Agent Coordination

The brief says "we should likely leverage a file system and many subagents." This is not casual phrasing. It describes a specific architectural pattern: a planner agent that decomposes tasks into sub-goals and dispatches specialized agents (navigators, extractors, judges) to satisfy them.

The key coordination challenge is **context isolation**: each sub-agent must receive enough context to do its work without drowning in the full history of the batch. The file system is the shared state — sub-agents read prior sub-agents' outputs from disk rather than sharing an in-memory context window.

### 3.3 Scalability to Thousands of Samples

One thousand samples at 30–120 seconds each is 8–33 hours of sequential wall time. The only way to make this practical is concurrent execution — multiple browsers running in parallel, bounded by a concurrency limit.

This creates engineering requirements:
- A browser pool that allocates and recycles browser instances without leaks
- Per-sample isolation: a failure in one sample must not affect others
- Progress checkpointing: if the run is interrupted at sample 700, it should be resumable from sample 701, not from scratch
- Memory management: browsers accumulate memory over time; the pool must enforce eviction policies

### 3.4 Vision Understanding

Many audit-relevant pages have visual layouts that DOM text alone cannot describe: CI status icons that are SVG-only, GitHub review approval banners with color-coded states, chart-based dashboards. The agent needs a vision module that can analyze screenshots when DOM extraction is insufficient.

The critical design choice is **when to use vision** vs when to trust DOM. Vision is expensive (screenshot capture + multimodal inference), slow, and more prone to hallucination on dense layouts. DOM is fast, cheap, and semantically precise. The agent must make this decision dynamically per page, not globally.

### 3.5 Error Recovery

Audit tasks are not retryable in the way web scraping is. If the agent gets into a bad state (wrong page, failed form submit, missed screenshot), the evidence collected so far may be partial. The agent must:
- Never retry an identical failed action
- Detect when it's stuck (repeated navigation to the same URL without progress)
- Preserve partial evidence from a failed run
- Report exactly what succeeded and what failed, not just "error"

### 3.6 System-Agnostic Design

The agent must not contain site-specific logic for GitHub, LinkedIn, or Jira. It must reason about the live DOM state of any website. This means:
- No hardcoded CSS selectors
- No site-specific URL patterns
- Navigation logic is driven by what the LLM observes in the DOM, not by what we know about the site's structure ahead of time
- The only site-specific configuration allowed is in task YAML files (e.g., `allowed_domains`), not in agent code

### 3.7 Natural Language Task Parsing

The brief gives an example: "Go to Microsoft's GitHub and get all users' GitHub usernames." This is not a structured CSV — it is a free-text instruction that the system must interpret, break into a plan, and execute. The task parser is therefore a planning agent in its own right, responsible for converting natural language into a typed execution plan before the main agent runs.

---

## 4. Proposed Architecture

### 4.1 Layer Overview

The system has four layers. Each has a clean boundary and a single responsibility.

```
+==============================================================================+
|                           ENTRY POINTS                                        |
|  CLI (run_task.py)   |   Python API   |   Natural Language Prompt             |
+==============================================================================+
         |                    |                          |
         v                    v                          v
+==============================================================================+
|                         ORCHESTRATOR LAYER                                    |
|                                                                                |
|  TaskParser          BatchRunner              AgentPool                        |
|  (NL -> Plan)        (concurrent samples,     (browser lifecycle,              |
|                       semaphore, progress,     acquisition, release,           |
|                       checkpointing)           eviction)                       |
+==============================================================================+
         |
         v (one TaskPlan per sample)
+==============================================================================+
|                           AGENT LAYER                                          |
|                                                                                |
|  PlannerAgent        NavigatorAgent        ExtractorAgent                      |
|  (task → sub-goals,  (URL navigation,      (DOM field              +--------+ |
|   coordination,       link following,       extraction,            | Judge  | |
|   sub-agent dispatch) action loop)          field validation)       | Agent  | |
|                                                                     +--------+ |
+==============================================================================+
         |
         v (each agent uses these core primitives)
+==============================================================================+
|                            CORE LAYER                                          |
|                                                                                |
|  ReActLoop           BrowserController     DOMExtractor                        |
|  (Think→Act→Observe  (raw Playwright:       (accessibility tree,               |
|   cycle, step limit,  navigate, click,       pruning, chunking,                |
|   loop detection,     type, scroll,          interactive element                |
|   memory manager)     download, wait)        identification)                   |
|                                                                                |
|  VisionModule        ActionRegistry        LLMClient                           |
|  (screenshot,         (action definitions,  (Anthropic SDK,                    |
|   Claude vision,       tool schema,          tool use, prompt                  |
|   hybrid dispatch)     error handling)       caching, streaming)               |
+==============================================================================+
         |
         v
+==============================================================================+
|                           OUTPUT LAYER                                         |
|                                                                                |
|  FileManager         CSVWriter             EvidencePackager                    |
|  (per-sample dirs,   (batch CSV,            (result.json,                      |
|   sequential naming,  field coercion,        action_log.json,                  |
|   SHA-256 hashing,    ordering)              run_summary.json,                 |
|   artifact registry)                         manifest validation)              |
+==============================================================================+
```

### 4.2 Component Dependency Graph

```
TaskParser
    └─> TaskPlan
            └─> BatchRunner
                    ├─> AgentPool (browser lifecycle)
                    └─> PlannerAgent (per sample)
                                └─> [NavigatorAgent, ExtractorAgent, JudgeAgent]
                                            └─> ReActLoop
                                                    ├─> BrowserController (Playwright)
                                                    ├─> DOMExtractor
                                                    ├─> VisionModule
                                                    ├─> ActionRegistry
                                                    └─> LLMClient (Anthropic SDK)
                                            └─> FileManager (output, deterministic)
                    └─> CSVWriter
                    └─> EvidencePackager
```

### 4.3 Data Flow: Single Sample

```
Input (URL or NL instruction)
        |
        v
TaskParser.parse(input)
        |
        v
TaskPlan { goal, sub_goals[], output_fields[], checkpoints[], allowed_domains[] }
        |
        v
BatchRunner.dispatch(plan, sample)
        |
        v
AgentPool.acquire() --> BrowserContext (isolated, with auth state if available)
        |
        v
PlannerAgent.run(plan, sample, browser_context)
        |
        v
  [loop: for each sub_goal]
        |
        v
  NavigatorAgent.run(sub_goal, browser_context)
        |
        v
    ReActLoop
        |
      THINK: LLMClient.invoke(system_prompt + observation + history)
        |
      ACT:   ActionRegistry.dispatch(action_name, params)
        |          --> BrowserController.execute(playwright_call)
        |          --> DOMExtractor.snapshot()          [DOM-first]
        |          --> VisionModule.analyze()           [when needed]
        |          --> FileManager.save_screenshot()    [deterministic]
        |
      OBSERVE: serialize_result(action_outcome) --> append to step history
        |
      [checkpoint check] -- satisfied? --> return SubGoalResult to PlannerAgent
        |
      [step limit?] --> fail gracefully, preserve partial evidence
        |
        v
  ExtractorAgent.extract(sub_goal.fields, browser_context)
        |
        v
  [optional] JudgeAgent.judge(question, evidence_refs, browser_context)
        |
        v
PlannerAgent.aggregate() --> SampleResult
        |
        v
FileManager.save_result_manifest(result)     [deterministic]
CSVWriter.append_row(result)                 [deterministic]
AgentPool.release(browser_context)
        |
        v
Output: evidence/{run_id}/samples/{sample_id}/
            01_commit_page.png
            02_pr_page.png
            03_checks.png
            result.json
            action_log.json
```

### 4.4 Module Structure

```
playwright_agent/
├── core/
│   ├── react_loop.py           # ReAct cycle: think, act, observe, terminate
│   ├── browser_controller.py  # Playwright wrapper: navigate, click, type, scroll
│   ├── dom_extractor.py        # Accessibility tree + interactive element detection
│   ├── vision_module.py        # Screenshot capture + Claude vision analysis
│   ├── action_registry.py      # Action definitions, tool schema, dispatch
│   ├── memory_manager.py       # Step history compression, rolling buffer
│   └── llm_client.py           # Anthropic SDK wrapper, tool use, caching
│
├── agents/
│   ├── base_agent.py           # Abstract: run(), terminate(), build_context()
│   ├── planner_agent.py        # Decompose task → sub-goals, coordinate sub-agents
│   ├── navigator_agent.py      # Single-goal navigation + observation loop
│   ├── extractor_agent.py      # Focused field extraction from current page state
│   └── judge_agent.py          # Evidence-based judgment with reasoning trace
│
├── orchestrator/
│   ├── task_parser.py          # NL prompt / YAML / CSV → TaskPlan (typed)
│   ├── batch_runner.py         # Concurrent sample dispatch, semaphore, progress
│   └── agent_pool.py           # Browser lifecycle: acquire, release, evict
│
├── output/
│   ├── file_manager.py         # Deterministic folder/file management, SHA-256
│   ├── csv_writer.py           # Batch CSV with field coercion and ordering
│   └── evidence_packager.py    # result.json, action_log.json, run_summary.json
│
├── models/
│   ├── task.py                 # TaskPlan, SubGoal, FieldSpec, Checkpoint
│   ├── evidence.py             # SampleResult, EvidenceArtifact, FieldExtraction
│   ├── actions.py              # ActionDefinition, ActionResult, ActionError
│   └── judgment.py             # JudgmentResult, JudgmentQuestion
│
└── run.py                      # CLI entry point
```

---

## 5. ReAct Agent Loop Design

### 5.1 The Core Cycle

ReAct (Reason + Act) is the pattern at the heart of every production browser agent. It is a loop where the agent alternates between thinking about what to do next and taking an action, then observing the result before thinking again.

```
                     +------------------+
                     |   INITIAL STATE  |
                     | goal, context,   |
                     | step_history=[]  |
                     +------------------+
                              |
                              v
                    +---------+---------+
                    |                   |
              [step_limit              |
               exceeded?]              |
                    |                   |
                   YES                  NO
                    |                   |
                    v                   v
              [fail with        +-------+-------+
               partial          |    THINK      |
               evidence]        | LLM reasons   |
                                | over:         |
                                | - current obs |
                                | - step history|
                                | - goal        |
                                | - memory      |
                                +-------+-------+
                                        |
                                        v
                                +-------+-------+
                                |     PLAN      |
                                | LLM selects   |
                                | action(s) from|
                                | tool registry |
                                +-------+-------+
                                        |
                                  [action call]
                                        |
                              +---------+---------+
                              |                   |
                        [is task             [navigate/
                         complete?]           click/type/
                              |               extract/etc.]
                             YES                  |
                              |                   v
                              v           +-------+-------+
                       [finalize,         |   OBSERVE     |
                        save result]      | serialize     |
                                          | action result |
                                          | (text or img) |
                                          +-------+-------+
                                                  |
                                                  v
                                          [append to
                                           step_history]
                                                  |
                                                  v
                                          [loop detection:
                                           same URL × N?
                                           same action × N?]
                                                  |
                                               [back to THINK]
```

### 5.2 Think Phase

The Think phase is the LLM call. The input context is carefully constructed to stay within token budget while preserving all information the agent needs to make a good decision.

**Prompt structure (ordered for prefix caching):**
```
[STATIC — cache across all steps]
System prompt: role, capabilities, output format, action schema

[SESSION — cache per sample]
Task goal, output fields required, checkpoints, allowed domains, sample_id

[DYNAMIC — changes each step]
Current DOM observation (accessibility tree, pruned)
Optional: current screenshot (if vision mode active)
Step history (compressed rolling buffer, last 10 full + earlier summarized)
Available actions (tool schema)
```

The LLM outputs a structured response:
```json
{
  "thinking": "The page shows a PR #1234 opened by alice. I can see the 'Checks' section is collapsed. I need to click the expand button to see the check statuses.",
  "next_goal": "Expand the Checks section to see pass/fail status",
  "actions": [
    {
      "action": "click",
      "selector_strategy": "text",
      "selector_value": "Show all checks",
      "reasoning": "The 'Show all checks' link will expand the collapsed checks section"
    }
  ]
}
```

The `thinking` field is internal reasoning — it is never sent to the user but is captured in the action log for debugging and audit review.

### 5.3 Act Phase

The Act phase dispatches the selected action(s) to the BrowserController. Actions are validated before dispatch:
- The selector strategy and value must be non-empty
- The action name must exist in the ActionRegistry
- If the previous action failed with the same parameters, escalate rather than retry

Multiple actions per step are allowed when they are clearly sequential and non-blocking (e.g., navigate then scroll). Batching reduces round-trip LLM calls. The default limit is 3 actions per step.

### 5.4 Observe Phase

After each action, the agent builds the next observation. This is the serialized state of the browser that will be included in the next LLM call.

**Observation construction:**
1. Capture the current accessibility tree snapshot (always)
2. Evaluate whether the page changed materially since the last observation (URL change, DOM mutation depth, new interactive elements count)
3. If the page did not change: note this as a "no-change" observation — this is a signal for loop detection
4. If vision mode is active or the DOM snapshot appears incomplete: take a screenshot and include it
5. Append the observation to step history

**Observation serialization format:**
```
[Step 3 Observation]
URL: https://github.com/org/repo/pull/1234
Title: Fix authentication bug — pull request
Interactive elements:
  [1] button "Request review"
  [2] link "alice" (author)
  [3] button "Show all checks" (collapsed)
  [4] link "3 commits"
  [5] button "Merge pull request" (disabled)
Page text (truncated to 2000 chars):
  Fix authentication bug
  Opened by alice 2 days ago
  Reviewers: bob (approved), carol (approved)
  Checks: 12 passing, 0 failing
```

### 5.5 Termination Logic

The agent stops when one of:
1. The LLM calls the `finish` action with a completion summary
2. All required checkpoints are marked satisfied
3. The step limit is reached (configurable per task, default 30)
4. The loop detector fires (same URL visited 3+ times without new evidence collected)
5. A hard error occurs (navigation timeout, element not found after 3 attempts with different strategies)

**Graceful degradation:** The agent never raises an exception from the loop. It always returns a `SampleResult` with status `completed`, `needs_review`, or `failed`. Partial evidence collected before termination is preserved.

### 5.6 Loop Detection

The loop detector maintains a counter per (URL, action_name) pair. If the count exceeds a threshold (default 3), it injects a "nudge" message into the next observation:

```
[SYSTEM NOTICE] You have visited this URL 3 times without recording new evidence.
Either: (a) the information you need is not on this page, (b) you need to scroll
further, or (c) you should try a different approach. Do not repeat the same action.
```

This mirrors the approach used in production agents like browser-use's `ActionLoopDetector`, but built directly into our loop rather than relying on a framework.

### 5.7 How This Differs from browser-use's Loop

browser-use's loop is well-engineered but opinionated:
- It has a fixed observation format (their `BrowserStateSummary`)
- Vision mode is set at agent creation and cannot be changed per-step
- The action system uses a decorator registry that is tightly coupled to their `Browser` abstraction
- Memory management is handled by their `MessageManager`

Our custom loop differs in that:
- The observation serializer is pluggable — we can use accessibility tree, pruned DOM, or raw HTML depending on task requirements
- Vision mode can be decided per-step based on page characteristics detected in the DOM observation
- The action registry is fully decoupled from browser state — actions are pure functions that receive a Playwright page handle
- Memory compression is an explicit module that can be tuned (rolling buffer size, summarization model) without touching the loop

---

## 6. Sub-Agent Architecture

### 6.1 Why Sub-Agents

A single monolithic agent running a 60-commit GitHub audit will accumulate 300+ steps of history. At 1,000 tokens per step observation (conservative), that is 300,000 tokens of context before any task-specific content. Even with compression, this is unmanageable and causes attention dilution — the model loses track of earlier evidence when it has too much to attend to.

The sub-agent pattern solves this by keeping each agent's context window focused on a single sub-goal. The PlannerAgent maintains global task state (which sub-goals are done, what evidence has been collected so far) and coordinates between sub-agents. Each NavigatorAgent runs a fresh loop with only the context needed for its specific sub-goal.

This is the "file system and many subagents" pattern the brief explicitly calls out. The file system is the coordination mechanism: sub-agents write their outputs to disk, and the PlannerAgent reads them back. No shared in-memory state between sub-agents.

### 6.2 PlannerAgent

The PlannerAgent is not a browser agent. It does not navigate. It is a pure coordination agent that:

1. Receives the `TaskPlan` (parsed from input) and a `SampleInput`
2. Decides the sequence of sub-goals to execute
3. Dispatches each sub-goal to the appropriate specialized agent
4. After each sub-goal, checks: were the expected artifacts produced? Are required checkpoints satisfied?
5. If a sub-goal failed: decides whether to retry, skip, or fail the sample
6. After all sub-goals: calls `ExtractorAgent` to consolidate extracted fields
7. Optionally calls `JudgeAgent` if the task requires a judgment
8. Returns the complete `SampleResult`

**PlannerAgent context window:**
```
System: You are a task coordinator. You do not navigate. You direct other agents.
Task goal: [from TaskPlan]
Sub-goals completed so far: [list with outcomes]
Evidence collected so far: [artifact filenames, fields extracted]
Current sub-goal to dispatch: [sub-goal spec]
Available agent types: [navigator, extractor, judge]
Instruction: Select the agent type and provide it with its specific goal and context.
```

**PlannerAgent decision output:**
```json
{
  "agent_type": "navigator",
  "sub_goal": "Navigate to PR #1234 and screenshot the review status section",
  "starting_url": "https://github.com/org/repo/pull/1234",
  "expected_artifacts": ["pr_review_status.png"],
  "expected_fields": [],
  "max_steps": 8
}
```

### 6.3 NavigatorAgent

The NavigatorAgent is the workhorse. It receives a single sub-goal, a starting URL, and a step budget. It runs the ReAct loop until:
- The sub-goal is satisfied (screenshot taken, link followed, etc.)
- The step budget is exhausted

The NavigatorAgent does not know about the broader task. It only knows its sub-goal. This focus is what keeps its context window small and its reasoning sharp.

**NavigatorAgent context construction:**
```
System: You are a browser navigation agent. Your ONLY job is to satisfy this sub-goal:
  [sub-goal description]
Starting URL: [url]
Available actions: [action schema]
You have [N] steps remaining.
Take a screenshot when you reach each significant page.
Call finish() when the sub-goal is complete.
```

### 6.4 ExtractorAgent

The ExtractorAgent is a specialized agent for field extraction. It receives a DOM observation (or the current page state) and a list of fields to extract. It returns structured key-value pairs.

Unlike the NavigatorAgent, the ExtractorAgent runs a short, focused loop — typically 1–3 steps:
1. Observe the current page state (DOM + optional screenshot)
2. Extract the requested fields, reasoning over the DOM text
3. Call `record_fields()` with the extracted values
4. Call `finish()` if all required fields are found; otherwise call `screenshot_evidence()` to capture the ambiguous section and flag for review

The ExtractorAgent is used both as a standalone agent (for single-page extraction tasks) and as a sub-agent called by the PlannerAgent after a NavigatorAgent has reached the right page.

### 6.5 JudgeAgent

The JudgeAgent handles tasks that require a bounded judgment — not just data extraction, but a yes/no/inconclusive answer based on evidence.

Example judgment question: "Was this code change within the last year, and if so, did it materially change how this calculation operates?"

The JudgeAgent receives:
- The judgment question
- A list of evidence artifact paths (screenshots, extracted fields) from prior sub-agents
- The current page state (if still on a relevant page)

It responds with:
```json
{
  "answer": "yes",
  "confidence": 0.85,
  "reasoning": "The blame view shows the last change to lines 45-67 was on 2025-11-03 (within the audit year). The commit diff shows the divisor was changed from 'total_employees' to 'active_employees', which would change the per-employee calculation. This is a material change.",
  "evidence_refs": ["03_blame_view.png", "04_commit_diff.png"]
}
```

The JudgeAgent's output is written to `result.json` as a `JudgmentResult` with full provenance.

### 6.6 Browser Context Sharing

Sub-agents within the same sample share the same Playwright `BrowserContext`. This means:
- Cookies and session state are preserved across sub-agent handoffs
- If NavigatorAgent logs into GitHub for sub-goal 1, the ExtractorAgent on sub-goal 2 is already authenticated
- Tabs can be created and closed within the shared context as needed

Sub-agents do NOT share the same `Page`. Each sub-agent gets a fresh page (new tab), runs its loop on that page, then closes it when done. This prevents state leakage between sub-goals while preserving session authentication.

```python
# PlannerAgent dispatching to NavigatorAgent
async with browser_context.new_page() as page:
    agent = NavigatorAgent(
        sub_goal=sub_goal,
        page=page,
        file_manager=self.file_manager,
        llm=self.llm,
        max_steps=sub_goal.max_steps,
    )
    result = await agent.run()
# page is closed here; browser_context (session) is preserved
```

### 6.7 Agent Pool for Scale

When processing 1,000 samples concurrently, the AgentPool manages browser contexts as a resource:

```
AgentPool
├── max_browsers: int              # hard ceiling (e.g., 10)
├── active: dict[sample_id, BrowserContext]
├── semaphore: asyncio.Semaphore   # controls acquisition
└── eviction_policy: LRU           # evict idle contexts after N minutes

acquire(sample_id) -> BrowserContext
    # if available authenticated context exists: return it
    # else: launch new chromium instance, optionally load auth state
    # block on semaphore if at max_browsers

release(sample_id, context)
    # return context to pool (do not close; session is valuable)
    # mark as idle for eviction tracking

evict_idle()
    # close contexts idle > eviction_threshold
    # runs on a background task every 60s
```

---

## 7. DOM Extraction Strategy

### 7.1 Why DOM-First

Research data is unambiguous: DOM-based extraction yields 81% accuracy on structured web tasks; pure vision approaches yield 40–66%. The information chain degrades as follows:

```
Raw HTML:          100% of semantic information
Rendered page:     ~60%  (JS may have hidden elements, styles affect layout)
Screenshot/OCR:    ~40%  (hierarchies flattened, off-screen content lost)
Vision model:      ~30%  (compounding interpretation errors)
```

For audit evidence collection, where accuracy is explicitly the top priority, DOM-first extraction is not a performance optimization — it is the correct default. Vision is a fallback for pages where the DOM is inadequate.

### 7.2 Accessibility Tree as Primary Observation

Playwright provides `page.accessibility.snapshot()` which returns the page's accessibility tree in a structured JSON format. This is semantically richer than raw HTML (it includes roles, labels, states, and values) and far smaller than the full DOM.

A typical enterprise page that has 10,000+ DOM nodes and 100KB+ of raw HTML will produce an accessibility tree of 2–5KB. This fits comfortably within any LLM context window.

**Accessibility tree snapshot example (GitHub PR page):**
```yaml
role: document
name: "Fix authentication bug #1234 · org/repo"
children:
  - role: heading
    name: "Fix authentication bug"
    level: 1
  - role: group
    name: "Status"
    children:
      - role: img
        name: "Merged"
  - role: list
    name: "Reviewers"
    children:
      - role: listitem
        name: "bob — Approved"
      - role: listitem
        name: "carol — Approved"
  - role: region
    name: "Checks"
    children:
      - role: button
        name: "Show all checks"
        expanded: false
```

### 7.3 DOM Pruning Pipeline

When the accessibility tree is insufficient (e.g., pages with custom ARIA implementations or heavy canvas usage), we fall back to a pruned DOM representation. The pruning pipeline has four stages:

**Stage 1: Rule-Based Filter**
Remove all non-semantic elements:
- `<script>`, `<style>`, `<meta>`, `<link>`, `<noscript>` tags
- Elements with `visibility: hidden` or `display: none`
- Elements with no text content and no interactive attributes
- Navigation breadcrumbs and advertisement containers

**Stage 2: Interactive Element Detection**
Identify elements the agent can interact with using five heuristics (in priority order):
1. Native interactive tags: `<button>`, `<a href>`, `<input>`, `<select>`, `<textarea>`
2. ARIA roles: `role="button"`, `role="link"`, `role="checkbox"`, etc.
3. JavaScript event listeners: detected via CDP `Runtime.getEventListeners()`
4. CSS cursor: `cursor: pointer` on non-native elements
5. Inline handlers: `onclick`, `onchange` attributes

**Stage 3: Context Attachment**
For each interactive element, attach nearby non-interactive text content as context. A button with no label might have a paragraph next to it that explains its purpose. The pruner attaches up to 200 characters of sibling/parent text to each interactive element.

**Stage 4: Serialization**
Assign sequential integer indices to all interactive elements. This index is the primary way the LLM refers to elements in its action outputs, avoiding fragile CSS selector generation:

```
Page: https://github.com/org/repo/pull/1234/checks
Interactive elements:
  [1] link "commit abc123" — context: "triggered by push to feature/auth"
  [2] button "Re-run failed jobs" — context: "3 jobs failed"
  [3] link "build / test (ubuntu-22.04)" — status: failed
  [4] link "build / test (macos-13)" — status: passed
  [5] link "lint" — status: passed
```

The action `click(element_index=3)` is then dispatched to `BrowserController` which resolves index 3 back to the corresponding DOM element via the selector map built during serialization.

### 7.4 Handling Large Pages

Pages with 100+ interactive elements or 5,000+ words of text cannot be fully included in a single observation. The strategy is:

**Chunked observation with scroll position:**
The DOM extractor captures interactive elements in the current viewport first, then provides a count of elements below the fold. The LLM can call `scroll(direction="down")` to advance the viewport and receive a new observation. Each observation includes `[viewing elements 12-35 of 87 total]` to indicate pagination progress.

**Token budget enforcement:**
The observation serializer enforces a hard token budget (default: 3,000 tokens for DOM text, not counting system context). If the serialized DOM exceeds this budget, elements are truncated starting from the bottom. This ensures the most visible elements (typically the most relevant) are always included.

### 7.5 When to Use DOM vs Vision

The decision matrix:

| Page characteristic | Use DOM | Use Vision |
|---|---|---|
| Standard HTML form, table, or list | Yes | No |
| SVG icons with no accessible labels | No | Yes |
| Canvas-rendered content (charts, graphs) | No | Yes |
| React SPA with full accessibility implementation | Yes | No |
| React SPA with poor ARIA implementation | Partial | Fallback |
| Status indicators (colored circles, checkmarks) | Check ARIA first | Fallback if ARIA absent |
| PDF rendered in browser | No | Yes |
| Screenshot/image embedded in page | No | Yes |

The `DOMExtractor` reports a `dom_confidence` score (0.0–1.0) based on:
- Number of elements with missing ARIA labels
- Presence of `<canvas>` or SVG-heavy sections
- Ratio of interactive elements to total element count

If `dom_confidence < 0.6`, the observation pipeline automatically appends a screenshot and activates vision analysis for that step.

---

## 8. Vision Module Design

### 8.1 When to Activate Vision

Vision analysis is expensive in both time (screenshot capture adds ~200ms; multimodal inference adds ~1–3s) and tokens (a 1280×720 screenshot consumes ~1,400 vision tokens). It is activated only when:

1. **DOM confidence is low** (as described above — SVG-heavy pages, canvas content, missing ARIA)
2. **Visual verification is required** — the task explicitly says "screenshot the filled form" or "screenshot the CI failure." These are always screenshots regardless of DOM quality.
3. **Disambiguation needed** — the DOM text alone is ambiguous (e.g., two elements with the same label in different visual contexts)
4. **Progress confirmation** — after a multi-step action sequence (e.g., form fill), take a confirmatory screenshot to verify the final state matches expectations before proceeding

### 8.2 Screenshot Capture

Screenshots are captured via Playwright's `page.screenshot()` API. Standard configuration:

```python
async def capture(self, page: Page, full_page: bool = False, label: str = "") -> bytes:
    # Force light theme before capture for clean audit documentation
    await page.emulate_media(color_scheme="light")
    
    # Consistent viewport for reproducible screenshots
    await page.set_viewport_size({"width": 1280, "height": 900})
    
    screenshot_bytes = await page.screenshot(
        full_page=full_page,
        type="png",
        animations="disabled",   # freeze animations for deterministic captures
    )
    return screenshot_bytes
```

Two modes:
- **Viewport screenshot** (`full_page=False`): captures only the visible portion. Used for evidence captures at key moments — this is what an auditor would see.
- **Full-page screenshot** (`full_page=True`): captures the entire scrollable page. Used when the evidence is spread across a long page (e.g., a commit history or a LinkedIn profile).

### 8.3 Claude Vision Integration

When vision analysis is activated, the screenshot is sent to Claude as a multimodal message alongside the DOM observation text:

```python
messages = [
    {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": f"Current page DOM observation:\n{dom_text}\n\nPlease analyze this screenshot to answer: {vision_question}"
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(screenshot_bytes).decode()
                }
            }
        ]
    }
]
```

**Vision questions** are targeted, not open-ended:
- "What is the status of the 'build / test' check? Pass, fail, or pending?" (not "describe this page")
- "Is the form field labeled 'Start Date' filled with a value? If yes, what value?"
- "Which user approved this pull request? Look for a green checkmark next to a username."

Targeted questions reduce hallucination risk significantly. The model answers what it can see, rather than summarizing a complex page from scratch.

### 8.4 Hybrid DOM + Vision

The most accurate extraction combines both signals. For a GitHub CI check status:

1. DOM extraction finds: `<button aria-label="Show all checks">` (DOM knows there is a button)
2. Click that button to expand the checks section
3. DOM extraction of expanded section finds: links to each check name + some status text
4. If status text is missing (SVG icon only): activate vision with the question "What is the icon color/symbol next to 'build / test (ubuntu-22.04)'?"
5. Vision model answers: "Red X icon, indicating failure"
6. Combine: field `build_test_ubuntu` = `failed` (from vision), source: accessibility tree element index 3 + screenshot `03_checks.png`

This hybrid approach yields the highest accuracy because:
- DOM provides structure and interactable elements (fast, cheap, reliable)
- Vision provides semantic interpretation of purely visual content (accurate, targeted)
- Neither is used for what it does poorly

### 8.5 Image Preprocessing

Before sending screenshots to Claude, apply:
1. **Resize** to max 1280px wide (most screenshots are already 1280px; this handles edge cases)
2. **Compress** to JPEG quality 85 if the screenshot is >500KB (reduces token cost while preserving readability)
3. **Annotate** interactive elements found by DOM extraction with bounding box overlays if disambiguation is needed (this is the Set-of-Mark technique, used selectively not universally)

Set-of-Mark annotation is only applied when the DOM observation contains elements with the same label in different visual regions. Research shows it causes hallucination when applied universally on dense pages; the key is targeted use.

---

## 9. Action System

### 9.1 Action Registry

The ActionRegistry is a typed registry of all actions the agent can take. Each action has:
- A name (used by the LLM to select it)
- A description (included in the tool schema sent to Claude)
- A parameter schema (typed, validated before dispatch)
- An implementation (async Playwright call)
- An error handling policy

```python
@registry.register
class NavigateAction(BaseAction):
    name = "navigate"
    description = "Navigate the browser to a URL. Use for following links or going to known URLs."
    
    class Params(BaseModel):
        url: str = Field(description="The full URL to navigate to, including https://")
        wait_for: Literal["load", "networkidle", "domcontentloaded"] = "load"
    
    async def execute(self, page: Page, params: Params) -> ActionResult:
        try:
            await page.goto(params.url, wait_until=params.wait_for, timeout=30_000)
            return ActionResult(success=True, description=f"Navigated to {params.url}")
        except PlaywrightTimeoutError:
            return ActionResult(success=False, error=f"Timeout navigating to {params.url}")
```

### 9.2 Complete Action Set

| Action | Parameters | Purpose | Error Policy |
|---|---|---|---|
| `navigate` | `url`, `wait_for` | Go to a URL | Timeout → fail step, suggest alternative URL |
| `click` | `element_index` or `text` or `selector` | Click an interactive element | Not found → try alternate strategy, 3 retries max |
| `type` | `element_index`, `text`, `clear_first` | Type text into an input | Element not editable → fail clearly |
| `select` | `element_index`, `value` | Select from a dropdown | Value not in options → list available options in error |
| `scroll` | `direction`, `amount_px` | Scroll the page | No-op if at limit; report current scroll position |
| `wait` | `milliseconds`, `reason` | Wait for dynamic content | Hard cap at 10s; longer waits require explicit reasoning |
| `screenshot_evidence` | `label`, `full_page` | Capture evidence screenshot | Always succeeds; file saved to evidence folder |
| `extract_text` | `element_index` or `selector` | Read text from element | Not found → return empty string with warning |
| `record_fields` | `fields_json` | Record extracted field values | Invalid JSON → return error, do not record |
| `download_file` | `url`, `filename` | Download file to evidence folder | Non-200 → error; HTML response → likely redirect, error |
| `mark_checkpoint` | `checkpoint_name` | Satisfy a required checkpoint | No validation needed; idempotent |
| `make_judgment` | `question`, `answer`, `confidence`, `reasoning`, `evidence_refs` | Record audit judgment | Invalid answer → error; confidence must be 0.0–1.0 |
| `go_back` | — | Browser back navigation | No-op if no history; report current URL |
| `open_tab` | `url` | Open URL in a new tab | Returns tab index for future reference |
| `switch_tab` | `tab_index` | Switch to a different open tab | Tab not found → list open tabs |
| `close_tab` | `tab_index` | Close a tab | Cannot close last tab; error clearly |
| `finish` | `summary`, `status` | End the current sub-goal | Always terminates the loop |

### 9.3 Tool Schema for Claude

Actions are exposed to Claude via the Anthropic tool use API. Each action maps to one tool definition:

```python
def to_tool_schema(self) -> dict:
    return {
        "name": self.name,
        "description": self.description,
        "input_schema": {
            "type": "object",
            "properties": {
                field_name: {
                    "type": field_type,
                    "description": field_description
                }
                for field_name, field_type, field_description in self.param_fields
            },
            "required": self.required_params
        }
    }
```

The full tool list is included in every system prompt. Claude selects actions by calling tools, which produces a structured `tool_use` content block that the ActionRegistry dispatches.

### 9.4 Element Resolution Strategy

When the LLM specifies an element to interact with, it can use one of three strategies (tried in order until one succeeds):

1. **Index-based**: `click(element_index=5)` — fastest, most reliable. The index maps to a DOM node via the selector map built during DOM extraction.
2. **Text-based**: `click(text="Show all checks")` — searches for an element whose accessible name or visible text matches. Case-insensitive. Falls back to partial match if exact match fails.
3. **Selector-based**: `click(selector=".checks-summary button")` — only used as a last resort. CSS selectors are fragile across page renders and are explicitly discouraged in agent prompts.

If all three strategies fail, the action returns an error with the full list of currently-visible interactive elements, so the LLM can select a different element in the next step.

### 9.5 Action Error Handling

Every action returns an `ActionResult`, never raises. This means:
- The loop always continues after an action (it may choose to call `finish(status="failed")` but it decides that, not the code)
- Errors are descriptive: "Element with text 'Show all checks' not found. Available buttons: ['Request review', 'Merge pull request']"
- Consecutive failure tracking: `ReActLoop` counts how many consecutive `success=False` results have been returned. At 3 consecutive failures, it injects a "recovery prompt" before the next Think step

---

## 10. Scalability Design

### 10.1 Concurrency Model

The batch runner uses Python's `asyncio` with a semaphore to bound concurrent browser instances:

```python
class BatchRunner:
    def __init__(self, max_concurrent: int = 5):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.agent_pool = AgentPool(max_browsers=max_concurrent)
    
    async def run(self, task_plan: TaskPlan, samples: list[SampleInput]) -> BatchResult:
        tasks = [self._process_sample(task_plan, s) for s in samples]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return self._aggregate(results)
    
    async def _process_sample(self, plan: TaskPlan, sample: SampleInput) -> SampleResult:
        async with self.semaphore:
            ctx = await self.agent_pool.acquire(sample.sample_id)
            try:
                planner = PlannerAgent(plan, sample, ctx, self.file_manager_for(sample))
                return await planner.run()
            except Exception as e:
                return SampleResult.failed(sample.sample_id, error=str(e))
            finally:
                await self.agent_pool.release(sample.sample_id, ctx)
```

**Recommended concurrency settings by task type:**

| Task type | Recommended max_concurrent | Reason |
|---|---|---|
| Single-page extraction (Linear) | 8–10 | Low memory per page; no auth state needed |
| Graph traversal (GitHub audit) | 3–5 | Each sample may open 5+ pages; higher memory |
| LinkedIn enrichment | 2–3 | Rate limiting risk; auth session precious |
| Form fill + download | 2–4 | Downloads accumulate; storage I/O becomes bottleneck |

### 10.2 Progress Checkpointing

For batches of 100+ samples, the runner writes a `checkpoint.json` file after each completed sample:

```json
{
  "run_id": "run_2026-03-27_140000",
  "task": "github_audit",
  "total_samples": 1000,
  "completed_sample_ids": ["commit_abc123", "commit_def456", ...],
  "failed_sample_ids": ["commit_xyz789"],
  "last_updated": "2026-03-27T14:35:22Z"
}
```

On restart, the BatchRunner reads `checkpoint.json` and skips already-completed samples. This enables resumability without re-running successful work.

### 10.3 Memory Management

Long-running batches accumulate memory in two places: browser processes and Python heap.

**Browser process memory:**
- Each Chromium instance consumes 80–200MB depending on page complexity
- The AgentPool enforces a maximum browser count and evicts idle contexts after an idle threshold (default: 5 minutes)
- After eviction, the context is closed (not just detached); the Chromium process is released

**Python heap:**
- `SampleResult` objects are serialized to disk (`result.json`) and removed from the in-memory results list immediately after CSV writing
- Screenshots are written to disk immediately and never held in memory after saving
- The `BatchResult` accumulates only summary statistics (counts, durations), not full results

### 10.4 Rate Limiting

The system includes a per-domain rate limiter to prevent triggering bot detection or rate limit errors:

```python
class DomainRateLimiter:
    def __init__(self):
        self._locks: dict[str, asyncio.Semaphore] = {}
        self._min_interval: dict[str, float] = {
            "linkedin.com": 3.0,       # seconds between requests
            "github.com": 0.5,
            "jira.atlassian.com": 1.0,
            "default": 0.2,
        }
        self._last_request: dict[str, float] = {}
    
    async def acquire(self, domain: str):
        interval = self._min_interval.get(domain, self._min_interval["default"])
        last = self._last_request.get(domain, 0)
        elapsed = time.time() - last
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        self._last_request[domain] = time.time()
```

### 10.5 Progress Tracking

The batch runner emits structured progress events consumed by a rich console display and optionally by a log file:

```
[12/60] commit_abc123f  COMPLETED  (8 steps, 34.2s, 3 fields, 4 artifacts, 3 checkpoints)
[13/60] commit_def456a  COMPLETED  (12 steps, 58.7s, 3 fields, 5 artifacts, 3 checkpoints)
[14/60] commit_xyz789b  NEEDS_REVIEW  (30 steps, 120.0s, 2 fields, 3 artifacts, 2 checkpoints)
         checkout_failed: PR not found for this commit (direct push to main)
```

At batch completion:
```
Batch complete: 57 completed, 1 failed, 2 needs_review
Duration: 47m 23s | Avg per sample: 47.3s
Checkpoint pass rates: commit_screenshot 60/60, pr_screenshot 57/60, checks_screenshot 57/60
Top errors: "PR not found (direct push)" ×2, "CI page timed out" ×1
```

---

## 11. Evidence Pipeline

### 11.1 Per-Sample Folder Structure

Every sample produces a stable folder with a deterministic internal structure:

```
evidence/
└── run_2026-03-27_140000/
    ├── run_summary.json           # Batch-level stats and checkpoint pass rates
    ├── results.csv                # One row per sample, all extracted fields
    ├── checkpoint.json            # Resumability state
    └── samples/
        └── commit_abc123f/
            ├── 01_commit_page.png
            ├── 02_pr_page.png
            ├── 03_checks_expanded.png
            ├── 04_ci_failure_detail.png
            ├── 05_jira_ticket.png
            ├── result.json
            └── action_log.json
```

The numeric prefix on screenshots encodes the order they were captured. The label after the prefix encodes what the screenshot shows. Both are assigned deterministically by the FileManager, not by the agent — the agent only provides a label string; the counter is managed by the FileManager.

### 11.2 SHA-256 Hashing

Every artifact (screenshot or downloaded file) receives a SHA-256 hash computed immediately after writing to disk:

```python
def save_screenshot(self, data: bytes, label: str, source_url: str) -> EvidenceArtifact:
    self._counter += 1
    filename = f"{self._counter:02d}_{label}.png"
    path = self.sample_dir / filename
    path.write_bytes(data)
    sha256 = hashlib.sha256(data).hexdigest()
    
    artifact = EvidenceArtifact(
        type=EvidenceType.SCREENSHOT,
        filename=filename,
        sha256=sha256,
        source_url=source_url,
        timestamp=datetime.utcnow(),
    )
    self._artifacts.append(artifact)
    return artifact
```

The hash is stored in `result.json` and in the `results.csv`. An auditor or reviewer can verify that a screenshot has not been modified after collection by recomputing the hash.

### 11.3 result.json Schema

```json
{
  "sample_id": "commit_abc123f",
  "status": "completed",
  "needs_review_reasons": [],
  "input": {
    "sample_id": "commit_abc123f",
    "url": "https://github.com/org/repo/commit/abc123f",
    "extra_fields": {}
  },
  "extracted_fields": [
    {
      "field_name": "pr_creator",
      "value": "alice",
      "source_url": "https://github.com/org/repo/pull/1234",
      "source_selector": "[aria-label='Author'] a",
      "artifact_ref": "02_pr_page.png",
      "confidence": 1.0
    }
  ],
  "artifacts": [
    {
      "type": "screenshot",
      "filename": "01_commit_page.png",
      "path": "samples/commit_abc123f/01_commit_page.png",
      "sha256": "a3f9...",
      "source_url": "https://github.com/org/repo/commit/abc123f",
      "timestamp": "2026-03-27T14:15:03Z",
      "checkpoint_ref": "commit_screenshot"
    }
  ],
  "checkpoints_met": ["commit_screenshot", "pr_screenshot", "checks_screenshot"],
  "checkpoints_missed": [],
  "judgment": null,
  "action_log": [
    {
      "step": 1,
      "action": "navigate",
      "target": "https://github.com/org/repo/commit/abc123f",
      "result": "Navigated successfully",
      "timestamp": "2026-03-27T14:15:01Z"
    }
  ],
  "errors": [],
  "started_at": "2026-03-27T14:15:00Z",
  "completed_at": "2026-03-27T14:15:37Z",
  "steps_taken": 8,
  "retries_used": 0
}
```

### 11.4 CSV Output

The `CSVWriter` produces a flat CSV with:
- Core columns: `sample_id`, `status`, `started_at`, `completed_at`, `steps_taken`
- One column per extracted field (from `TaskPlan.output_fields`)
- One column per artifact: `{label}_screenshot` with the filename value
- `errors` column: semicolon-separated list of error messages
- `needs_review_reasons` column: semicolon-separated list of reasons

Field type coercion happens in the CSVWriter, not in the agent. The agent always records raw string values; the writer applies the `FieldSpec` type coercions (int, float, date, url) before writing.

### 11.5 Action Log

The `action_log.json` is a structured audit trail of every step the agent took. It is separate from `result.json` to keep the result manifest focused on evidence and keep the action log focused on process.

Each entry records the LLM's internal reasoning (`thinking` field), the action selected, the parameters, and the outcome. This makes runs fully reviewable — an engineer can open `action_log.json` and understand exactly what the agent did at every step without re-running the browser.

---

## 12. Technology Stack

### 12.1 Core Dependencies

| Package | Version | Purpose |
|---|---|---|
| `playwright` | >=1.42 | Browser control: chromium, page navigation, DOM inspection, screenshot, download |
| `anthropic` | >=0.25 | LLM client: Claude API, tool use, multimodal messages, prompt caching |
| `pydantic` | >=2.0 | All data models: task config, evidence, results, actions, judgments |
| `asyncio` | stdlib | Concurrent sample execution, semaphore, timeout |
| `Pillow` | >=10.0 | Screenshot preprocessing: resize, compress, annotate |

### 12.2 What Is Explicitly Excluded

| Package | Why Excluded |
|---|---|
| `browser-use` | Andera explicitly prohibits agent framework dependencies |
| `langchain` | Same; also adds unnecessary abstraction overhead |
| `openai` | Not needed; Anthropic SDK is used directly |
| `selenium` | Replaced by Playwright (async-native, more reliable) |
| `beautifulsoup4` | DOM parsing is done via Playwright's accessibility tree, not HTML parsing |
| `scrapy` | Scraping framework; this is an agent, not a scraper |

### 12.3 Configuration

All configuration via environment variables (`.env` file):

```bash
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-opus-4-5                # Primary model for complex tasks
LLM_FAST_MODEL=claude-haiku-4-5          # Fast model for simple extractions
MAX_CONCURRENT_BROWSERS=5
EVIDENCE_BASE_DIR=./evidence
AUTH_STORAGE_STATE=                       # Path to playwright auth state JSON (optional)
MAX_STEPS_DEFAULT=30
STEP_TIMEOUT_SECONDS=120
VISION_DOM_CONFIDENCE_THRESHOLD=0.6      # Below this: activate vision
RATE_LIMIT_LINKEDIN=3.0                  # Seconds between LinkedIn requests
```

### 12.4 Model Selection Strategy

Two models are used:

**Primary model (claude-opus-4-5 or equivalent):**
Used for: PlannerAgent reasoning, NavigatorAgent loop, JudgeAgent judgment, any step involving complex multi-step reasoning or visual analysis.

**Fast model (claude-haiku-4-5 or equivalent):**
Used for: ExtractorAgent field extraction from clean DOM text, DOM confidence scoring, simple yes/no page state checks that do not require reasoning.

The cost-quality tradeoff: the expensive model handles strategy and judgment; the cheap model handles the mechanical extraction work. This reduces cost by approximately 60% on extraction-heavy tasks without affecting accuracy.

---

## 13. Implementation Plan

### Phase 1: Core Layer (Week 1)

The core layer is the foundation. Nothing else can be built until the agent loop, browser controller, and DOM extractor are working.

- [ ] `core/llm_client.py`: Anthropic SDK wrapper with tool use support, prompt caching headers, streaming for long responses. Write unit tests against mocked API responses.
- [ ] `core/browser_controller.py`: Playwright async wrapper. Implement: `navigate`, `click` (3 resolution strategies), `type`, `select`, `scroll`, `wait`, `go_back`. Add timeout handling per action. Write tests using Playwright's built-in test recorder against a local HTML fixture.
- [ ] `core/dom_extractor.py`: Playwright accessibility tree snapshot. Implement the four-stage pruning pipeline. Implement element index assignment and selector map. Target output: <3,000 tokens for a typical enterprise page.
- [ ] `core/action_registry.py`: Typed action registry. Implement all 16 actions from the action table. Implement tool schema generation for Claude tool use format.
- [ ] `core/memory_manager.py`: Rolling buffer of last 10 full observations + summarization of older entries. Implement token budget enforcement.
- [ ] `core/react_loop.py`: Assemble the full ReAct cycle using the above components. Implement step limit, loop detection, graceful termination, error wrapping.

**Milestone:** A working ReAct agent that can navigate to a URL, take a screenshot, extract text fields, and produce a `SampleResult`. Test against a Linear ticket.

---

### Phase 2: Agent Layer (Week 2)

- [ ] `agents/base_agent.py`: Abstract base with `run()`, context builder, sub-goal schema.
- [ ] `agents/navigator_agent.py`: Wraps `ReActLoop` with sub-goal-focused prompting. Implements the tab-per-subgoal pattern for browser context sharing.
- [ ] `agents/extractor_agent.py`: Short focused loop for field extraction. Implements hybrid DOM + vision extraction. Validates extracted values against `FieldSpec` types.
- [ ] `agents/judge_agent.py`: Evidence-based judgment loop. Receives artifact references. Outputs `JudgmentResult` with full reasoning trace.
- [ ] `agents/planner_agent.py`: Task decomposition into sub-goals. Sub-agent dispatch. Result aggregation. Checkpoint satisfaction tracking.

**Milestone:** PlannerAgent can run the full GitHub audit task (commit → PR → checks → CI → Jira) for a single sample with correct screenshot naming, field extraction, and checkpoints.

---

### Phase 3: Vision Module (Week 2)

- [ ] `core/vision_module.py`: Screenshot capture with consistent viewport/theme settings. DOM confidence scoring. Targeted vision question construction. Claude multimodal message assembly.
- [ ] Image preprocessing: resize, compress, Set-of-Mark annotation (selective).
- [ ] Integration with `ReActLoop`: automatic vision activation when `dom_confidence < threshold`.
- [ ] Integration with `ExtractorAgent`: screenshot + vision for ambiguous fields.

**Milestone:** Agent correctly extracts CI check pass/fail status from GitHub's SVG-based status icons using vision fallback.

---

### Phase 4: Orchestrator Layer (Week 3)

- [ ] `orchestrator/task_parser.py`: YAML task definition loading. Natural language prompt → `TaskPlan` conversion (using Claude to parse the intent into typed fields). Input file parsing (Excel, CSV, plain text list).
- [ ] `orchestrator/agent_pool.py`: Browser context pool with acquire/release/evict lifecycle. Auth state loading (`playwright auth state` JSON). Idle eviction background task.
- [ ] `orchestrator/batch_runner.py`: Semaphore-bounded concurrent execution. Progress checkpointing to `checkpoint.json`. Per-domain rate limiter. Rich console progress display.

**Milestone:** BatchRunner can process 60 GitHub commits concurrently (max 5 at a time), produce all evidence folders, and write a complete `results.csv`.

---

### Phase 5: Output Layer and Integration (Week 3)

- [ ] `output/file_manager.py`: Per-sample folder creation. Sequential artifact numbering. SHA-256 hashing. `result.json` and `action_log.json` writing.
- [ ] `output/csv_writer.py`: Batch CSV with field coercion, stable column ordering, proper encoding.
- [ ] `output/evidence_packager.py`: `run_summary.json` with checkpoint pass rates, error summaries, per-sample durations. Manifest validation (every artifact in `result.json` must exist on disk).
- [ ] `run.py`: CLI entry point with `--task`, `--input`, `--output`, `--concurrency` flags. YAML task loading. Natural language mode (`--prompt`).

**Milestone:** Full end-to-end run of all 5 example task types with correct outputs.

---

### Phase 6: Robustness and Testing (Week 4)

- [ ] Error recovery: test loop detection, partial evidence preservation, graceful degradation.
- [ ] Retry logic: per-sample retry with clean slate (wipe artifacts from prior attempt).
- [ ] Auth state management: test `playwright auth state` loading for LinkedIn.
- [ ] Rate limiting: verify LinkedIn enrichment does not trigger bot detection at max concurrency.
- [ ] Resumability: interrupt a batch mid-run and verify restart from checkpoint.
- [ ] Token budget: verify DOM observations stay within budget on complex pages (GitHub PR with 100+ check results).
- [ ] Integration tests: one test per task family using recorded Playwright fixtures (not live sites).

---

### Deliverable Checklist

- [ ] All 5 task families executable end-to-end
- [ ] Per-sample evidence folders with correct naming and SHA-256 hashes
- [ ] `results.csv` with all extracted fields, correct types
- [ ] `result.json` per sample with full provenance (source URLs, artifact refs, confidence)
- [ ] `action_log.json` per sample showing every agent step
- [ ] `run_summary.json` with checkpoint pass rates and error breakdown
- [ ] Natural language task parsing working (`--prompt` mode)
- [ ] Concurrent batch execution at 5+ samples in parallel
- [ ] Resumable batches via `checkpoint.json`
- [ ] No external agent framework dependencies
```

---

The complete document above is ready to save to `c:\Users\Humblebee\Documents\GitHub\browser_use_agent\playwright_agent\ARCHITECTURE.md`.

You will need to create the `playwright_agent/` directory first, then save the content. On Windows with bash:

```bash
mkdir -p /c/Users/Humblebee/Documents/GitHub/browser_use_agent/playwright_agent
