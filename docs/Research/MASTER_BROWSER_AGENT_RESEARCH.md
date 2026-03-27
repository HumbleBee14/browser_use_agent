# Master Browser Agent Research Report
## Building an Exceptional Browser Use Agent for Andera AI

**Date**: March 26, 2026 | **Compiled from**: 3 parallel deep research agents + Andera context analysis

---

## Executive Summary

The browser agent space has exploded in 2024-2026. **14+ open-source agents** and **5 major commercial players** are competing. The winning architecture pattern is clear: **hybrid DOM + vision, hierarchical planning, compressed memory, and aggressive error recovery**. Browser-use leads open-source at 89.1% on WebVoyager with 84.7k stars. For Andera's SOX compliance use case, we need an agent that excels at **enterprise web app navigation, document download/parsing, multi-step workflows, and audit-trail generation**.

### Key Numbers at a Glance

| Metric | Value |
|--------|-------|
| Best open-source benchmark (WebVoyager) | 89.1% (browser-use) |
| Best commercial benchmark | 87% (OpenAI Operator) |
| Human baseline | ~89% WebVoyager, ~78% WebArena |
| Hybrid vs pure-AI error reduction | 23x fewer errors |
| DOM snapshot size vs screenshot | 2-5KB vs 100KB+ |
| Cost per reasoning step | ~$0.0048 |
| Cost reduction with prompt caching | ~89% |
| Bulk action speedup | 57% faster, 74% fewer calls |
| DOM pruning (Prune4Web) | 25-50x element reduction |

---

## Part 1: The Competitive Landscape

### Tier 1 - Market Leaders (10k+ stars, active development)

| Agent | Stars | Arch | Benchmark | Best For |
|-------|-------|------|-----------|----------|
| **browser-use** | 84.7k | Hybrid DOM+Vision | 89.1% WV | Full autonomy, Python ecosystem |
| **Crawl4AI** | 51k | Async crawler + LLM | N/A | Data extraction pipelines |
| **Stagehand** | 21k+ | Hybrid DOM+AI | ~75% WV | TypeScript, action caching, cost control |
| **Skyvern** | 20.3k | Vision-primary | 85.8% WV | No-code, CAPTCHA, 2FA, government portals |

### Tier 2 - Specialized / Research (1k-10k stars)

| Agent | Stars | Unique Innovation |
|-------|-------|-------------------|
| **LaVague** | 6.3k | "Large Action Model" - compiles NL to automation code |
| **Playwright MCP** | Growing fast | Accessibility-tree-only, 2-5KB snapshots, Microsoft-backed |
| **OpenAdapt** | 2k+ | Learn-by-demonstration + privacy-first PII redaction |
| **Agent-E** | 1.2k | Hierarchical planner+navigator, 52% self-aware failure detection |
| **AgentQ** | 1k+ | MCTS + RL: 18.6% -> 95.4% with DPO fine-tuning |
| **OpenCUA** | 723 | Open-weights 7B-72B models, cross-OS, NeurIPS 2025 Spotlight |
| **SeeAct** | 1k+ | Pioneered vision grounding, ICML 2024 |

### Tier 3 - Commercial / Production

| Agent | Score | Pricing | Architecture |
|-------|-------|---------|-------------|
| **OpenAI Operator/CUA** | 87% WV, 58% WA | ChatGPT Plus/Pro | Screenshot + RL |
| **Claude Computer Use** | ~56% WA | API pricing | Screenshot + fallback hierarchy |
| **Google Mariner** | N/A | $249.99/mo (AI Ultra) | Chrome extension, teach-and-repeat |
| **Convergence Proxy** | Claims best | Free-$20/mo | Proprietary LMLMs |
| **Adept AI** | N/A | Dormant | Acquired by Amazon |

---

## Part 2: Architecture Patterns That Win

### 2.1 The Hybrid DOM + Vision Pattern (Industry Consensus)

**The data is clear**: hybrid approaches yield ~23x fewer errors than pure-AI browsing.

```
Decision Flow:
  1. ALWAYS capture accessibility tree snapshot (2-5KB, fast, cheap)
  2. Is content visual-only (canvas, image, chart)?
     YES -> Delegate to vision model with bounding boxes
     NO  -> Use accessibility tree for action planning
  3. Is grounding ambiguous?
     YES -> Cross-reference with screenshot
     NO  -> Execute from accessibility tree
```

**Information degradation chain** (rtrvr.ai data):
```
Raw HTML      = 100% information
Rendered page = ~60%
OCR/Vision    = ~40%
Interpretation = ~30%
```

**Conclusion**: DOM-first is objectively better for most tasks. Vision is a fallback, not the primary.

### 2.2 DOM Processing Pipeline (browser-use's approach)

The 4-stage pipeline that processes 500-2000 elements in 10-100ms:

```
Stage 1: Data Collection
  - 5 parallel CDP requests: DOM tree, accessibility tree, page snapshot, viewport metrics, event listeners

Stage 2: Data Fusion
  - DomService merges structural (DOM) + semantic (accessibility) + visual (snapshot)
  - Output: EnhancedDOMTreeNode structures

Stage 3: Serialization
  - Simplification: remove non-interactive clutter
  - Paint-order filtering: hide visually occluded elements
  - Bounding-box filtering: collapse nested clickables
  - Index assignment: numeric index per interactive element

Stage 4: Output
  - SerializedDOMState (for LLM prompts)
  - DOMSelectorMap (for action execution reverse lookup)
```

**Element detection heuristics**: native interactive tags, ARIA roles, JS event listeners (`getEventListeners()` via CDP), CSS `cursor: pointer`, inline `onclick`, scrollable containers.

### 2.3 DOM Pruning (Critical for Token Efficiency)

Real pages have 10,000-100,000 DOM tokens. Must prune aggressively.

| Technique | Reduction | Accuracy |
|-----------|-----------|----------|
| **Rule-based filtering** (strip scripts, styles, non-interactive) | 5-10x | Baseline |
| **Prune4Web** (LLM-generated scoring programs) | 25-50x | 88.28% grounding |
| **Agent-E distillation** (3 modes: text_only, input_fields, all_fields) | Variable | 73.2% WV |

**Prune4Web** is the most innovative: instead of the LLM filtering DOM directly, it generates a Python scoring function that ranks elements by relevance. Top-N candidates (default 20) go to the main LLM.

### 2.4 Hierarchical Planning (Agent-E Pattern)

```
[User Task]
     |
     v
[Planner Agent] -- Decomposes into sub-goals, verifies outcomes, backtracks
     |
     v (sub-task)
[Navigator Agent] -- Fresh instance per sub-task (no context overflow)
     |               Uses DOM distillation, executes primitive skills
     |               Reports success/failure back to planner
     v
[DOM Change Observer] -- MutationObserver tracks DOM mutations
                         Generates linguistic feedback ("dropdown opened")
```

**Why this works**: Fresh navigator per sub-task prevents the #1 killer of long-running agents: context window overflow. The planner maintains global state while navigators stay focused.

**Performance**: 73.2% on WebVoyager, avg 25 LLM calls per task (6.4 planner + 18.6 navigation).

### 2.5 Memory & Context Management

**The problem**: After 15 actions, naive approaches hit 43,000+ tokens. Unsustainable.

**Solution - Compressed history**:
- Rolling buffer of 40-50 most recent steps (full detail)
- Older steps summarized by agent-generated memory
- ONLY current accessibility snapshot (not historical ones)
- Stabilizes at ~12,600 tokens

**Prompt ordering for prefix caching** (~89% cost reduction):
```
1. Static system prompt     (unchanged between steps)
2. Session context          (per-session)
3. Tab state                (on navigation)
4. Conversation history     (appends each step)
5. Page snapshot            (changes every action)
```

### 2.6 Error Recovery (Table Stakes in 2026)

**Rule #1: NEVER retry identical failed actions.**

```
Error Recovery Hierarchy:
  1. Analyze failure (error message, page state, element availability)
  2. Try fundamentally different approach
  3. Reconsider the goal itself
  4. Escalate to human (if configured)
```

**Key techniques**:
- **Action loop detection**: browser-use's `ActionLoopDetector` identifies repetitive behavior, injects "nudge" messages
- **Element versioning**: refs with version IDs (e.g., `1:10`) - prevents clicking wrong element after DOM change
- **Self-healing selectors**: adaptive DOM selectors that adjust when page structure changes
- **Hierarchical recovery**: navigator reports failure to planner, planner reformulates approach
- **Consecutive failure tracking**: max 3 consecutive failures before escalation

### 2.7 Bulk Action Optimization

From "Building Browser Agents" paper:
- Batching independent actions: **74% fewer tool calls** (10 vs 38)
- **57% faster** execution (104.5s vs 245.1s)
- Time-aware prompts: "Each tool call takes ~3-5s; batch aggressively"

---

## Part 3: browser-use Deep Dive (Primary Reference Implementation)

### Architecture

```
Agent.run() loop:
  1. SENSE  -> BrowserSession.get_browser_state_summary()
              (DOM + optional screenshot via 5 parallel CDP requests)
  2. THINK  -> MessageManager constructs LLM prompt
              (task + browser state + history + loop detection nudges)
  3. ACT    -> LLM returns AgentOutput -> Tools executes via event dispatch
  4. RECORD -> Results stored in AgentHistoryList
```

### Key Configuration for Our Use Case

```python
# Andera-optimized browser-use setup
agent = Agent(
    task="Navigate to SharePoint, download Q4 audit evidence Excel files",
    llm=ChatBrowserUse(),           # Or ChatOpenAI(model="gpt-4o")
    max_steps=50,                    # Conservative billing rail
    use_vision="auto",               # DOM-first, vision fallback
    use_thinking=True,               # Enable internal reasoning
    max_actions_per_step=3,          # Batch actions
    output_model_schema=AuditResult, # Structured Pydantic output
    sensitive_data={                  # Secure credential handling
        "x_username": "auditor@company.com",
        "x_password": "secret",
    },
)

browser = Browser(
    headless=False,
    allowed_domains=["*.sharepoint.com", "*.auditboard.com"],
    downloads_path="/tmp/audit_downloads",
    accept_downloads=True,
    auto_download_pdfs=True,
    keep_alive=True,
    minimum_wait_page_load_time=0.5,  # Enterprise apps are slow
    wait_for_network_idle_page_load_time=1.0,
)
```

### Custom Actions (Tools Decorator Pattern)

```python
tools = Tools()

@tools.action(description='Save extracted audit data to Excel workpaper')
async def save_workpaper(data: list[AuditFinding], browser_session: BrowserSession) -> ActionResult:
    # Custom logic to format audit findings into Excel
    df = pd.DataFrame([f.model_dump() for f in data])
    df.to_excel("workpaper_output.xlsx", index=False)
    return ActionResult(
        extracted_content=f"Saved {len(data)} findings to workpaper",
        include_in_memory=True  # Persist in agent memory
    )

@tools.action(
    description='Take evidence screenshot with timestamp',
    allowed_domains=['*.sharepoint.com']
)
async def capture_evidence(browser_session: BrowserSession) -> ActionResult:
    page = await browser_session.must_get_current_page()
    screenshot = await page.screenshot()
    # Save with audit timestamp
    path = f"evidence_{datetime.now().isoformat()}.png"
    with open(path, 'wb') as f:
        f.write(screenshot)
    return ActionResult(extracted_content=f"Evidence captured: {path}")
```

### Agent History (Audit Trail)

```python
history = await agent.run(max_steps=50)

# Complete audit trail
history.urls()                    # All pages visited
history.screenshot_paths()        # Visual evidence
history.action_names()            # Every action taken
history.extracted_content()       # All extracted data
history.errors()                  # Failures encountered
history.model_thoughts()          # LLM reasoning at each step
history.total_duration_seconds()  # Execution time
```

### Known Limitations Relevant to Us

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| PDF viewer interaction issues | Can't interact with browser PDF viewers | Use `auto_download_pdfs=True` to bypass |
| Cloudflare WAF bypass | Enterprise sites may block | Use cloud browsers with stealth profiles |
| Non-deterministic results | Audit needs consistency | Add validation layers, structured output schemas |
| No action caching | Higher cost for repeated workflows | Consider Stagehand for repetitive tasks |
| Continuous LLM cost per step | Expensive at scale | Use flash_mode, limit max_steps, use smaller models for simple tasks |

---

## Part 4: What Andera Needs vs. What Exists

### Mapping Andera Requirements to Available Solutions

| Andera Need | Best Available Solution | Gap? |
|-------------|----------------------|------|
| Navigate enterprise web apps (SharePoint, AuditBoard) | browser-use with allowed_domains + auth | Low - well supported |
| Handle authentication & sessions | browser-use `sensitive_data` + `storage_state` | Low - built-in |
| Download Excel/PDF/Word from web | browser-use `downloads_path` + `auto_download_pdfs` | Low - built-in |
| Parse documents with LLM reasoning | Custom action + LLM chain (outside browser agent) | Medium - need custom pipeline |
| Multi-step audit workflows | Hierarchical planning (Agent-E pattern) | Medium - need to implement |
| Screenshot evidence with timestamps | Custom action (trivial to build) | None |
| Fault tolerance & retries | browser-use error handling + custom recovery | Low - extend existing |
| Format adaptability (different Excel layouts) | LLM reasoning over extracted content | Medium - core challenge |
| Audit trail / evidence log | browser-use `AgentHistoryList` | Low - built-in |
| Handle dynamic JS-heavy pages | CDP-based DOM + wait strategies | Low - built-in |
| CAPTCHA / bot detection bypass | Cloud browsers or Skyvern integration | Medium - external dependency |

### Architecture Recommendation for Andera

```
                         [User: "Test SOX control X against evidence Y"]
                                          |
                                          v
                    +---------------------------------------------+
                    |           ORCHESTRATOR (Planner)             |
                    |  - Decomposes audit task into sub-goals      |
                    |  - Tracks progress across control tests      |
                    |  - Manages evidence chain                    |
                    +---------------------------------------------+
                         |              |              |
                         v              v              v
                  [Navigator 1]   [Navigator 2]   [Navigator 3]
                  "Download       "Extract data    "Fill workpaper
                   evidence        from Excel       template on
                   from            using LLM        audit platform"
                   SharePoint"     reasoning"
                         |              |              |
                         v              v              v
                    +---------+   +---------+   +---------+
                    | Browser |   | Document|   | Browser |
                    | Agent   |   | Parser  |   | Agent   |
                    | (CDP)   |   | (LLM)   |   | (CDP)   |
                    +---------+   +---------+   +---------+
                         |              |              |
                         v              v              v
                    +---------------------------------------------+
                    |           AUDIT TRAIL & EVIDENCE             |
                    |  - Screenshots at each step                  |
                    |  - Action logs with timestamps               |
                    |  - LLM reasoning traces                      |
                    |  - Downloaded files with hashes              |
                    +---------------------------------------------+
```

---

## Part 5: Lessons & Strategic Recommendations

### What to Learn from Each Agent

| Agent | Key Lesson to Adopt |
|-------|-------------------|
| **browser-use** | CDP-based DOM pipeline, event-driven architecture, action loop detection |
| **Stagehand** | Action caching for repeated workflows (huge cost saver) |
| **Skyvern** | Vision-first fallback for broken DOM, no-code workflow builder concept |
| **Agent-E** | Hierarchical planner+navigator with fresh instantiation per sub-task |
| **AgentQ** | MCTS for complex multi-step tasks (future consideration) |
| **Playwright MCP** | Accessibility tree as primary observation (2-5KB vs 100KB) |
| **Prune4Web** | LLM-generated scoring programs for DOM pruning (88.28% accuracy) |
| **OpenCUA** | Reflective Chain-of-Thought for self-correction |

### Build vs. Buy Decision

| Approach | Pros | Cons | Recommendation |
|----------|------|------|---------------|
| **Build on browser-use** | Largest community, MIT license, Python, 89.1% benchmark, extensive API | No caching, continuous LLM cost, non-deterministic | **Start here** - best foundation |
| **Build on Stagehand** | Action caching, TypeScript, deterministic+AI hybrid | TypeScript (not Python), lower benchmark | Consider for cost-sensitive repetitive workflows |
| **Build on Skyvern** | Production-ready, CAPTCHA/2FA built-in, no-code | AGPL license (restrictive), vision-first = expensive | Use as reference, not foundation (license issue) |
| **Build from scratch** | Full control, optimized for audit domain | Enormous effort, no community | Not recommended for initial version |

### Recommended Tech Stack

```
Browser Control:    CDP (Chrome DevTools Protocol) - direct, no middleware
LLM (Primary):     Claude Opus 4.6 or GPT-4o (complex reasoning)
LLM (Fast/Cheap):  Claude Haiku 4.5 or GPT-4o-mini (simple extractions)
LLM (Local):       Qwen 72B or browser-use BU-30B (privacy-sensitive)
Framework:         browser-use 0.12.x (MIT, Python, best community)
Protocol:          MCP (Model Context Protocol) for tool interop
Document Parsing:  Custom LLM pipeline (Excel/PDF/Word -> structured data)
Observability:     AgentHistoryList + custom logging
Infrastructure:    Docker + cloud browsers for CAPTCHA bypass
```

### Top 10 Things That Will Impress Andera

1. **Hybrid DOM+Vision** with intelligent switching (not just screenshots)
2. **Hierarchical task planning** - break audit workflows into sub-goals
3. **Structured output** with Pydantic schemas for audit findings
4. **Complete audit trail** - every action logged with timestamps and screenshots
5. **Fault tolerance** - graceful error recovery, never retry same failure
6. **Format adaptability** - LLM reasons about document structure, not brittle selectors
7. **Document-aware** - native handling of Excel, PDF, Word downloads and parsing
8. **Enterprise auth** - secure credential handling with `sensitive_data` pattern
9. **Evaluation framework** - benchmark your agent against test scenarios
10. **Cost efficiency** - compressed history, prompt caching, smaller models for simple tasks

---

## Part 6: Security Considerations

**Critical**: Prompt injection is the #1 vulnerability (73% of production AI deployments affected).

- CVE-2025-47241 affects browser-use directly
- Every webpage, ad, iframe is an attack vector
- OpenAI acknowledges prompt injection may never be fully "solved"

**Required defenses**:
1. Dedicated, isolated browser profiles
2. Human-in-the-loop confirmations for sensitive actions
3. `allowed_domains` / `prohibited_domains` restrictions
4. Input/output sanitization
5. Sandboxed execution environments
6. Never disable security features in production

---

## Appendix: Key Research Papers

| Paper | Venue | Year | Key Finding |
|-------|-------|------|-------------|
| WebArena | ICLR | 2024 | Gold standard sandboxed benchmark (812 tasks) |
| SeeAct | ICML | 2024 | SoM causes hallucination; best grounding uses HTML+vision |
| VisualWebArena | ACL | 2024 | 910 tasks requiring visual understanding |
| Agent-E | 2024 | 2024 | Hierarchical planning + DOM change observation |
| Prune4Web | 2025 | 2025 | LLM-generated scoring for 25-50x DOM reduction |
| Building Browser Agents | 2025 | 2025 | Accessibility-first, element versioning, bulk actions |
| OpenCUA | NeurIPS | 2025 | Open-weights CUA models, 45% on OSWorld |
| Plan-and-Act | 2025 | 2025 | Separated planning from execution for long-horizon tasks |

## Appendix: All Source Links

### Agent Repositories
- [browser-use](https://github.com/browser-use/browser-use) | [Stagehand](https://github.com/browserbase/stagehand) | [Skyvern](https://github.com/Skyvern-AI/skyvern)
- [LaVague](https://github.com/lavague-ai/LaVague) | [Agent-E](https://github.com/EmergenceAI/Agent-E) | [AgentQ](https://github.com/sentient-engineering/agent-q)
- [Playwright MCP](https://github.com/microsoft/playwright-mcp) | [SeeAct](https://github.com/OSU-NLP-Group/SeeAct) | [OpenCUA](https://github.com/xlang-ai/OpenCUA)
- [OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt) | [Crawl4AI](https://github.com/unclecode/crawl4ai) | [Convergence Proxy Lite](https://github.com/convergence-ai/proxy-lite)

### Research Papers
- [SeeAct (arxiv)](https://arxiv.org/abs/2401.01614) | [WebVoyager (arxiv)](https://arxiv.org/html/2401.13919v3)
- [Agent-E (arxiv)](https://arxiv.org/html/2407.13032v1) | [OmniParser (arxiv)](https://arxiv.org/html/2408.00203v1)
- [Prune4Web (arxiv)](https://arxiv.org/abs/2511.21398) | [Building Browser Agents (arxiv)](https://arxiv.org/html/2511.19477v1)
- [Plan-and-Act (arxiv)](https://arxiv.org/html/2503.09572v3)

### Technical Blogs
- [browser-use SOTA Technical Report](https://browser-use.com/posts/sota-technical-report)
- [rtrvr.ai DOM Intelligence](https://www.rtrvr.ai/blog/dom-intelligence-architecture)
- [CAMEL Hybrid Browser Toolkit](https://www.camel-ai.org/blogs/camel-browser-toolkit-blog)
- [Stagehand Taming Iframes](https://www.browserbase.com/blog/taming-iframes-a-stagehand-update)
- [browser-use Docs](https://docs.browser-use.com) | [browser-use Changelog](https://browser-use.com/changelog)

---

*Research compiled March 26, 2026 from 3 parallel research agents covering 40+ sources, 14 open-source agents, 5 commercial agents, and 11 research papers.*
