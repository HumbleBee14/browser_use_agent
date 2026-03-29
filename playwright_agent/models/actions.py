"""Action schema — the 10 typed actions the agent can take.

Claude always returns one of these via tool_choice={"type":"any"}.
No free-form prose. If it can't proceed, it returns "fail" with a note.

Each action optionally includes structured reflection fields:
- evaluation_previous_step: self-assessment of last action's outcome
- memory_update: working memory scratchpad for continuity across steps
- next_goal: declared intent before acting (improves recovery & planning)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class AgentAction(BaseModel):
    """Single action selected by the LLM each step."""

    action: Literal[
        "goto",           # navigate to a URL
        "click",          # click an element by index or text
        "type",           # fill an input field
        "scroll",         # scroll up or down
        "screenshot",     # capture full-page evidence screenshot
        "extract",        # read text from an element into history
        "wait",           # wait for an element to appear
        "save_progress",  # checkpoint partial data without stopping
        "done",           # task complete — write extracted data
        "fail",           # unrecoverable — write reason and stop
    ]
    selector: str | None = None     # click, type, extract, wait
    url: str | None = None          # goto
    text: str | None = None         # type
    direction: str | None = None    # scroll: "up" | "down"
    extracted: dict[str, Any] | None = None  # done: structured output
    note: str | None = None         # fail: reason string
    label: str | None = None        # screenshot: filename label
    # Structured reflection (inspired by browser-use's decision hygiene)
    evaluation_previous_step: str | None = None  # "Did my last action work?"
    memory_update: str | None = None             # "What to remember going forward"
    next_goal: str | None = None                 # "What I'll do next and why"


class ActionResult(BaseModel):
    """Outcome of executing one action. Always returned, never raises."""

    success: bool
    description: str = ""
    error: str | None = None
    extracted_text: str | None = None  # for "extract" action


class StepRecord(BaseModel):
    """One entry in action_log.json — full audit trail per step."""

    step: int
    thinking: str = ""
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    url: str = ""
    evaluation: str = ""
    memory_update: str = ""
    next_goal: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EvidenceArtifact(BaseModel):
    """One screenshot or downloaded file with provenance."""

    filename: str
    sha256: str
    source_url: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SampleResult(BaseModel):
    """Complete output for one sample — written to result.json."""

    sample_id: str
    status: Literal["done", "failed", "needs_review", "partial_success"] = "failed"
    steps: int = 0
    extracted: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[EvidenceArtifact] = Field(default_factory=list)
    judgment: dict[str, Any] | None = None
    flagged: bool = False
    notes: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


def action_tool_schema(*, include_reflection: bool = True) -> list[dict]:
    """Generate Anthropic tool_use schema for the 10 agent actions.

    When ``include_reflection`` is False (``REFLECTION_MODE=light``), reflection
    fields are omitted from tool definitions to reduce per-request token overhead.
    """
    reflection_properties: dict = {}
    if include_reflection:
        reflection_properties = {
            "evaluation_previous_step": {
                "type": "string",
                "description": "One sentence: did your previous action succeed or fail, and why?",
            },
            "memory_update": {
                "type": "string",
                "description": "One sentence: key fact to remember for upcoming steps.",
            },
            "next_goal": {
                "type": "string",
                "description": "One sentence: what you intend to accomplish with this action.",
            },
        }
    return [
        {
            "name": "goto",
            "description": "Navigate the browser to a URL.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL including https://"},
                    **reflection_properties,
                },
                "required": ["url"],
            },
        },
        {
            "name": "click",
            "description": (
                "Click an element. Use the integer index from the page state "
                "(e.g. '3' for element [3]), or the visible text of the element."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Element index (e.g. '3') or visible text (e.g. 'Show all checks')",
                    },
                    **reflection_properties,
                },
                "required": ["selector"],
            },
        },
        {
            "name": "type",
            "description": "Type text into an input field. Clears existing content first.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Element index or visible label of the input field",
                    },
                    "text": {"type": "string", "description": "Text to type"},
                    **reflection_properties,
                },
                "required": ["selector", "text"],
            },
        },
        {
            "name": "scroll",
            "description": "Scroll the page up or down to reveal more content.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "Scroll direction",
                    },
                    **reflection_properties,
                },
                "required": ["direction"],
            },
        },
        {
            "name": "screenshot",
            "description": (
                "Take a full-page evidence screenshot. Use a short descriptive label "
                "like 'profile', 'commit_page', 'checks', 'form_filled'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Short label for the screenshot filename",
                    },
                    **reflection_properties,
                },
                "required": ["label"],
            },
        },
        {
            "name": "extract",
            "description": "Read the text content of a specific element into action history.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Element index or text to locate the element",
                    },
                    **reflection_properties,
                },
                "required": ["selector"],
            },
        },
        {
            "name": "wait",
            "description": "Wait for a specific element to appear on the page (max 10 seconds).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "Text or selector to wait for",
                    },
                    **reflection_properties,
                },
                "required": ["selector"],
            },
        },
        {
            "name": "save_progress",
            "description": (
                "Save partial extracted data as a checkpoint WITHOUT stopping the task. "
                "Use this when you have collected some data (e.g. from one page) and need to "
                "continue collecting more from other pages. Data is merged across calls — "
                "each call adds to what was saved before. Continue working after calling this."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "extracted": {
                        "type": "object",
                        "description": "Partial extracted data to checkpoint (merged with previous saves)",
                    },
                    "note": {
                        "type": "string",
                        "description": "Brief note about progress (e.g. 'Completed PR #1 of 5')",
                    },
                    **reflection_properties,
                },
                "required": ["extracted"],
            },
        },
        {
            "name": "done",
            "description": (
                "Mark the task as complete. Provide all extracted data matching "
                "the output_schema. All required_fields must be present."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "extracted": {
                        "type": "object",
                        "description": "Extracted data matching the task's output_schema",
                    },
                    **reflection_properties,
                },
                "required": ["extracted"],
            },
        },
        {
            "name": "fail",
            "description": "Mark the task as failed. Provide a clear reason why.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Why the task cannot be completed"},
                    **reflection_properties,
                },
                "required": ["note"],
            },
        },
    ]
