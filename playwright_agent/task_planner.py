"""Task Planner — converts natural language instructions into executable task specs.

This is the NL → structure bridge. The user says:
  "Go to microsoft/vscode on GitHub and check the last 5 commits"

The planner calls Claude once to produce:
  1. A TaskSpec (what to extract, how to navigate)
  2. A list of SampleInputs (the work queue) OR a discovery plan

For large tasks (100+ items), the planner generates a discovery phase that
collects URLs first, then the orchestrator distributes them as parallel samples.

Usage:
  spec, samples = await plan("Go to microsoft/vscode and check last 5 commits")

Batch chunking:
  spec, samples = await plan("Extract all 200 org members from github.com/orgs/microsoft/people")
  # → planner sets needs_discovery=True, orchestrator runs discovery → 200 parallel workers
"""

from __future__ import annotations

import json

from anthropic import AsyncAnthropic

import config
from models.task import TaskSpec, SampleInput

# Reuse module-level client
_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0)
    return _client


PLANNER_SYSTEM = """You are a task planner for a browser evidence agent. Given a natural language instruction, you produce a structured execution plan.

You must return valid JSON with exactly two keys:

1. "task_spec" — a task specification with these fields:
   - task_id: short snake_case name
   - phase: "execution"
   - system_prompt: instructions for the browser agent (what pages to visit, what to extract, when to screenshot)
   - goal: what to extract, in plain language
   - keywords: list of relevant terms for DOM pruning
   - output_schema: dict of field_name → type (string, number, boolean, array, string | null)
   - required_fields: list of fields that must be non-null for completion
   - required_artifacts: list of screenshot labels the agent must capture
   - max_steps: integer (simple tasks: 8-12, multi-page: 15-25)
   - judgment_required: boolean
   - judgment_question: string or null
   - judgment_output_schema: dict or null

2. "samples" — a list of work items, each with:
   - sample_id: unique identifier
   - url: starting URL for this sample

CRITICAL RULES FOR SAMPLES:
- Only generate multiple samples if you can construct DIFFERENT, SPECIFIC URLs for each one.
  GOOD: 3 samples with 3 different commit SHA URLs (if you know the SHAs)
  BAD: 3 samples all pointing to the same listing page

- If you CANNOT know the specific URLs in advance (e.g., "last 3 commits" — you don't know the SHAs, or "all org members" — you don't know the usernames), generate ONE sample pointing to the listing/index page. Set the system_prompt to instruct the agent to:
  1. Navigate to the listing page
  2. Find the N most recent items
  3. Click into each one, extract data, take screenshots
  4. Call done with ALL items collected

- The output_schema for multi-item tasks should use a flat structure with indexed fields:
  e.g., items: "array" where each item has the fields you need
  OR use a simple array: items: "array"

IMPORTANT:
- The browser agent can only see what's in the browser. No API calls.
- Set missing/unknown output fields to null.
- Keep system_prompt concise and directive — tell the agent exactly what steps to take.
- The agent has these actions: goto, click, type, scroll, screenshot, extract, wait, save_progress, done, fail.
- save_progress: checkpoints partial data without stopping. Use it for multi-item tasks where the agent collects data from multiple pages. Tell the agent to "call save_progress after each item" in the system_prompt.
- For multi-item tasks on a single page, give more max_steps (25-40).
- For multi-page navigation (click into each item, extract, go back, repeat), give 40-60 max_steps and ALWAYS instruct the agent to use save_progress after each item.

BATCH CHUNKING — for large-scale tasks:
- If the instruction implies 10+ items that each have their own URL, include "needs_discovery": true and "discovery_url" in the response.
- The system will run a discovery phase first to collect all URLs, then distribute them as parallel samples.
- Example: "Extract all org members" → set needs_discovery: true, discovery_url: "https://github.com/orgs/microsoft/people"
- The discovery agent will paginate and collect URLs. Each URL becomes a separate parallel sample.

EXAMPLE 1 — Single page extraction: "Go to torvalds GitHub profile and extract their name, followers, and pinned repos"

```json
{
  "task_spec": {
    "task_id": "github_profile_torvalds",
    "phase": "execution",
    "system_prompt": "You are a browser agent. Navigate to the GitHub profile page. Take one screenshot labeled 'profile'. Extract the display name, followers count, and pinned repository names from the page state. Do NOT scroll or revisit. Call done with all fields as soon as you have them. Set missing fields to null.",
    "goal": "Extract display_name, followers, pinned_repos from the GitHub profile. Take one screenshot.",
    "keywords": ["followers", "pinned", "repositories", "bio"],
    "output_schema": {
      "display_name": "string",
      "followers": "string | null",
      "pinned_repos": "array | null"
    },
    "required_fields": ["display_name"],
    "required_artifacts": ["profile"],
    "max_steps": 10,
    "judgment_required": false,
    "judgment_question": null,
    "judgment_output_schema": null
  },
  "samples": [
    {"sample_id": "torvalds", "url": "https://github.com/torvalds"}
  ]
}
```

EXAMPLE 2 — Multi-item from listing page: "Go to Hacker News and extract the top 5 posts with title, score, and author"

```json
{
  "task_spec": {
    "task_id": "hackernews_top_posts",
    "phase": "execution",
    "system_prompt": "You are a browser agent. Navigate to the Hacker News front page. Take a screenshot labeled 'frontpage'. Read the page state and extract the first 5 posts: each post's title, score (points), and author (submitted by). Return them as an array in the 'posts' field. Call done immediately after extracting — do NOT click into individual posts.",
    "goal": "Extract the top 5 posts from the front page. Each post needs: title, score, author. Return as an array.",
    "keywords": ["points", "submitted", "comments", "title", "score"],
    "output_schema": {
      "posts": "array"
    },
    "required_fields": ["posts"],
    "required_artifacts": ["frontpage"],
    "max_steps": 8,
    "judgment_required": false,
    "judgment_question": null,
    "judgment_output_schema": null
  },
  "samples": [
    {"sample_id": "hackernews_top5", "url": "https://news.ycombinator.com"}
  ]
}
```

KEY PATTERNS FROM EXAMPLES:
- system_prompt is SHORT and DIRECTIVE — tells agent exactly what to do step by step
- system_prompt MUST always include: "Read the data from the page state text provided to you. Take ONE screenshot for evidence, then call done with all extracted data."
- system_prompt always says "Call done as soon as you have the data" and "Set missing fields to null"
- required_artifacts uses SHORT GENERIC labels like ["profile", "page"] — NOT numbered like ["profile_1", "profile_2"]. The agent may name screenshots "profile_alice", "profile_bob" etc., and the check passes if ANY screenshot contains the label as a substring. So ["profile"] matches "02_profile_alice.png". NEVER use numbered artifact labels.
- For multi-item tasks: ONE sample with listing URL, agent extracts all items, returns as array
- max_steps: single page = 8-15, multi-page navigation = 15-25, discovery/listing = 25-40
- IMPORTANT: The agent reads data from the DOM/page state text, NOT from screenshots. Screenshots are for evidence only. The system_prompt must make this clear.

Return ONLY valid JSON. No markdown, no explanation."""


def _parse_planner_response(text: str) -> dict:
    """Extract JSON from Claude's response, handling markdown wrapping."""
    text = text.strip()

    if "```" in text:
        import re
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if json_match:
            text = json_match.group(1).strip()

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON: {str(e)[:200]}\nRaw: {text[:500]}")


def _build_task_spec(spec_data: dict) -> TaskSpec:
    """Build TaskSpec with sensible defaults for fields the planner might skip."""
    spec_data.setdefault("phase", "execution")
    spec_data.setdefault("pagination", False)
    spec_data.setdefault("stop_condition", "")
    spec_data.setdefault("input_schema", {})
    spec_data.setdefault("auth_profile", None)
    spec_data.setdefault("required_artifacts", [])
    spec_data.setdefault("required_fields", [])
    spec_data.setdefault("judgment_required", False)
    spec_data.setdefault("judgment_question", None)
    spec_data.setdefault("judgment_output_schema", None)
    return TaskSpec(**spec_data)


async def plan(instruction: str) -> tuple[TaskSpec, list[SampleInput]]:
    """Convert natural language instruction into TaskSpec + samples.

    Returns (task_spec, samples) ready for execution.

    If the planner detects a large-scale task, it may set needs_discovery=True
    with a discovery_url. The caller (main.py) should then run discovery first
    to collect URLs, and use those as parallel samples.
    """
    client = _get_client()

    response = await client.messages.create(
        model=config.LLM_MODEL,
        max_tokens=4096,
        system=PLANNER_SYSTEM,
        messages=[{"role": "user", "content": instruction}],
    )

    data = _parse_planner_response(response.content[0].text)
    task_spec = _build_task_spec(data["task_spec"])

    # Build samples
    samples = []
    for s in data.get("samples", []):
        samples.append(SampleInput(
            sample_id=s.get("sample_id", "sample_001"),
            url=s.get("url", ""),
            extra=s.get("extra", {}),
        ))

    # Batch chunking: if planner flagged needs_discovery, attach metadata
    # so the orchestrator can run discovery → parallel execution
    if data.get("needs_discovery"):
        task_spec._discovery_url = data.get("discovery_url", "")
        task_spec._needs_discovery = True

    return task_spec, samples


async def plan_chunked(
    instruction: str,
    chunk_size: int = 10,
) -> tuple[TaskSpec, TaskSpec | None, list[SampleInput]]:
    """Plan with batch chunking awareness.

    Returns:
        (execution_spec, discovery_spec_or_None, initial_samples)

    If the task is large-scale, returns a discovery spec that should be run
    first to collect URLs. Those URLs are then split into chunks and run as
    parallel samples against the execution spec.
    """
    task_spec, samples = await plan(instruction)

    if not getattr(task_spec, "_needs_discovery", False):
        return task_spec, None, samples

    discovery_url = getattr(task_spec, "_discovery_url", "")
    if not discovery_url:
        return task_spec, None, samples

    discovery_spec = TaskSpec(
        task_id=f"{task_spec.task_id}_discovery",
        phase="discovery",
        system_prompt=(
            f"You are a discovery agent. Navigate to {discovery_url}. "
            f"Collect all item URLs by paginating through the list. "
            f"Extract each item's URL and a short identifier. "
            f"Call done with the full list when you've collected all items."
        ),
        goal=f"Paginate and collect all item URLs from {discovery_url}",
        keywords=task_spec.keywords,
        output_schema={"urls": "array"},
        required_fields=["urls"],
        max_steps=40,
    )

    return task_spec, discovery_spec, samples
