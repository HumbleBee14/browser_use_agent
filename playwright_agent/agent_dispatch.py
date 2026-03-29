"""Playwright execution for one AgentAction — always returns ActionResult."""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import Page

from core import dom_extractor
from models.actions import ActionResult, AgentAction
from tools import browser
from tools.output import OutputManager


async def dispatch(
    action: AgentAction,
    page: Page,
    snap: dom_extractor.DOMSnapshot,
    output_mgr: OutputManager,
    seen_screenshot_hashes: set[str] | None = None,
) -> ActionResult:
    """Execute one action. Always returns ActionResult, never raises."""
    try:
        if action.action == "goto":
            return await browser.goto(page, action.url or "")

        if action.action == "click":
            return await browser.click(page, action.selector or "", snap.element_map)

        if action.action == "type":
            return await browser.type_text(
                page, action.selector or "", action.text or "", snap.element_map,
            )

        if action.action == "scroll":
            return await browser.scroll(page, action.direction or "down")

        if action.action == "screenshot":
            data = await browser.take_screenshot(page, full_page=True)
            artifact = output_mgr.save_screenshot(data, action.label or "page", page.url)
            if seen_screenshot_hashes is not None and artifact.sha256 in seen_screenshot_hashes:
                page_title = snap.title or "unknown"
                return ActionResult(
                    success=True,
                    description=(
                        f"Screenshot saved: {artifact.filename} — but this is IDENTICAL to a previous screenshot "
                        f"of \"{page_title}\" (same SHA256). You are still on the same page. "
                        f"Do NOT take another screenshot. Navigate to a new page with goto, or call done/fail."
                    ),
                )
            if seen_screenshot_hashes is not None:
                seen_screenshot_hashes.add(artifact.sha256)
            page_title = snap.title or "unknown"
            page_url = snap.url or page.url
            return ActionResult(
                success=True,
                description=(
                    f"Screenshot saved: {artifact.filename} "
                    f"(page: \"{page_title}\", url: {page_url})"
                ),
            )

        if action.action == "extract":
            return await browser.extract_text(page, action.selector or "", snap.element_map)

        if action.action == "wait":
            return await browser.wait_for(page, action.selector or "")

        if action.action == "download":
            result = await browser.download_file(page, action.selector or "", snap.element_map)
            if result.success and result.download_path:
                # Save the downloaded file to evidence folder
                temp_path = Path(result.download_path)
                if temp_path.exists():
                    data = temp_path.read_bytes()
                    suggested_name = result.download_name or temp_path.name
                    artifact = output_mgr.save_download(data, suggested_name, page.url)
                    return ActionResult(
                        success=True,
                        description=(
                            f"Downloaded: {artifact.filename} "
                            f"({len(data)} bytes, sha256: {artifact.sha256[:12]}...)"
                        ),
                    )
            return result

        if action.action == "select_option":
            return await browser.select_option(
                page, action.selector or "", action.value or "", snap.element_map,
            )

        if action.action == "save_progress":
            return ActionResult(success=True, description="Progress checkpointed")

        if action.action in ("done", "fail"):
            return ActionResult(success=True, description=f"Action: {action.action}")

        return ActionResult(success=False, error=f"Unknown action: {action.action}")

    except Exception as e:
        return ActionResult(success=False, error=f"Dispatch error: {str(e)[:200]}")
