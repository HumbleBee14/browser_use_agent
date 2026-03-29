"""Phase 2 regression tests — DOM extractor + vision module.

Run: python tests/test_phase2.py
Note: These tests launch a real browser (headless) and hit real URLs.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright
from core.dom_extractor import (
    snapshot, serialize, DOMSnapshot, DOMNode, PageMetrics,
    _parse_aria_snapshot, _filter_semantic, _keyword_score, _compute_confidence,
    SEMANTIC_ROLES, INTERACTIVE_ROLES,
)
from core.vision import capture_screenshot


# ---- Unit tests (no browser) ----

def test_parse_aria_snapshot_basic():
    raw = """- heading "Page Title" [level=1]
- link "About":
  - /url: https://example.com/about
- button "Sign in"
- textbox "Search"
"""
    nodes = _parse_aria_snapshot(raw)
    assert len(nodes) >= 3
    heading = next(n for n in nodes if n.role == "heading")
    assert heading.name == "Page Title"
    assert heading.level == 1
    link = next(n for n in nodes if n.role == "link" and n.name == "About")
    assert link.url == "https://example.com/about"


def test_parse_aria_skips_navigation():
    raw = """- navigation "Global":
  - link "Home":
    - /url: /
  - link "About":
    - /url: /about
- heading "Main Content" [level=1]
- button "Action"
"""
    nodes = _parse_aria_snapshot(raw)
    # Navigation block should be skipped
    names = [n.name for n in nodes]
    assert "Home" not in names
    assert "Main Content" in names
    assert "Action" in names


def test_filter_semantic():
    nodes = [
        DOMNode(role="heading", name="Title"),
        DOMNode(role="region", name=""),  # no name, filtered
        DOMNode(role="link", name="Click me"),
        DOMNode(role="button", name="Submit"),
    ]
    filtered = _filter_semantic(nodes)
    assert len(filtered) == 3  # heading, link, button (region has no name)


def test_keyword_scoring():
    nodes = [
        DOMNode(role="link", name="followers"),
        DOMNode(role="heading", name="Navigation"),
        DOMNode(role="link", name="bio section"),
        DOMNode(role="button", name="Something else"),
    ]
    scored = _keyword_score(nodes, ["followers", "bio"])
    # Boosted nodes should come first
    assert scored[0].name == "followers"
    assert scored[1].name == "bio section"


def test_compute_confidence_healthy():
    metrics = PageMetrics(
        total_nodes=50, semantic_nodes=30, interactive_nodes=15,
        canvas_count=0, svg_count=2, missing_aria_labels=1,
    )
    c = _compute_confidence(metrics)
    assert c > 0.6, f"Expected > 0.6, got {c}"


def test_compute_confidence_svg_heavy():
    """SVG-heavy pages should score slightly lower than healthy but still above vision threshold.

    SVGs are mostly decorative (icons, arrows) — they shouldn't tank confidence.
    Only canvas and missing ARIA labels are strong signals of broken DOM.
    """
    metrics = PageMetrics(
        total_nodes=50, semantic_nodes=30, interactive_nodes=10,
        canvas_count=0, svg_count=20, missing_aria_labels=5,
    )
    c = _compute_confidence(metrics)
    # SVG-heavy should still be above vision threshold (0.6) — SVGs are decorative
    assert c > 0.6, f"SVG-heavy should NOT trigger vision, got {c}"
    # But should be lower than a healthy page (slight penalty)
    healthy = PageMetrics(total_nodes=50, semantic_nodes=30, interactive_nodes=15,
                          canvas_count=0, svg_count=2, missing_aria_labels=1)
    assert c < _compute_confidence(healthy), "SVG-heavy should score lower than healthy"


def test_compute_confidence_empty():
    metrics = PageMetrics(total_nodes=0)
    c = _compute_confidence(metrics)
    assert c == 0.1


def test_serialize_format():
    snap = DOMSnapshot(
        url="https://example.com",
        title="Test Page",
        nodes=[
            DOMNode(index=0, role="heading", name="Title", level=1),
            DOMNode(index=1, role="link", name="About", url="https://example.com/about"),
            DOMNode(index=2, role="button", name="Submit"),
        ],
    )
    text = serialize(snap)
    assert "URL: https://example.com" in text
    assert "[0]" in text
    assert "[heading]" in text
    assert '"Title"' in text
    assert "→ https://example.com/about" in text


# ---- Integration tests (require browser) ----

async def _test_github_profile():
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page()

    await page.goto("https://github.com/torvalds", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except:
        pass

    snap = await snapshot(page, keywords=["bio", "followers", "pinned"])
    assert len(snap.nodes) > 5, f"Expected >5 nodes, got {len(snap.nodes)}"
    assert snap.confidence > 0.4, f"Expected confidence >0.4, got {snap.confidence}"
    assert snap.url == "https://github.com/torvalds"

    text = serialize(snap)
    assert "torvalds" in text.lower() or "linus" in text.lower()

    await browser.close()
    await p.stop()
    return True


async def _test_screenshot_capture():
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page()

    await page.goto("https://github.com/torvalds", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except:
        pass

    data = await capture_screenshot(page, full_page=False)
    assert isinstance(data, bytes)
    assert len(data) > 1000, f"Screenshot too small: {len(data)} bytes"
    # PNG magic bytes
    assert data[:4] == b'\x89PNG'

    await browser.close()
    await p.stop()
    return True


async def _test_element_map_click():
    """Prove that element_map from dom_extractor works with browser.click()."""
    from tools.browser import click

    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page()

    await page.goto("https://github.com/torvalds", wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except:
        pass

    snap = await snapshot(page, keywords=["linux", "pinned"])

    # Find a link node with a pw_selector in the element_map
    link_index = None
    for node in snap.nodes:
        if node.role == "link" and node.name and str(node.index) in snap.element_map:
            link_index = str(node.index)
            break

    assert link_index is not None, "No clickable link found in element_map"

    # Click using the index — this is the path that was broken before
    result = await click(page, link_index, snap.element_map)
    assert result.success, f"Click failed: {result.error}"
    assert "via index" in result.description

    await browser.close()
    await p.stop()
    return True


async def _test_state_parsing_false():
    """Verify that checked=false and selected=false parse correctly."""
    raw = """- checkbox "Accept terms" (checked=false)
- tab "Settings" (selected=false)
- tab "Profile" (selected)
- option "English" (checked)
"""
    nodes = _parse_aria_snapshot(raw)
    checkbox = next(n for n in nodes if n.role == "checkbox")
    assert checkbox.checked is False, f"Expected False, got {checkbox.checked}"

    tab_off = next(n for n in nodes if n.name == "Settings")
    assert tab_off.selected is False, f"Expected False, got {tab_off.selected}"

    tab_on = next(n for n in nodes if n.name == "Profile")
    assert tab_on.selected is True

    opt = next(n for n in nodes if n.name == "English")
    assert opt.checked is True


def test_state_parsing_false_values():
    asyncio.run(_test_state_parsing_false())


def test_element_map_click_integration():
    asyncio.run(_test_element_map_click())


def test_github_profile_integration():
    asyncio.run(_test_github_profile())


def test_screenshot_capture_integration():
    asyncio.run(_test_screenshot_capture())


# ---- Runner ----

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  [PASS] {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"  {passed} passed, {failed} failed, {passed + failed} total")
    if failed:
        sys.exit(1)
    print("  ALL TESTS PASSED")
