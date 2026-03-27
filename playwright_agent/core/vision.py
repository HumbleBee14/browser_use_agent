"""Vision module — screenshot capture + Claude multimodal analysis.

Activated when:
  1. dom_confidence < 0.6 (canvas/SVG-heavy pages)
  2. Task spec requires a screenshot
  3. Agent explicitly calls screenshot action

Key rules:
  - Targeted questions only ("What is the status icon next to X?")
  - Never open-ended ("Describe this page")
  - Light theme forced for consistent white screenshots
"""

from __future__ import annotations

import base64

from anthropic import AsyncAnthropic
from playwright.async_api import Page

import config

# Module-level client for connection reuse across vision calls
_client: AsyncAnthropic | None = None


async def capture_screenshot(page: Page, full_page: bool = True) -> bytes:
    """Capture a screenshot with consistent settings.

    - Light color scheme (white background for OCR/audit)
    - 1280x900 viewport
    - Animations disabled for deterministic output
    """
    # Light theme + viewport are set once in worker.py — don't re-set to avoid flickering
    return await page.screenshot(
        full_page=full_page,
        type="png",
        animations="disabled",
    )


async def analyze_screenshot(
    screenshot_bytes: bytes,
    question: str,
    page_context: str = "",
    model: str | None = None,
) -> str:
    """Send a screenshot to Claude vision with a targeted question.

    Args:
        screenshot_bytes: Raw PNG bytes
        question: Specific question about the screenshot (NOT "describe this page")
        page_context: Optional DOM text for hybrid DOM+vision analysis
        model: LLM model override (defaults to config.LLM_MODEL)

    Returns:
        Claude's answer as a string
    """
    model = model or config.LLM_MODEL

    content = []

    # Add DOM context if available (hybrid mode)
    if page_context:
        content.append({
            "type": "text",
            "text": f"Page DOM context:\n{page_context}\n\n",
        })

    # Add the screenshot
    content.append({
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(screenshot_bytes).decode(),
        },
    })

    # Add the targeted question
    content.append({
        "type": "text",
        "text": question,
    })

    # Reuse module-level client for HTTP connection pooling across calls
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0)
    client = _client

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": content}],
        )
        return response.content[0].text
    except Exception as e:
        return f"Vision analysis failed: {str(e)[:200]}"


def build_vision_question(dom_context: str, task_goal: str) -> str:
    """Build a targeted vision question based on what the DOM couldn't answer.

    Used when dom_confidence < 0.6 — the DOM found structure but couldn't
    read visual-only signals (SVG icons, colored status badges, etc.).
    """
    return (
        f"The DOM shows interactive elements but some visual information is missing. "
        f"Based on the task goal: '{task_goal}', "
        f"what information is visible in this screenshot that the following DOM text does not capture?\n\n"
        f"DOM text:\n{dom_context[:1000]}\n\n"
        f"Focus on: status icons, color-coded badges, visual indicators, "
        f"and any text rendered as images or SVGs."
    )
