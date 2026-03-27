"""Thin Playwright wrappers — the agent loop calls these, never touches Playwright directly.

Every function returns an ActionResult, never raises.
After every navigation action: wait_for_load_state("networkidle").
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from models.actions import ActionResult

# Per-domain rate limiting (seconds between requests)
RATE_LIMITS: dict[str, float] = {
    "linkedin.com": 3.0,
    "github.com": 0.5,
    "atlassian.net": 1.0,
    "linear.app": 1.0,
    "default": 0.2,
}
_last_request: dict[str, float] = {}
_rate_lock = asyncio.Lock()


async def _rate_limit(url: str) -> None:
    """Sleep if we're hitting a domain too fast. Concurrency-safe via asyncio.Lock."""
    import time
    domain = urlparse(url).netloc.replace("www.", "")
    interval = RATE_LIMITS.get(domain, RATE_LIMITS["default"])
    async with _rate_lock:
        last = _last_request.get(domain, 0)
        elapsed = time.time() - last
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        _last_request[domain] = time.time()


async def _wait_stable(page: Page, timeout: float = 8000) -> None:
    """Wait for page to stabilize after navigation."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeout:
        pass  # JS-heavy pages may never fully idle — proceed anyway


async def goto(page: Page, url: str) -> ActionResult:
    """Navigate to a URL."""
    try:
        await _rate_limit(url)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await _wait_stable(page)
        return ActionResult(success=True, description=f"Navigated to {page.url}")
    except PlaywrightTimeout:
        return ActionResult(success=False, error=f"Timeout navigating to {url}")
    except Exception as e:
        return ActionResult(success=False, error=f"Navigation error: {str(e)[:200]}")


async def click(page: Page, selector: str, element_map: dict[str, str] | None = None) -> ActionResult:
    """Click an element. Tries index → text → CSS selector in order."""
    try:
        clicked = False

        # Strategy 1: Index-based (from DOM extractor's map)
        if element_map and selector.isdigit():
            pw_selector = element_map.get(selector)
            if pw_selector:
                await page.click(pw_selector, timeout=5000)
                clicked = True

        # Strategy 2: Text-based
        if not clicked:
            try:
                locator = page.get_by_text(selector, exact=False)
                if await locator.count() > 0:
                    await locator.first.click(timeout=5000)
                    clicked = True
            except (PlaywrightTimeout, Exception):
                pass

        # Strategy 3: CSS selector (last resort)
        if not clicked:
            try:
                await page.click(selector, timeout=5000)
                clicked = True
            except (PlaywrightTimeout, Exception):
                pass

        if not clicked:
            return ActionResult(
                success=False,
                error=f"Element not found: '{selector}'. Try a different selector or text.",
            )

        await _wait_stable(page)
        return ActionResult(success=True, description=f"Clicked '{selector}'")

    except Exception as e:
        return ActionResult(success=False, error=f"Click error: {str(e)[:200]}")


async def type_text(page: Page, selector: str, text: str, element_map: dict[str, str] | None = None) -> ActionResult:
    """Fill an input field. Uses page.fill() which clears first."""
    try:
        filled = False

        # Strategy 1: Index-based
        if element_map and selector.isdigit():
            pw_selector = element_map.get(selector)
            if pw_selector:
                await page.fill(pw_selector, text, timeout=5000)
                filled = True

        # Strategy 2: Label/placeholder text
        if not filled:
            try:
                locator = page.get_by_label(selector)
                if await locator.count() > 0:
                    await locator.first.fill(text, timeout=5000)
                    filled = True
            except (PlaywrightTimeout, Exception):
                pass

        # Strategy 3: Placeholder text
        if not filled:
            try:
                locator = page.get_by_placeholder(selector, exact=False)
                if await locator.count() > 0:
                    await locator.first.fill(text, timeout=5000)
                    filled = True
            except (PlaywrightTimeout, Exception):
                pass

        # Strategy 4: CSS selector
        if not filled:
            try:
                await page.fill(selector, text, timeout=5000)
                filled = True
            except (PlaywrightTimeout, Exception):
                pass

        if not filled:
            return ActionResult(
                success=False,
                error=f"Input not found: '{selector}'. Try a different selector.",
            )

        return ActionResult(success=True, description=f"Typed '{text[:50]}' into '{selector}'")

    except Exception as e:
        return ActionResult(success=False, error=f"Type error: {str(e)[:200]}")


async def scroll(page: Page, direction: str = "down") -> ActionResult:
    """Scroll the page up or down."""
    try:
        delta = 600 if direction == "down" else -600
        await page.mouse.wheel(0, delta)
        await asyncio.sleep(0.3)  # brief pause for lazy-loaded content
        return ActionResult(success=True, description=f"Scrolled {direction}")
    except Exception as e:
        return ActionResult(success=False, error=f"Scroll error: {str(e)[:200]}")


async def wait_for(page: Page, selector: str) -> ActionResult:
    """Wait for an element to appear (max 10 seconds)."""
    try:
        # Try text first
        locator = page.get_by_text(selector, exact=False)
        await locator.first.wait_for(state="visible", timeout=10000)
        return ActionResult(success=True, description=f"Element appeared: '{selector}'")
    except (PlaywrightTimeout, Exception):
        pass

    try:
        await page.wait_for_selector(selector, state="visible", timeout=10000)
        return ActionResult(success=True, description=f"Element appeared: '{selector}'")
    except PlaywrightTimeout:
        return ActionResult(success=False, error=f"Timeout waiting for '{selector}' (10s)")
    except Exception as e:
        return ActionResult(success=False, error=f"Wait error: {str(e)[:200]}")


async def take_screenshot(page: Page, full_page: bool = True) -> bytes:
    """Capture a screenshot. Returns raw PNG bytes.

    Light theme is forced, animations disabled for deterministic output.
    """
    await page.emulate_media(color_scheme="light")
    return await page.screenshot(full_page=full_page, type="png", animations="disabled")


async def extract_text(page: Page, selector: str, element_map: dict[str, str] | None = None) -> ActionResult:
    """Read text content from an element."""
    try:
        text = ""

        # Strategy 1: Index-based
        if element_map and selector.isdigit():
            pw_selector = element_map.get(selector)
            if pw_selector:
                text = await page.inner_text(pw_selector, timeout=5000)

        # Strategy 2: Text locator
        if not text:
            try:
                locator = page.get_by_text(selector, exact=False)
                if await locator.count() > 0:
                    text = await locator.first.inner_text(timeout=5000)
            except (PlaywrightTimeout, Exception):
                pass

        # Strategy 3: CSS selector
        if not text:
            try:
                text = await page.inner_text(selector, timeout=5000)
            except (PlaywrightTimeout, Exception):
                pass

        if text:
            return ActionResult(
                success=True,
                description=f"Extracted {len(text)} chars",
                extracted_text=text[:2000],  # cap at 2000 chars
            )
        return ActionResult(
            success=False,
            error=f"No text found for '{selector}'",
            extracted_text="",
        )

    except Exception as e:
        return ActionResult(success=False, error=f"Extract error: {str(e)[:200]}")


async def get_page_info(page: Page) -> dict:
    """Get current page URL, title, and viewport info."""
    return {
        "url": page.url,
        "title": await page.title(),
    }
