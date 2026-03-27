# Open-Source Browser Use Agents: Comprehensive Research Report (2024-2026)

*Research date: March 26, 2026*

---

## Table of Contents
1. [Major Open-Source Browser Agents](#1-major-open-source-browser-agents)
2. [Emerging Trends in Browser Agent Architecture](#2-emerging-trends-in-browser-agent-architecture)
3. [Production/Commercial Browser Agents](#3-productioncommercial-browser-agents)
4. [Benchmarks & Leaderboards](#4-benchmarks--leaderboards)
5. [Security Considerations](#5-security-considerations)
6. [Strategic Takeaways for Building a Competitive Agent](#6-strategic-takeaways)

---

## 1. Major Open-Source Browser Agents

### 1.1 Browser-Use
- **GitHub:** https://github.com/browser-use/browser-use
- **Stars:** ~84.7k (largest community in the space)
- **Language:** Python | **License:** MIT
- **Last Updated:** March 2026 (very active)
- **Architecture:** **Hybrid (DOM-primary + optional Vision)**
  - Reads browser DOM tree via Chrome DevTools Protocol (CDP), returns structured, token-efficient element data
  - Optional screenshot/vision support for complex visual scenarios
  - Event-driven middleware with EventBus and watchdog pattern for loose coupling
  - Background daemon keeps browser alive between commands (~50ms latency)
  - `MessageManager` builds LLM prompts from browser state, history, and task description
  - `ActionLoopDetector` identifies repetitive behavior and injects nudge messages
- **LLM Backbone:** Custom "ChatBrowserUse" model optimized for browsing; also supports Claude, GPT-4o, Gemini, and any LangChain-compatible model. Ollama for local models.
- **Key Capabilities:**
  - Autonomous multi-step task execution
  - CLI interface with persistent browser sessions
  - Cloud deployment with stealth browsing, proxy rotation, CAPTCHA solving
  - Custom tools framework for domain-specific extensions
  - Benchmark suite for evaluating 100+ real-world browser tasks
  - MCP Server integration
- **What Makes It Unique:** Largest community by far. Pragmatic hybrid approach prioritizes DOM for efficiency while falling back to vision when needed. Event-driven architecture with watchdogs is a key architectural innovation. Extensive ecosystem (web-ui, awesome-projects, cloud SDK).
- **Benchmark:** 89.1% on WebVoyager (after removing 55 outdated tasks)
- **Limitations:**
  - Requires LLM inference at every step (expensive for frequent workflows)
  - CVE-2025-47241 identified -- the open-source version is not intended for production "as-is" and lacks critical defensive mechanisms against prompt injection
  - Performance drops on canvas-heavy sites (Google Sheets, Figma) where DOM is not informative

---

### 1.2 Stagehand (by Browserbase)
- **GitHub:** https://github.com/browserbase/stagehand
- **Stars:** ~21k-50k (fast-growing; reports vary by date)
- **Language:** TypeScript | **License:** MIT
- **Last Updated:** March 2026 (very active)
- **Architecture:** **Hybrid (DOM/Accessibility Tree + Vision)**
  - Three atomic AI primitives: `act()`, `extract()`, `observe()`
  - V3 moved to CDP-native architecture (removed Playwright dependency), cutting round-trip time by 44%
  - Dynamic Stagehand Agent for high-level decision making
  - Action caching: records successful actions and replays without LLM calls on repeat visits
  - Server-side caching for act/extract/observe results
- **LLM Backbone:** Supports computer use models from Google, OpenAI, Anthropic, Microsoft
- **Key Capabilities:**
  - Code + natural language hybrid: use code when you know what to do, AI for unfamiliar pages
  - Preview AI actions before running them
  - Multi-language SDKs (TypeScript, Python, Java, C#, Ruby, Kotlin)
  - Deep iframe support (XPath chunking with Playwright frameLocator)
  - Self-healing actions
- **What Makes It Unique:** Bridges brittle traditional automation and unpredictable full-agent solutions. Action caching dramatically reduces cost for repeated workflows. CDP-native V3 architecture is fastest in class.
- **Benchmark:** ~75% on WebVoyager (with Claude Sonnet 4.6)
- **Limitations:**
  - CAPTCHA handling, proxy routing, structured data pipelines sit outside core framework
  - Requires Browserbase infrastructure for full cloud capabilities
  - TypeScript-first (Python SDK is newer/less mature)

---

### 1.3 Skyvern
- **GitHub:** https://github.com/Skyvern-AI/skyvern
- **Stars:** ~20.3k
- **Language:** Python | **License:** AGPL-3.0
- **Last Updated:** March 2026 (very active)
- **Architecture:** **Vision-primary + DOM hybrid**
  - Uses Vision LLMs to understand pages via screenshots (resistant to layout changes)
  - Blends computer vision with DOM parsing for action identification
  - Multi-modal LLMs map natural language instructions to actions
- **LLM Backbone:** Gemini 3.0 Flash, Gemini 2.5 Pro/Flash, supports Ollama for local vision models (qwen3-vl, llava)
- **Key Capabilities:**
  - No-code workflow builder for non-technical users
  - Native 2FA/TOTP authentication handling
  - CAPTCHA support built-in
  - Proxy networks with geographic targeting
  - Structured schema-based data extraction
  - Automated file downloads to cloud storage
  - Live viewport streaming
  - Parallel execution
  - Works on never-before-seen websites without per-site customization
- **What Makes It Unique:** Production-grade out of the box. Vision-first approach handles sites with broken accessibility/DOM. Navigates government portals and insurance sites it has never seen before. No-code builder for non-developers.
- **Benchmark:** 85.8% on WebVoyager
- **Limitations:**
  - AGPL-3.0 license (copyleft -- restrictive for commercial embedding)
  - Performance drops on sites with dynamic forms where DOM changes after input
  - Real-world success rates lower when facing Cloudflare, DataDome bot protection
  - Cost optimization roadmap still in progress (context tree and prompt caching planned)
- **Funding:** $2.7M seed round (December 2025), YC-backed

---

### 1.4 LaVague
- **GitHub:** https://github.com/lavague-ai/LaVague
- **Stars:** ~6.3k
- **Language:** Python (70%), TypeScript (15%) | **License:** Apache-2.0
- **Last Updated:** Active (93 open issues, 9 PRs)
- **Architecture:** **DOM-based with RAG**
  - **World Model:** Takes objective + current page state, outputs instructions
  - **Action Engine:** "Compiles" instructions into Selenium/Playwright code and executes
  - RAG retrieves relevant HTML chunks for generating interaction code
- **LLM Backbone:** GPT-4o by default, fully customizable
- **Key Capabilities:**
  - LaVague QA: turns Gherkin specs into automated tests
  - Modular architecture (lavague-core, lavague-integrations, lavague-server, lavague-gradio)
  - Gradio-based web UI
- **What Makes It Unique:** "Large Action Model" framing -- compiles natural language into executable automation code. QA testing integration is unique in the space.
- **Limitations:**
  - Smaller community compared to leaders
  - DOM/RAG approach struggles with highly dynamic or canvas-based pages
  - Dependent on HTML structure quality

---

### 1.5 Agent-E (by Emergence AI)
- **GitHub:** https://github.com/EmergenceAI/Agent-E
- **Stars:** ~1.2k
- **Language:** Python | **License:** Available (LICENSE.txt)
- **Last Updated:** February 2025
- **Architecture:** **DOM-based with constrained action space**
  - Built on AG2 framework (formerly AutoGen)
  - Dual-agent model: User Proxy Agent (executes skills) + Browser Navigation Agent
  - **Sensing Skills:** get_dom_with_content_type, geturl (state perception)
  - **Action Skills:** click, text_entry, url_navigation (environment manipulation)
  - Deliberately constrains LLM to well-defined actions (no unrestricted code execution)
- **LLM Backbone:** OpenAI, Azure, open-source via LiteLLM/Ollama
- **Key Capabilities:**
  - FastAPI wrapper for HTTP-based task automation
  - Conversational interaction with LLMs
  - Form filling, e-commerce search, content discovery, media interaction
- **What Makes It Unique:** Safety-first design -- constrains actions to predefined skill set rather than allowing arbitrary code execution. Multi-agent architecture with clear separation of concerns.
- **Benchmark:** 73.1% on WebVoyager (full dataset)
- **Limitations:**
  - Lower benchmark performance than leaders
  - Less active development (last update Feb 2025)
  - Smaller community

---

### 1.6 AgentQ (by Sentient Engineering / MultiOn research)
- **GitHub:** https://github.com/sentient-engineering/agent-q
- **Stars:** ~1k+
- **Language:** Python
- **Architecture:** **DOM-based with MCTS + Reinforcement Learning**
  - States = DOM + URL + objective
  - Actions = clicking, typing, navigation
  - Multiple architectures: planner-navigator, solo planner-actor, actor-critic, actor-critic + MCTS + DPO
  - Monte Carlo Tree Search explores possible rationales and web actions
  - UCB1 heuristic balances exploration vs exploitation
  - Self-critique mechanism for action quality assessment
  - DPO (Direct Preference Optimization) for iterative fine-tuning
- **LLM Backbone:** Llama-3 70B (fine-tuned), supports various base models
- **Key Capabilities:**
  - Self-improving: learns from its own interactions
  - MCTS-guided search for optimal action sequences
  - Online reinforcement learning from real web interactions
- **What Makes It Unique:** Only open-source browser agent with MCTS-based reinforcement learning and DPO fine-tuning. Achieves massive performance gains (18.6% to 95.4% with Llama-3 70B on booking tasks). Academic/research-grade innovation.
- **Benchmark:** 95.4% on real-world booking scenarios (with online search), up from 18.6% zero-shot
- **Limitations:**
  - Research-focused, not production-ready
  - Requires significant compute for MCTS exploration + DPO training
  - Narrow benchmark coverage (primarily booking/e-commerce)

---

### 1.7 Playwright MCP (by Microsoft)
- **GitHub:** https://github.com/microsoft/playwright-mcp
- **Stars:** Growing rapidly (one of most adopted MCP servers)
- **Language:** TypeScript
- **Architecture:** **Accessibility Tree-based (no vision)**
  - Uses browser's accessibility tree -- structured, text-based page representation
  - Elements identified by roles, labels, attributes, states (not CSS selectors or pixel coordinates)
  - Accessibility snapshots average 2-5KB vs 100KB+ for screenshots
  - MCP (Model Context Protocol) standard for LLM-tool communication
- **LLM Backbone:** Any MCP-compatible LLM (Claude, GPT, Gemini, etc.)
- **Key Capabilities:**
  - Deterministic, fast browser control
  - Cross-browser support (Chromium, Firefox, WebKit)
  - Built into GitHub Copilot Coding Agent
  - E2E test execution, test generation, form automation
  - Cloudflare Browser Rendering integration
- **What Makes It Unique:** Speed -- "Snapshot Mode" is faster than screenshot-based tools. Leverages the accessibility tree rather than raw DOM or screenshots. Backed by Microsoft with deep Playwright ecosystem integration.
- **Limitations:**
  - Cannot handle canvas-based applications (Figma, Google Sheets) where accessibility tree is empty
  - No visual understanding -- purely structural
  - Requires pages to have good accessibility markup

---

### 1.8 SeeAct (OSU NLP Group)
- **GitHub:** https://github.com/OSU-NLP-Group/SeeAct
- **Stars:** ~1k+
- **Language:** Python
- **Last Updated:** 2024 (research project)
- **Architecture:** **Vision-based (screenshot-primary)**
  - Uses large multimodal models (GPT-4V) to "see" and act on web pages
  - Grounding via set-of-marks on screenshots
  - Runs through Playwright as browser interface
- **LLM Backbone:** GPT-4V(ision) primarily
- **Key Capabilities:**
  - Generalist web agent -- works on any website
  - Published as Python package (pip install seeact)
  - ICML 2024 paper
- **What Makes It Unique:** Simple but surprisingly effective. Many recent agents (except Claude CU 3.7 and Operator) underperform SeeAct despite being more complex. Pioneered the vision-grounding approach.
- **Limitations:**
  - Research project, not production-grade
  - Dependent on GPT-4V API
  - Limited error recovery mechanisms

---

### 1.9 WebVoyager
- **GitHub:** https://github.com/MinorJerry/WebVoyager
- **Language:** Python
- **Architecture:** **Vision-based (multimodal)**
  - Annotates screenshots with bounding boxes for element identification
  - Combines page text with visual context
  - End-to-end interaction with real-world websites
- **LLM Backbone:** Large multimodal models (GPT-4V, etc.)
- **What Makes It Unique:** Demonstrated that visual context (annotated screenshots) significantly improves element targeting. Established the WebVoyager benchmark that became an industry standard.
- **Limitations:** Research prototype, not actively maintained as a product

---

### 1.10 OpenCUA (XLANG Lab)
- **GitHub:** https://github.com/xlang-ai/OpenCUA
- **Stars:** ~723
- **Language:** Python | **License:** MIT
- **Last Updated:** January 2026
- **Architecture:** **Vision-based with reflective CoT**
  - Modified Qwen-based models with 1D RoPE
  - Reflective Chain-of-Thought reasoning
  - Multi-image history for temporal context
  - Cross-OS support (Windows, macOS, Ubuntu)
- **Models:** OpenCUA-7B, 32B, 72B (open-weights)
- **Key Capabilities:**
  - AgentNetTool: cross-platform annotation application
  - AgentNet Dataset: first large-scale CUA dataset (3 OS, 200+ apps)
  - AgentNetBench: offline evaluation tool
  - vLLM integration for production deployment with tensor parallelism
- **What Makes It Unique:** NeurIPS 2025 Spotlight paper. Fully open-weights models. First large-scale cross-OS computer use dataset. 45.0% on OSWorld-Verified (SOTA for open-source models).
- **Limitations:**
  - Computer use (full desktop), not browser-specific
  - Requires significant GPU resources for 72B model
  - Academic/research focus

---

### 1.11 OpenAdapt
- **GitHub:** https://github.com/OpenAdaptAI/OpenAdapt
- **Stars:** ~2k+
- **Language:** Python | **License:** MIT
- **Architecture:** **Learn-by-demonstration + multimodal**
  - Records human GUI interactions to generate automation scripts
  - Trains vision-language models on recorded demonstrations
  - Adapts to software changes using multimodal models
  - Built-in PII/PHI detection and redaction
- **LLM Backbone:** OpenAI, Anthropic, Google, Ollama, vLLM
- **Key Capabilities:**
  - Cross-platform (macOS, Windows)
  - Desktop + browser automation
  - Privacy-first with automatic data redaction
  - Adaptive intelligence that handles UI changes
- **What Makes It Unique:** RPA-style "record and replay" enhanced with LLMs. Privacy-first design. Works across browsers AND desktop apps.

---

### 1.12 Crawl4AI
- **GitHub:** https://github.com/unclecode/crawl4ai
- **Stars:** ~51k
- **Language:** Python
- **Architecture:** **Async web crawler with LLM extraction**
  - Parallel crawling with chunk-based extraction
  - Agentic crawler for autonomous multi-step operations
  - Automated schema generator (natural language to extraction schemas)
  - MCP server integration
- **Key Capabilities:**
  - LLM-friendly output (Markdown, structured data)
  - Domain-specific scrapers (academic, e-commerce)
  - Web embedding index for semantic search
  - Docker deployment
- **What Makes It Unique:** Not an "agent" per se, but the default tool for feeding web content into LLMs. Massive adoption (51k stars). Pairs well with browser agents for data extraction pipelines.

---

### 1.13 Index (by Laminar)
- **GitHub:** https://github.com/lmnr-ai/index
- **Stars:** ~2.3k
- **Language:** Python | **License:** Apache-2.0
- **Last Updated:** July 2025 (repository archived)
- **Architecture:** **Vision-based with reasoning LLMs**
  - Powered by reasoning LLMs with vision capabilities
  - Structured data extraction via Pydantic schemas
  - Observability integration with Laminar platform
- **LLM Backbone:** Claude 3.7 Sonnet, Gemini 2.5 Pro, OpenAI o4-mini, Gemini 2.5 Flash
- **Benchmark:** 92% on WebVoyager (with Claude 3.7 extended thinking)
- **Status:** Archived -- team appears to have pivoted to serverless API

---

### 1.14 Other Notable Projects

| Project | GitHub | Stars | Key Feature |
|---------|--------|-------|-------------|
| **Steel Browser** | steel-dev/steel-browser | ~5k | Browser sandbox API for AI agents |
| **BrowserOS** | browseros-ai/BrowserOS | ~1k | Open-source agentic browser (alt to ChatGPT Atlas) |
| **Open Operator (Browserbase)** | browserbase/open-operator | ~2k | Template for building web agents with Stagehand |
| **Open Operator (OpenHands)** | OpenHands/open-operator | ~3k | Resources for computer-use agents |
| **Eko 2.0 (Fellou)** | FellouAI/eko | ~1k | Framework for building custom AI agents in agentic browser |
| **Mind2Web** | OSU-NLP-Group/Mind2Web | ~1k | Benchmark dataset: 2,000+ tasks across 137 websites |
| **Proxy Lite (Convergence)** | convergence-ai/proxy-lite | ~1k | Open-weights mini version of Proxy agent, 3B model |

---

## 2. Emerging Trends in Browser Agent Architecture

### 2.1 Screenshot vs DOM vs Hybrid Approaches

The industry is converging on **hybrid approaches** as the dominant paradigm:

| Approach | Strengths | Weaknesses | Best For |
|----------|-----------|------------|----------|
| **Screenshot/Vision** | Works on canvas apps (Figma, Sheets), no DOM dependency, handles visual layouts | Slow (100KB+ per frame), imprecise on dense grids (24px cells), expensive API calls | Canvas-heavy apps, visual verification |
| **DOM/Accessibility Tree** | Fast (2-5KB snapshots), deterministic, precise element targeting | Fails on canvas apps, requires good markup, breaks on dynamic DOM changes | Structured forms, standard web apps |
| **Hybrid** | Adapts to page type, ~23x fewer errors than pure AI browsing | More complex implementation, higher engineering cost | Production deployments |

**Key finding:** A hybrid approach combining AI planning with deterministic execution yields about 23x fewer errors than purely AI-driven browsing (CAMEL AI research). The "Hybrid Browser Toolkit" from CAMEL-AI intelligently switches between text and visual approaches based on the task.

### 2.2 How Agents Handle Complex Pages

- **Dynamic content:** Agents use `waitUntil: "networkidle"` to ensure all scripts and iframes load before acting. DOM observation via MutationObserver for detecting changes after user input.
- **Canvas-based apps:** Vision models are the only option (DOM/accessibility tree is empty). This remains a fundamental challenge.
- **Iframes:** Stagehand's "deep" XPath locator breaks paths into chunks, using `frameLocator()` to descend into each iframe context. This was a major 2025 update.
- **Single-page apps (SPAs):** Agents monitor URL hash changes and DOM mutations rather than page load events.

### 2.3 Multi-Step Task Planning

Three planning paradigms have emerged (from research taxonomy):

1. **Step-by-Step (BFS):** Most common. Agent observes state, decides next action, executes, repeats. Used by Browser-Use, Stagehand, Skyvern.
2. **Tree Search (Best-First):** AgentQ uses MCTS to explore multiple action branches simultaneously, selecting the most promising path. More compute-intensive but dramatically better for complex tasks.
3. **Full-Plan-in-Advance (DFS):** Agent creates complete action plan upfront, then executes. Used by some LaVague workflows. Fragile if intermediate states differ from expectations.

**OpAgent framework** (2026 research) uses modular Planner-Grounder-Reflector-Summarizer architecture achieving 71.6% on WebArena.

### 2.4 Error Recovery Strategies

- **Self-correction loops:** Operator (OpenAI) plans multi-step strategies and self-corrects on the fly. When encountering errors or unexpected pages, it re-plans.
- **Self-reflection:** Agent analyzes its own action trace after failure, criticizes its approach, and tries improved strategy.
- **World model simulation:** Recent research augments agents with learned models of web dynamics. The agent simulates likely outcomes of risky actions as natural language descriptions before executing, avoiding irreversible errors.
- **Compressed memory:** Rolling buffer of recent actions in full detail + summarized older steps. Provides immediate context for error recovery while maintaining global task progress view.
- **Action loop detection:** Browser-Use's `ActionLoopDetector` identifies repetitive behavior and injects "nudge" messages to break out of loops.
- **Self-healing selectors:** Stagehand and Skyvern use adaptive DOM selectors that adjust when page structure changes.

### 2.5 CAPTCHA, Dynamic Content, and Iframes

**CAPTCHAs:**
- Modern CAPTCHAs (2025) use behavioral analysis and advanced detection -- stealth plugins are largely ineffective
- **Browserbase:** Built-in CAPTCHA solving (up to 30 seconds per solve), runs in background
- **Skyvern:** Native CAPTCHA support integrated into workflow
- **Browser-Use Cloud:** CAPTCHA solving as cloud service feature
- Best practice: Hybrid approach combining AI speed with human CAPTCHA-solving services for reliability

**Dynamic Content:**
- Vision agents struggle with date pickers and tightly packed grids
- DOM agents break when structure changes after user input (dropdowns revealing new fields)
- Hybrid approaches monitor DOM mutations and re-evaluate state after each interaction

**Iframes:**
- Historically a major pain point for all automation frameworks
- Stagehand's 2025 "Taming Iframes" update introduced deep XPath locator solution
- CAPTCHA iframes particularly challenging as they load dynamically with runtime site keys

---

## 3. Production/Commercial Browser Agents

### 3.1 OpenAI Operator / CUA / ChatGPT Agent / Atlas

- **Model:** Computer-Using Agent (CUA) = GPT-4o vision + reinforcement learning
- **Architecture:** Screenshot-based iterative loop (perceive -> reason -> act)
- **Performance:** 58.1% WebArena, 87% WebVoyager, 38.1% OSWorld
- **Evolution:**
  - January 2025: Operator launched (standalone product)
  - July 2025: Integrated into ChatGPT as "ChatGPT Agent"
  - October 2025: Atlas browser launched with Agent Mode
- **Key capabilities:** Self-correction, hands control back to user when stuck
- **Security:** OpenAI acknowledges prompt injection remains "unsolved." Continuous hardening with layered defenses.

### 3.2 Claude Computer Use (Anthropic)

- **Models:** Claude Opus 4.6, Sonnet 4.6, Opus 4.5 (computer_20251124 tool version)
- **Architecture:** Screenshot-based with fallback hierarchy
  - First attempts purpose-built connectors (Slack, Calendar, etc.)
  - Falls back to direct screen interaction when no connector exists
  - `zoom` action for detailed screen region inspection (new in 2026)
- **March 2026 updates:**
  - Computer Use in Claude Cowork (direct macOS desktop control)
  - Auto Mode for Claude Code with AI safety classifier
  - Claude Code Channels (Discord/Telegram control)
- **Limitations:** Currently macOS-only. Requires explicit per-app permission. Prone to errors (early stage). Scans for prompt injection attacks.

### 3.3 Google Project Mariner

- **Model:** Gemini 2.5 Pro
- **Architecture:** Chrome extension-based agent
- **Key features:**
  - "Teach & Repeat": demonstrate task once, Mariner replicates
  - Up to 10 simultaneous tasks (cloud VM-based since 2025)
  - Identifies text, code, images, forms to build page understanding
- **Availability:** U.S. only, $249.99/month (AI Ultra plan)
- **Status:** Research prototype expanding to more users (Google I/O 2025)

### 3.4 Convergence Proxy

- **Architecture:** Proprietary Large Meta Learning Models (LMLMs)
- **Open-source:** Proxy Lite (3B parameter open-weights model) at convergence-ai/proxy-lite
- **Key Features:**
  - No API dependencies -- interacts with websites like a human
  - Dynamic web interaction: form submissions, error recovery, multi-step workflows
  - 25,000+ users, 150,000+ automated actions in first two weeks
- **Performance:** Claims to outperform OpenAI Operator and Anthropic solutions on top benchmarks
- **Pricing:** Free tier (5 sessions/day), Pro ($20/month, unlimited)

### 3.5 Adept AI (ACT-1 / ACT-2)

- **Status:** Effectively dormant. Co-founders hired by Amazon in 2024 deal.
- **Architecture:** ACT-1 = large Transformer trained on digital tool use; ACT-2 = Fuyu vision model for GUI
- **Current state:** Plans for advanced autonomous agents put on hold due to training costs. Enterprise-focused, never released publicly.

---

## 4. Benchmarks & Leaderboards

### WebArena (812 tasks, self-hosted web environments)
| Agent | Score | Date |
|-------|-------|------|
| IBM CUGA | 61.7% | Feb 2025 |
| OpenAI CUA | 58.1% | Jan 2025 |
| Gemini 2.5 Pro | 54.8% | 2025 |
| OpAgent | 71.6% | 2026 (research) |
| Best humans | ~78% | baseline |

Progress: 14% (2024) -> ~60% (2026) in two years.

### WebVoyager (real website tasks)
| Agent | Score | Notes |
|-------|-------|-------|
| Index (Claude 3.7 ext. thinking) | 92% | Archived project |
| Browser-Use | 89.1% | 55 outdated tasks removed |
| OpenAI CUA | 87% | |
| Skyvern | 85.8% | On unseen websites |
| Stagehand (Claude Sonnet 4.6) | ~75% | |
| Agent-E | 73.1% | Full dataset |

### OSWorld (full desktop computer use)
| Agent | Score | Notes |
|-------|-------|-------|
| OpenCUA-72B | 45.0% | SOTA open-source |
| OpenAI CUA | 38.1% | |

### WebChoreArena (tedious/labor-intensive tasks, 532 tasks)
- Top LLMs: ~37.8% (significantly harder than WebArena)

---

## 5. Security Considerations

**Critical finding:** Prompt injection is the #1 vulnerability (OWASP 2025 Top 10 for LLMs), affecting 73% of production AI deployments.

**Key risks for browser agents:**
- Every webpage, ad, embedded doc, and dynamically loaded script is an attack vector
- Browser agents can navigate URLs, fill forms, click buttons, download files -- all exploitable
- CVE-2025-47241 affects Browser-Use specifically
- February 2025: Zero-interaction exfiltration demonstrated (hidden instructions on GitHub page leaked private data)
- August-October 2025: Prompt injection vulnerabilities in Perplexity Comet, Fellou, Opera Neon

**OpenAI's position:** Prompt injection may never be fully "solved" for browser agents.

**Defensive layers:**
1. Dedicated, isolated browser profiles
2. Human-in-the-loop confirmations (never disable)
3. Restrict to low-stakes tasks
4. Input/output sanitization
5. Behavioral anomaly detection
6. Sandboxed execution environments

---

## 6. Strategic Takeaways for Building a Competitive Agent

### Architecture Recommendations

1. **Go hybrid:** DOM-primary for speed and efficiency, vision fallback for canvas/visual-heavy pages. The hybrid approach yields 23x fewer errors.

2. **Accessibility tree is underrated:** Playwright MCP's approach (2-5KB snapshots vs 100KB+ screenshots) is dramatically faster and cheaper. Use it as the primary perception layer.

3. **Action caching is a competitive moat:** Stagehand's approach of recording and replaying successful actions without LLM calls cuts cost dramatically for repeated workflows.

4. **MCTS/RL for complex tasks:** AgentQ's approach (18.6% -> 95.4% with MCTS+DPO) shows that search-based planning massively outperforms greedy step-by-step execution. Consider for high-value workflows.

5. **Error recovery is table stakes:** Self-correction loops, action loop detection, compressed memory, and self-healing selectors are now expected.

6. **MCP is the standard:** Model Context Protocol (now Linux Foundation) is how LLMs connect to tools. Build MCP-compatible from day one.

### Market Gaps to Exploit

- **Cross-browser + cross-OS:** Most agents are Chrome-only. Multi-browser support is rare.
- **Local/privacy-first:** Most agents require cloud LLMs. Ollama/local model support is growing but immature.
- **Canvas/visual app support:** No agent handles Google Sheets, Figma, Canva well.
- **Enterprise security:** Production-grade prompt injection defense is unsolved and critical.
- **Cost efficiency:** LLM inference per step is expensive. Action caching, smaller specialized models, and hybrid deterministic/AI execution are the frontier.

### Key Technologies to Integrate

- **Browser:** Playwright (via CDP) or direct CDP
- **LLM:** Claude 4.x, GPT-4.1, Gemini 2.5 Pro (cloud); Qwen, Llama (local)
- **Protocol:** MCP for tool communication
- **Observability:** Laminar, LangSmith, or custom tracing
- **Infrastructure:** Browserbase, Steel Browser, or self-hosted Docker

---

## Sources

- [Browser-Use GitHub](https://github.com/browser-use/browser-use)
- [Stagehand GitHub](https://github.com/browserbase/stagehand)
- [Skyvern GitHub](https://github.com/Skyvern-AI/skyvern)
- [LaVague GitHub](https://github.com/lavague-ai/LaVague)
- [Agent-E GitHub](https://github.com/EmergenceAI/Agent-E)
- [AgentQ GitHub](https://github.com/sentient-engineering/agent-q)
- [Playwright MCP GitHub](https://github.com/microsoft/playwright-mcp)
- [SeeAct GitHub](https://github.com/OSU-NLP-Group/SeeAct)
- [WebVoyager GitHub](https://github.com/MinorJerry/WebVoyager)
- [OpenCUA GitHub](https://github.com/xlang-ai/OpenCUA)
- [OpenAdapt GitHub](https://github.com/OpenAdaptAI/OpenAdapt)
- [Crawl4AI GitHub](https://github.com/unclecode/crawl4ai)
- [Index GitHub](https://github.com/lmnr-ai/index)
- [Convergence Proxy Lite GitHub](https://github.com/convergence-ai/proxy-lite)
- [Fellou Eko GitHub](https://github.com/FellouAI/eko)
- [WebArena Benchmark](https://webarena.dev/)
- [OpenAI Operator / CUA](https://openai.com/index/introducing-operator/)
- [Claude Computer Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
- [Google Project Mariner](https://deepmind.google/models/project-mariner/)
- [Convergence Proxy](https://convergence.ai/)
- [Browser Agent Security Survey (arxiv)](https://arxiv.org/html/2511.19477v1)
- [CAMEL Hybrid Browser Toolkit](https://www.camel-ai.org/blogs/camel-browser-toolkit-blog)
- [Agentic Browser Security 2025 (Wiz)](https://www.wiz.io/blog/agentic-browser-security-2025-year-end-review)
- [Anthropic Prompt Injection Defenses](https://www.anthropic.com/research/prompt-injection-defenses)
- [OpenAI Atlas Prompt Injection Hardening](https://openai.com/index/hardening-atlas-against-prompt-injection/)
- [Best 30+ Open Source Web Agents (AIMultiple)](https://aimultiple.com/open-source-web-agents)
- [Top 10 Browser AI Agents 2026 (O-mega)](https://o-mega.ai/articles/top-10-browser-use-agents-full-review-2026)
- [State of AI Browser Agents 2025 (FillApp)](https://fillapp.ai/blog/the-state-of-ai-browser-agents-2025)
- [Browser-Use SOTA Technical Report](https://browser-use.com/posts/sota-technical-report)
- [Stagehand Taming Iframes](https://www.browserbase.com/blog/taming-iframes-a-stagehand-update)
- [Skyvern CAPTCHA Bypass Guide](https://www.skyvern.com/blog/best-way-to-bypass-captcha-for-ai-browser-automation-september-2025/)
- [rtrvr.ai DOM Intelligence Architecture](https://www.rtrvr.ai/blog/dom-intelligence-architecture)
- [Browser-Use vs Stagehand Comparison](https://www.skyvern.com/blog/browser-use-vs-stagehand-which-is-better/)
- [NeurIPS 2025 CUA Papers](https://cua.ai/blog/neurips-2025-cua-papers)
- [Adept AI / Amazon Analysis](https://www.sramanamitra.com/2025/06/10/analysis-of-amazons-adept-ai-deal/)
