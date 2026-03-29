# Roadmap — Browser Evidence Agent

---

## What We Have (Strong)

| Capability | Status |
|-----------|--------|
| Custom ReAct loop with structured reflection | Done |
| 12 typed actions (goto, click, type, scroll, screenshot, extract, wait, download, select_option, save_progress, done, fail) | Done |
| DOM-first perception (a11y tree + CDP fallback) | Done |
| Vision fallback with targeted questions | Done |
| Parallel batch execution with bounded concurrency | Done |
| Evidence pipeline (SHA-256, atomic writes, per-sample folders) | Done |
| Run-scoped memory (patterns + failures) | Done |
| Natural language prompt → auto task spec | Done |
| Resume after crash (idempotent restart) | Done |
| Download any file type + native select dropdowns | Done |
| Escalating recovery (stagnation detection, budget pressure, spam detection) | Done |
| Form fill with input value enrichment | Done |
| Structured logging (console + file + JSONL) | Done |
| 117 regression tests | Done |

---

## What's Pending

### Priority 1 — Critical (next to build)

| Feature | Effort | Impact | Description |
|---------|--------|--------|-------------|
| Multi-agent coordination | 4 hr | Critical | Planner decomposes "audit 60 commits" into sub-tasks. Coordinator agent assigns work, monitors progress, handles failures. Sub-agents report back. Not just parallel workers — intelligent task division with role awareness |
| Shared live memory across sub-agents | 2 hr | Critical | Sub-agents share a live memory bus. Agent 1 learns "checks section requires scroll" → Agent 2 reads it mid-run. Implements: `save_progress` writes tips to shared memory, agents reload every 3-5 steps |
| Multi-tab support | 30 min | High | `open_tab(url)`, `switch_tab(index)`, `close_tab(index)` — needed for comparing pages, cross-referencing data |
| File upload action | 15 min | Medium | `upload_file(selector, filepath)` via `locator.set_input_files()` — needed for form submissions with attachments |

### Priority 2 — Medium Effort

| Feature | Effort | Impact | Description |
|---------|--------|--------|-------------|
| Persistent memory (opt-in) | 1 hr | Medium | Re-enable cross-run memory with `ENABLE_PERSISTENT_MEMORY=true` flag. Key by `domain:task_id` to prevent cross-task contamination |
| iframe support | 1 hr | High | `switch_to_frame(selector)` + auto-detect iframes in DOM extractor. Many enterprise apps (Workday, ServiceNow) use iframes |
| Drag-and-drop | 1 hr | Low | `drag(source, target)` via Playwright's `drag_to()`. Needed for Kanban boards, file organizers |
| Complex widget handling | 2 hr | Medium | Date pickers, autocomplete, rich text editors — each needs specific interaction patterns |
| Multi-item discovery from NL | 2 hr | High | Planner generates discovery phase for "find all X on this page" prompts instead of single-sample extraction |

### Priority 3 — Larger Features 

| Feature | Effort | Impact | Description |
|---------|--------|--------|-------------|
| WebVoyager benchmark | 4 hr | High | Run against the standard browser agent benchmark. Proves credibility with a public score |
| API integration mode | 4 hr | Medium | Allow tasks to mix browser actions with REST API calls (e.g., check GitHub API alongside browser navigation) |
| Streaming/live monitoring | 4 hr | Medium | WebSocket or SSE endpoint for real-time step-by-step progress in a dashboard |
| Autonomous login flow | 4 hr | Medium | Agent can fill login forms and handle 2FA prompts (currently uses saved cookies) |
| PDF/document reading | 8 hr | High | After downloading a file, parse its contents (PDF text, Excel data) and include in extraction |
| (moved to Priority 1) | — | — | Multi-agent coordination + shared live memory |

---


- Complete Priority 1 (all 4 items) + iframe support + persistent memory + WebVoyager benchmark.

- Document(PDF) reading + multi-agent coordination + streaming dashboard + community testing and edge case hardening.
