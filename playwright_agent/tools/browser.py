"""Thin Playwright wrappers — the agent loop calls these, never touches Playwright directly.

Every function returns an ActionResult, never raises.
After every navigation action: wait_for_load_state("networkidle").
"""

from __future__ import annotations

import asyncio
import time
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
    domain = urlparse(url).netloc.replace("www.", "")
    interval = RATE_LIMITS.get(domain, RATE_LIMITS["default"])
    async with _rate_lock:
        last = _last_request.get(domain, 0)
        elapsed = time.time() - last
        if elapsed < interval:
            await asyncio.sleep(interval - elapsed)
        _last_request[domain] = time.time()


async def _resolve_element(page: Page, selector: str, element_map: dict[str, str] | None):
    """Resolve a selector to a Playwright Locator using 3 strategies.

    Returns (locator, strategy_name) or (None, None) if not found.
    Element map values are stored as "role:name" by dom_extractor.
    """
    # Strategy 1: Index-based via element_map (most reliable)
    if element_map and selector.isdigit():
        role_name = element_map.get(selector)
        if role_name and ":" in role_name:
            role, name = role_name.split(":", 1)
            try:
                locator = page.get_by_role(role, name=name)
                if await locator.count() > 0:
                    return locator.first, "index"
            except Exception:
                pass

    # Strategy 2: Text-based
    try:
        locator = page.get_by_text(selector, exact=False)
        if await locator.count() > 0:
            return locator.first, "text"
    except Exception:
        pass

    # Strategy 3: CSS selector (last resort)
    try:
        locator = page.locator(selector)
        if await locator.count() > 0:
            return locator.first, "css"
    except Exception:
        pass

    return None, None


async def _wait_stable(page: Page, timeout: float = 8000) -> None:
    """Staged readiness: domcontentloaded → short stabilization → proceed.

    Better than blind networkidle which either:
    - wastes time on noisy background requests, or
    - snapshots too early if meaningful content renders after initial idle.
    """
    # Stage 1: Wait for DOM to be loaded (fast, reliable)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except PlaywrightTimeout:
        pass

    # Stage 2: Brief stabilization for SPAs that render after DOMContentLoaded
    await asyncio.sleep(0.5)

    # Stage 3: Try networkidle with short timeout — proceed if it doesn't settle
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
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
        locator, strategy = await _resolve_element(page, selector, element_map)
        if not locator:
            return ActionResult(
                success=False,
                error=f"Element not found: '{selector}'. Try a different selector or text.",
            )

        await locator.click(timeout=5000)
        await _wait_stable(page)
        return ActionResult(success=True, description=f"Clicked '{selector}' (via {strategy})")

    except Exception as e:
        return ActionResult(success=False, error=f"Click error: {str(e)[:200]}")


async def type_text(page: Page, selector: str, text: str, element_map: dict[str, str] | None = None) -> ActionResult:
    """Fill an input field. Uses locator.fill() which clears first."""
    try:
        locator, strategy = await _resolve_element(page, selector, element_map)

        # Also try label and placeholder (common for form fields)
        if not locator:
            try:
                loc = page.get_by_label(selector)
                if await loc.count() > 0:
                    locator, strategy = loc.first, "label"
            except Exception:
                pass

        if not locator:
            try:
                loc = page.get_by_placeholder(selector, exact=False)
                if await loc.count() > 0:
                    locator, strategy = loc.first, "placeholder"
            except Exception:
                pass

        if not locator:
            return ActionResult(
                success=False,
                error=f"Input not found: '{selector}'. Try a different selector.",
            )

        await locator.fill(text, timeout=5000)
        return ActionResult(success=True, description=f"Typed '{text[:50]}' into '{selector}' (via {strategy})")

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
    # Light theme + viewport are set once in worker.py — don't re-set here to avoid flickering
    return await page.screenshot(full_page=full_page, type="png", animations="disabled")


async def extract_text(page: Page, selector: str, element_map: dict[str, str] | None = None) -> ActionResult:
    """Read text content from an element."""
    try:
        locator, strategy = await _resolve_element(page, selector, element_map)
        if locator:
            text = await locator.inner_text(timeout=5000)
            truncated = text[:2000]
            if len(text) > 2000:
                truncated += f"\n... [truncated, full text was {len(text)} chars]"
            return ActionResult(
                success=True,
                description=f"Extracted {len(text)} chars (via {strategy})",
                extracted_text=truncated,
            )

        return ActionResult(
            success=False,
            error=f"No text found for '{selector}'",
            extracted_text="",
        )

    except Exception as e:
        return ActionResult(success=False, error=f"Extract error: {str(e)[:200]}")


async def download_file(
    page: Page, selector: str, element_map: dict[str, str] | None = None,
) -> ActionResult:
    """Click a download link/button and capture the downloaded file.

    Returns ActionResult with the temporary downloaded file path and the browser's
    suggested filename so the caller can preserve a human-meaningful artifact name.
    """
    try:
        locator, strategy = await _resolve_element(page, selector, element_map)
        if not locator:
            return ActionResult(
                success=False,
                error=f"Download target not found: '{selector}'",
            )

        # Intercept the download triggered by clicking
        async with page.expect_download(timeout=30000) as download_info:
            await locator.click(timeout=5000)

        download = await download_info.value
        suggested_name = download.suggested_filename or "download"
        temp_path = await download.path()

        if not temp_path:
            return ActionResult(
                success=False,
                error="Download started but no file was saved",
            )

        # Read file bytes for saving via OutputManager
        data = temp_path.read_bytes()
        size = len(data)

        return ActionResult(
            success=True,
            description=f"Downloaded: {suggested_name} ({size} bytes)",
            download_path=str(temp_path),
            download_name=suggested_name,
        )

    except PlaywrightTimeout:
        return ActionResult(
            success=False,
            error=f"Download timeout (30s) for '{selector}' — link may not trigger a download",
        )
    except Exception as e:
        return ActionResult(success=False, error=f"Download error: {str(e)[:200]}")


async def select_option(
    page: Page, selector: str, value: str, element_map: dict[str, str] | None = None,
) -> ActionResult:
    """Select an option from a native <select> dropdown.

    Args:
        selector: Element index or label of the <select>
        value: Option text to select (matched by visible label)
    """
    try:
        locator, strategy = await _resolve_element(page, selector, element_map)

        # Labels are common for native <select> fields but are not covered by the
        # generic element resolver, so try them explicitly before failing.
        if not locator:
            try:
                loc = page.get_by_label(selector)
                if await loc.count() > 0:
                    locator, strategy = loc.first, "label"
            except Exception:
                pass

        if not locator:
            return ActionResult(
                success=False,
                error=f"Select element not found: '{selector}'",
            )

        # Try selecting by visible label text first, fall back to value
        try:
            await locator.select_option(label=value, timeout=5000)
        except Exception:
            await locator.select_option(value=value, timeout=5000)

        return ActionResult(
            success=True,
            description=f"Selected '{value}' from '{selector}' (via {strategy})",
        )

    except Exception as e:
        return ActionResult(success=False, error=f"Select error: {str(e)[:200]}")


async def get_page_info(page: Page) -> dict:
    """Get current page URL, title, and viewport info."""
    return {
        "url": page.url,
        "title": await page.title(),
    }
