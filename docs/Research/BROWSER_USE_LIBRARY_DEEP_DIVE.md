# Browser-Use Python Library: Deep Research Report

**Date**: March 26, 2026
**Library**: browser-use (github.com/browser-use/browser-use)
**Latest Version**: 0.12.5 (March 25, 2026)
**Stars**: 70k+ | **Forks**: ~8k | **License**: MIT
**Language**: Python | **WebVoyager Benchmark**: 89.1%

---

## Table of Contents

1. [Internal Architecture](#1-internal-architecture)
2. [Key Features](#2-key-features)
3. [Configuration and Extensibility](#3-configuration-and-extensibility)
4. [Recent Updates (2025-2026)](#4-recent-updates-2025-2026)
5. [Comparison with Competitors](#5-comparison-with-competitors)
6. [Best Practices](#6-best-practices)
7. [Code Examples](#7-code-examples)

---

## 1. Internal Architecture

### Core Agent Loop: Sense-Think-Act Cycle

Browser-use operates through a continuous feedback loop that mimics human browsing behavior. The `Agent.run()` method executes with configurable `max_steps` (default: 100), iterating until task completion or failure threshold.

**Each step follows this pattern:**

```
1. SENSE   -> Gather current browser state (DOM + screenshot) via BrowserSession.get_browser_state_summary()
2. THINK   -> MessageManager constructs LLM prompts combining task context, browser state, and execution history
3. ACT     -> LLM returns AgentOutput with structured actions; Tools executes them through event dispatch
4. RECORD  -> Results stored in AgentHistoryList for subsequent iterations
```

### DOM Processing Pipeline (4 Stages)

This is the critical differentiator -- raw browser data transforms into LLM-consumable format through a multi-stage pipeline:

**Stage 1 - Data Collection**: Five parallel CDP (Chrome DevTools Protocol) requests gather:
- DOM tree
- Accessibility tree
- Page snapshot
- Viewport metrics
- JavaScript event listeners

**Stage 2 - Data Fusion**: `DomService` merges structural (DOM), semantic (accessibility), and visual (snapshot) data into unified `EnhancedDOMTreeNode` structures.

**Stage 3 - Serialization Pipeline**: `DOMTreeSerializer` applies:
- **Simplification**: Removes non-interactive content/clutter
- **Paint-order filtering**: Hides occluded (visually hidden) elements
- **Bounding-box filtering**: Collapses nested clickable elements
- **Index assignment**: Each interactive element receives a numeric index

**Stage 4 - Output**: Produces two artifacts:
- `SerializedDOMState` for LLM prompts
- `DOMSelectorMap` for index-to-element reverse lookup during action execution

### Element Mapping and Interaction

When the LLM specifies "click element 5", the `DOMSelectorMap` performs reverse lookup to identify the exact DOM node. Then `ClickElementEvent` sends CDP commands with precise coordinates calculated from bounding boxes and viewport data. XPath tracking ensures consistency and reproducibility.

### Message Manager

`MessageManager.create_state_messages()` constructs multi-part prompts containing:
- System prompt with action schema and capabilities
- Current DOM tree with element indices
- Screenshot as base64 (optional, in vision mode)
- Execution history (previous goals, results, errors)
- Loop detection nudges when repetitive behavior is detected

### Browser Context Management

`BrowserSession` maintains context through:
- **SessionManager**: Pool of CDP WebSocket connections to Chrome targets
- **EventBus**: Decoupled event routing to specialized Watchdog handlers
- **DOMWatchdog**: Captures and monitors page state
- **DownloadsWatchdog**: Monitors file downloads
- **CrashWatchdog**: Detects browser failures
- **PopupsWatchdog**: Handles dialog boxes and popups

### Error Handling and Resilience

- **Retry logic**: LLM providers implement exponential backoff (5 retries)
- **Loop detection**: `ActionLoopDetector` identifies repetitive action sequences and injects behavioral nudge messages
- **Watchdog recovery**: Specialized watchdogs handle CDP disconnections, navigation timeouts, and crashes
- **Structured validation**: All LLM responses validated against `AgentOutput` Pydantic model

### Key Architectural Insight

The event-driven design decouples Agent reasoning (high-level planning) from Browser control (low-level CDP execution), enabling independent scaling and recovery mechanisms. As of August 2025, browser-use migrated from Playwright to 100% CDP-based interactions for faster execution.

---

## 2. Key Features

### Multi-Tab Support
Browser-use can deal with multiple browser tabs, enabling complex workflows and parallel tasks. Tab information is captured as part of the browser state and fed to the LLM so it can decide when to switch tabs, open new ones, or close them.

### File Upload and Download
- **Upload**: Via `available_file_paths` parameter passed to the agent. Custom actions can also handle file input elements.
- **Download**: `BrowserContextConfig` supports `save_downloads_path` / `downloads_path` for specifying download locations. `accept_downloads` defaults to `True`. `auto_download_pdfs` bypasses the PDF viewer.
- **DownloadsWatchdog** monitors and tracks file downloads.

### Custom Actions/Tools
The `@tools.action()` decorator (formerly `@controller.action()`) registers Python functions as agent-callable tools. Supports:
- Sync and async functions
- Pydantic model parameter validation
- Domain-restricted execution
- Injectable parameters (browser_session, cdp_client, page_extraction_llm, file_system, etc.)

### Vision Capabilities
- Screenshots captured during `BrowserStateRequestEvent` are encoded as base64 and included in LLM prompts
- Highlighting overlays applied to interactive elements for visual grounding
- Configurable via `use_vision` parameter: `"auto"`, `True`, or `False`
- `highlight_elements` config option enables/disables visual emphasis for vision models
- Vision mode allows models to interpret pixel-level cues alongside structural data

### Structured Data Extraction
- Accepts `output_model_schema` (Pydantic model) for validated, typed task results
- Agents output JSON instead of free-form prose when a schema is bound
- Custom actions can also return structured data via `ActionResult`

### Built-in Action Categories
Navigation, page interaction, JavaScript execution, tab management, content extraction, visual analysis, form controls, file operations, Google search.

### Cloud and Production Features
- `@sandbox()` decorator for auto-provisioning cloud browsers
- Cloud profile sync (cookies synced from local to remote)
- Proxy support with country-specific routing
- SOC 2 Type II compliant (as of November 2025)
- Stealth browser infrastructure with persistent profiles

---

## 3. Configuration and Extensibility

### Agent Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `task` | required | User-defined objective (natural language) |
| `llm` | required | Language model instance |
| `browser` | auto | Browser instance with customizable settings |
| `tools` | None | Custom tool registry (Tools instance) |
| `max_steps` | 100 | Maximum execution iterations |
| `use_vision` | "auto" | Screenshot inclusion mode |
| `use_thinking` | True | Enable internal reasoning |
| `flash_mode` | False | Skip evaluation steps for speed |
| `max_actions_per_step` | 3 | Batch action execution limit |
| `override_system_message` | None | Replace default prompt entirely |
| `extend_system_message` | None | Append instructions to default prompt |
| `initial_actions` | None | Pre-execution steps without LLM |
| `output_model_schema` | None | Pydantic model for structured output |
| `max_history_items` | None | Limit LLM context window usage |
| `llm_timeout` | 90 | Seconds for LLM API calls |
| `step_timeout` | 120 | Seconds per iteration |
| `sensitive_data` | None | Dict of placeholder->secret mappings |

### BrowserConfig Parameters (Selected)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `cdp_url` | None | Connect to existing browser via CDP |
| `headless` | None | Run without UI (auto-detects) |
| `window_size` | None | Browser dimensions |
| `viewport` | None | Content area size |
| `keep_alive` | None | Maintain browser after completion |
| `allowed_domains` | None | Restrict navigation (supports wildcards) |
| `prohibited_domains` | None | Block specific domains |
| `user_data_dir` | auto | Profile storage location |
| `storage_state` | None | Cookies/localStorage persistence |
| `proxy` | None | ProxySettings(server, bypass, username, password) |
| `executable_path` | None | Custom browser path |
| `channel` | None | 'chromium', 'chrome', 'msedge', etc. |
| `highlight_elements` | True | Visual emphasis for vision models |
| `paint_order_filtering` | True | Remove obscured DOM elements |
| `accept_downloads` | True | Auto-accept downloads |
| `downloads_path` | None | Download destination |
| `auto_download_pdfs` | True | Bypass PDF viewer |
| `minimum_wait_page_load_time` | 0.25 | Pre-capture delay (seconds) |
| `wait_for_network_idle_page_load_time` | 0.5 | Network inactivity timeout |
| `wait_between_actions` | 0.5 | Inter-action pause |
| `record_video_dir` | None | Video recording output directory |
| `record_har_path` | None | Network trace output (.har) |
| `disable_security` | False | Remove browser security restrictions |

### Custom Action System

**Decorator pattern** with `@tools.action()`:
- `description` (required): Explains tool purpose to the LLM
- `allowed_domains`: Restricts execution to specific domains
- `param_model`: Pydantic model for typed parameters

**Injectable parameters** (matched by name, not type):
- `browser_session: BrowserSession` - Current CDP session
- `cdp_client` - Direct Chrome DevTools Protocol access
- `page_extraction_llm: BaseChatModel` - Agent's LLM for custom calls
- `file_system: FileSystem` - File operations
- `available_file_paths: list[str]` - Files for upload/processing
- `has_sensitive_data: bool` - Sensitive data flag

**Return types**: `ActionResult | str | None`. The stringified result is passed back to the LLM. Use `ActionResult(extracted_content=..., include_in_memory=True)` for long-term memory persistence.

### Authentication Handling

- **sensitive_data**: Dictionary mapping placeholder strings to actual secrets. The agent uses placeholders in its reasoning (avoiding screenshot leaks), and values are substituted at execution time.
- **storage_state**: Load cookies/localStorage from a file or dict for pre-authenticated sessions.
- **Cloud profile sync**: Export local Chrome profiles with cookies to cloud environments.
- Best practice: Disable vision mode (`use_vision=False`) when handling credentials to prevent screenshot leaks.

---

## 4. Recent Updates (2025-2026)

### Major Milestones

| Date | Release | Highlights |
|------|---------|------------|
| **2026-03-24** | Security patch | Removed litellm from core deps due to supply chain attack (versions 1.82.7-1.82.8 backdoored) |
| **2026-03-22** | CLI 2.0 | Terminal-native execution, works with Claude Code/Cursor, Chromium 146, hCaptcha solver |
| **2026-02-25** | Agent API & SDK 3.0 | New experimental agent built from scratch, `client.run()` API, breaking changes |
| **2026-01-27** | BU 2.0 Model | 83.3% accuracy (+12% over 1.0), ~62s avg task duration, matches Claude Opus 4.5 but 40% faster |
| **2025-12-16** | BU-30B-A3B-Preview | Open-source 30B param model (3B active), 200 tasks/$1, on Hugging Face |
| **2025-12-04** | Skills API | Convert plain-text descriptions into production API endpoints |
| **2025-11-21** | MCP Server | Cloud MCP at api.browser-use.com/mcp, Gemini 3 Pro support, Teams features |
| **2025-11-04** | SOC 2 Type II | Enterprise security certification |
| **2025-10-15** | Code Use | CodeAgent generates Python/JS for high-volume data extraction |
| **2025-10-08** | LLM Use | 6x faster agents with custom-trained LLM, 20 steps/minute |
| **2025-09-30** | Stealth Infrastructure | Persistent browser profiles via CDP, proxy support, file handling |
| **2025-09-19** | Actor Use | CDP-based alternative to Playwright/Puppeteer |
| **2025-08-19** | CDP Migration | 100% CDP-based (removed Playwright dependency), faster execution |

### Key Trends
- Transition from Playwright to pure CDP for lower-level browser control
- Development of custom LLMs optimized specifically for browser automation
- Cloud-first infrastructure with enterprise security compliance
- MCP (Model Context Protocol) integration for tool interoperability

---

## 5. Comparison with Competitors

### Feature Matrix

| Feature | Browser-Use | Stagehand | Skyvern | LaVague |
|---------|------------|-----------|---------|---------|
| **Language** | Python | TypeScript | Python | Python |
| **WebVoyager Score** | 89.1% | High (unspecified) | 85.85% | N/A |
| **Architecture** | Autonomous agent loop | Hybrid (Playwright + AI methods) | Vision + LLM | World Model + Action Engine |
| **Approach** | Full autonomy, goal-driven | Deterministic-first, AI-augmented | Vision-based, no selectors needed | Step-by-step guidance |
| **Custom LLM** | Yes (BU 2.0, BU-30B) | No | Yes (Skyvern 2.0) | No |
| **CAPTCHA Solving** | Yes (hCaptcha, reCAPTCHA) | External only | Built-in | No |
| **2FA/TOTP** | Manual/external | External only | Built-in | No |
| **Proxy Support** | Yes (cloud) | Via Browserbase | Built-in | No |
| **Caching** | No | Auto-caching of actions | No | No |
| **No-Code Option** | No (Python required) | No (TypeScript required) | Yes (YAML workflows) | No |
| **Open Source** | Yes (MIT) | Yes | Yes | Yes |
| **Stars (GitHub)** | 70k+ | ~15k | ~15k | ~5k |
| **Pricing** | Free + LLM costs | Free + LLM costs | $0.05/step + free tier | Free + LLM costs |

### Detailed Comparisons

**Browser-Use vs Stagehand**
- Browser-Use is fully autonomous: give it a goal and it plans the entire navigation. Stagehand lets you write deterministic Playwright scripts and inject AI only where needed (act, extract, observe).
- Stagehand's auto-caching records successful actions and replays them without LLM calls on repeat runs, significantly lowering costs for repeated workflows.
- Browser-Use has higher ongoing LLM costs due to continuous inference at every step; Stagehand localizes inference to specific moments.
- Browser-Use excels at exploratory/unknown workflows; Stagehand excels at enhancing existing test suites.

**Browser-Use vs Skyvern**
- Skyvern uses computer vision to understand pages without relying on DOM selectors, making it resilient to layout changes.
- Skyvern includes built-in CAPTCHA solving, 2FA/TOTP, and proxy networks. Browser-Use requires external solutions or cloud infrastructure for these.
- Skyvern's no-code YAML workflows work across multiple sites; Browser-Use requires Python scripting.
- Browser-Use has higher benchmark scores (89.1% vs 85.85%) but Skyvern specializes in form-filling tasks.

**Browser-Use vs LaVague**
- LaVague operates as a step-by-step guidance system where you describe actions ("Click the green button") and it finds and clicks. Browser-Use accepts high-level goals and plans entire navigation autonomously.
- LaVague uses a World Model (processes current page + objective to generate instructions) and an Action Engine (compiles instructions into Selenium or Playwright code).
- Browser-Use has significantly more community traction (70k+ vs ~5k stars) and active development.
- LaVague is better for repetitive tasks where you know exactly what you want; Browser-Use is better for exploratory automation.

### When to Use Which

| Scenario | Best Choice |
|----------|-------------|
| Exploratory workflows, unknown page structures | Browser-Use |
| Enhancing existing Playwright/TypeScript suites | Stagehand |
| Multi-site production automation with minimal code | Skyvern |
| Repetitive tasks with known steps | LaVague |
| Government/insurance portals needing 2FA | Skyvern |
| Python ecosystem, maximum flexibility | Browser-Use |
| TypeScript ecosystem, deterministic control | Stagehand |
| Cost-sensitive repeated workflows | Stagehand (caching) |

---

## 6. Best Practices

### Reliability Tips

1. **Set `max_steps` conservatively**: Acts as a hard billing rail. Hallucination loops (e.g., on CAPTCHAs/sliders) cause the model to repeat useless actions indefinitely without a cap.

2. **Scope tasks narrowly**: Instead of "Research all competitors and create a report", break into smaller sub-tasks. Smaller tasks have higher success rates.

3. **Use `extend_system_message` for domain-specific instructions**: Add context about the target site's layout, expected behavior, or failure modes.

4. **Prefer `storage_state` over raw credentials**: For authentication, load pre-authenticated sessions rather than passing passwords through the agent.

5. **Disable vision when handling secrets**: Set `use_vision=False` and use `sensitive_data` dict to prevent credential leakage through screenshots.

6. **Use structured output schemas**: Bind Pydantic models via `output_model_schema` to get typed, validated results instead of free-form text.

7. **Match smaller models to simpler tasks**: Use GPT-4o-mini or similar for simple reads/extractions; reserve GPT-4o/Claude for complex multi-page flows.

### Performance Optimization

8. **Use `flash_mode=True`** for speed when evaluation steps are unnecessary.

9. **Limit `max_history_items`** to control context window usage and reduce token costs.

10. **Set appropriate wait times**: Tune `minimum_wait_page_load_time`, `wait_for_network_idle_page_load_time`, and `wait_between_actions` for your target sites.

11. **Use `allowed_domains`** to prevent the agent from wandering to irrelevant sites.

### Production Deployment

12. **Use cloud browsers** (`use_cloud=True`) for CAPTCHA bypassing, lowest latency, and authentication profile sync.

13. **Add timeouts and alerts**: Monitor `total_duration_seconds()` from history and set up alerts for long-running agents.

14. **Log each step in development**: Use `AgentHistoryList` methods (`action_names()`, `errors()`, `model_thoughts()`) to debug agent behavior.

15. **Consider deterministic alternatives for high-volume work**: For schema-critical extraction at scale, traditional Playwright or pre-built scrapers are more reliable and cheaper.

### Known Limitations

- **PDF viewers**: Difficult to interact with using standard DOM methods (browser-native components with shadow DOMs).
- **Cloudflare WAF/Turnstile**: Cannot reliably bypass without specialized tools.
- **AWS Bedrock**: Fundamental incompatibilities with browser-use's structured output approach (converse() API does not handle complex union-type schemas).
- **Non-determinism**: Results vary between runs. Not suitable for high-frequency, schema-critical data pipelines without validation layers.
- **Cost**: Continuous LLM inference at every step adds up quickly. No built-in caching mechanism.

---

## 7. Code Examples

### Basic Agent Creation

```python
from browser_use import Agent, ChatBrowserUse
import asyncio

async def main():
    agent = Agent(
        task="Search for latest news about AI and return the top 3 headlines",
        llm=ChatBrowserUse(),  # Recommended: fastest and most cost-effective
    )
    history = await agent.run(max_steps=50)
    print(history.final_result())

asyncio.run(main())
```

### Using OpenAI/Other LLMs

```python
from langchain_openai import ChatOpenAI
from browser_use import Agent

agent = Agent(
    task="Navigate to hackerone.com, find top 5 highest-paying telecom bug bounty programs as JSON.",
    llm=ChatOpenAI(model="gpt-4o"),
)
result = await agent.run()
print(result.final_result())
```

### Structured Data Extraction with Pydantic

```python
from pydantic import BaseModel, Field
from browser_use import Agent, ChatBrowserUse

class CompetitorPrice(BaseModel):
    sku_name: str
    price_usd: float = Field(description="Float only; strip currency symbols")
    in_stock: bool

agent = Agent(
    task="Extract Databricks tier pricing (sku_name, price_usd, in_stock).",
    llm=ChatBrowserUse(),
    output_model_schema=CompetitorPrice,
)
result = await agent.run()
# result.final_result() returns validated CompetitorPrice instance
```

### Custom Actions with Tools Decorator

```python
from browser_use import Tools, Agent, ActionResult, BrowserSession, ChatBrowserUse

tools = Tools()

# Simple action: ask human for input
@tools.action(description='Ask human for help with a question')
async def ask_human(question: str) -> ActionResult:
    answer = input(f'{question} > ')
    return ActionResult(extracted_content=f'The human responded with: {answer}')

# Action with browser session injection
@tools.action(description='Click the submit button using CSS selector')
async def click_submit_button(browser_session: BrowserSession):
    page = await browser_session.must_get_current_page()
    elements = await page.get_elements_by_css_selector('button[type="submit"]')
    if not elements:
        return ActionResult(extracted_content='No submit button found')
    await elements[0].click()
    return ActionResult(extracted_content='Submit button clicked!')

# Action with Pydantic input model
from pydantic import BaseModel, Field

class Car(BaseModel):
    name: str = Field(description='Car model, e.g. "Toyota Camry"')
    price: int = Field(description='Price in USD, e.g. 25000')

@tools.action(description='Save cars to file')
def save_cars(cars: list[Car]) -> str:
    import json
    with open('cars.json', 'w') as f:
        json.dump([c.model_dump() for c in cars], f)
    return f'Saved {len(cars)} cars to file'

# Domain-restricted action
@tools.action(
    description='Fill out banking forms',
    allowed_domains=['https://mybank.com']
)
def fill_bank_form(account_number: str) -> str:
    return f'Filled form for account {account_number}'

# Wire tools into agent
agent = Agent(
    task='Find top 10 used cars on autotrader and save to file',
    llm=ChatBrowserUse(),
    tools=tools,
)
```

### Custom Controller with Side Effects (Legacy Pattern)

```python
from browser_use import Agent, Controller
from langchain_openai import ChatOpenAI
import polars as pl

controller = Controller()

@controller.action("Save extraction to local Parquet file")
def write_to_parquet(data: str) -> str:
    df = pl.read_json(data.encode())
    df.write_parquet("extraction_log.parquet")
    return "Write successful."

agent = Agent(
    task="Find top 10 HN posts, output to Parquet.",
    llm=ChatOpenAI(model="gpt-4o"),
    controller=controller,
)
```

### Sensitive Data / Authentication

```python
from browser_use import Agent, ChatBrowserUse

agent = Agent(
    task="Log into example.com using credentials x_username and x_password, then navigate to the dashboard",
    llm=ChatBrowserUse(),
    sensitive_data={
        "x_username": "actual_user@email.com",
        "x_password": "actual_secret_password",
    },
    use_vision=False,  # IMPORTANT: disable vision to prevent credential leakage in screenshots
)
```

### Browser Configuration

```python
from browser_use import Agent, Browser, ChatBrowserUse

browser = Browser(
    headless=False,
    allowed_domains=["*.example.com", "*.google.com"],
    downloads_path="/tmp/downloads",
    proxy={"server": "http://proxy.example.com:8080", "username": "user", "password": "pass"},
    window_size={"width": 1920, "height": 1080},
    keep_alive=True,
    minimum_wait_page_load_time=0.5,
    wait_for_network_idle_page_load_time=1.0,
)

agent = Agent(
    task="Download the latest quarterly report from investor.example.com",
    llm=ChatBrowserUse(),
    browser=browser,
)
```

### Using System Chrome Profile

```python
from browser_use import Browser

# List available Chrome profiles
profiles = Browser.list_chrome_profiles()
print(profiles)  # [{'directory': 'Default', 'name': 'Personal'}, ...]

# Use existing profile (with all cookies/sessions)
browser = Browser.from_system_chrome(profile_directory='Default')
```

### Accessing Agent History

```python
history = await agent.run(max_steps=50)

# Available history methods
print(history.urls())                    # All visited URLs
print(history.screenshot_paths())        # Screenshot file paths
print(history.action_names())            # List of actions taken
print(history.extracted_content())       # All extracted text/data
print(history.errors())                  # Any errors encountered
print(history.final_result())            # Final task result
print(history.is_done())                 # Whether task completed
print(history.model_thoughts())          # LLM reasoning at each step
print(history.total_duration_seconds())  # Total execution time
```

### Cloud Deployment with Sandbox

```python
from browser_use import sandbox

@sandbox(
    cloud_profile_id="prof_abc123",          # Use pre-authenticated profile
    cloud_proxy_country_code="US",           # Route through US proxy
)
async def my_automation():
    agent = Agent(
        task="Check account balance",
        llm=ChatBrowserUse(),
    )
    return await agent.run()
```

---

## Sources

- [Browser-Use GitHub Repository](https://github.com/browser-use/browser-use)
- [Browser-Use Official Documentation](https://docs.browser-use.com)
- [Browser-Use Changelog](https://browser-use.com/changelog)
- [Browser-Use Custom Functions Docs](https://docs.browser-use.com/customize/custom-functions)
- [Browser-Use All Parameters](https://docs.browser-use.com/customize/browser/all-parameters)
- [Browser-Use Agent Configuration](https://docs.browser-use.com/open-source/customize/agent/basics)
- [Browser-Use AGENTS.md](https://github.com/browser-use/browser-use/blob/main/AGENTS.md)
- [DeepWiki: Browser-Use Architecture](https://deepwiki.com/browser-use/browser-use)
- [browser-use PyPI](https://pypi.org/project/browser-use/)
- [Apify: browser-use 2026 Guide](https://use-apify.com/blog/browser-use-ai-browser-automation-guide)
- [DZone: Build AI Browser Agent](https://dzone.com/articles/build-ai-browser-agent-llms-playwright-browser-use)
- [NashTech: BrowserUse Overview](https://blog.nashtechglobal.com/browseruse-web-automation-library-for-ai-agents/)
- [Skyvern Blog: Browser Use vs Stagehand](https://www.skyvern.com/blog/browser-use-vs-stagehand-which-is-better/)
- [Firecrawl: 11 Best AI Browser Agents 2026](https://www.firecrawl.dev/blog/best-browser-agents)
- [Firecrawl: Browser Automation Tools Comparison](https://www.firecrawl.dev/blog/browser-automation-tools-comparison)
- [Brightdata: Build AI Agents with browser-use](https://brightdata.com/blog/ai/browser-use-with-scraping-browser)
- [Medium: Automate the Web with browser-use](https://medium.com/@yashrajputishu/automate-the-web-76623ecbddf0)
- [Medium: Develop Browser Agents](https://kailash-pathak.medium.com/develop-intelligent-browser-agents-integrating-llms-playwright-browser-use-and-web-ui-ac0836af520b)
- [Medium: Mastering Browser Sessions](https://sahilkumar1210.medium.com/mastering-browser-sessions-with-browser-use-the-backbone-of-reliable-ai-automations-f285e449f661)
- [AIMultiple: Open Source Web Agents 2026](https://aimultiple.com/open-source-web-agents)
- [Awesome Agents: AI Browser Automation 2026](https://awesomeagents.ai/tools/best-ai-browser-automation-tools-2026/)
- [RankmyAI: Browser Automation Rankings 2026](https://www.rankmyai.com/rankings/use-browser-automation-overall)
