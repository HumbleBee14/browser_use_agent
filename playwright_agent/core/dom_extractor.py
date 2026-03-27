"""DOM Extractor — converts Playwright a11y tree into compact LLM-ready text.

Primary: Playwright's aria_snapshot() (fast, native).
Fallback: CDP Accessibility.getFullAXTree when aria_snapshot is empty/sparse (<5 nodes).

Four-pass pruning pipeline:
  Pass 1: parse aria_snapshot into structured nodes
  Pass 2: keep semantic roles, drop navigation/chrome
  Pass 3: task-aware keyword scoring
  Pass 4: trim to budget

Outputs indexed text like:
  [0] [heading]  "Linus Torvalds"
  [1] [link]     "linux" → https://github.com/torvalds/linux
  [2] [button]   "Follow"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from playwright.async_api import Page


# Roles we care about
SEMANTIC_ROLES = frozenset({
    "button", "link", "textbox", "checkbox", "radio", "tab", "menuitem",
    "heading", "table", "row", "cell", "listitem", "combobox", "option",
    "status", "alert", "img", "switch", "slider", "spinbutton", "searchbox",
    "treeitem", "menuitemcheckbox", "menuitemradio", "text", "paragraph",
    "region", "article", "main",
})

INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "checkbox", "radio", "tab", "menuitem",
    "combobox", "option", "switch", "slider", "spinbutton", "searchbox",
    "treeitem", "menuitemcheckbox", "menuitemradio",
})

# Roles to skip entirely (chrome/boilerplate)
SKIP_ROLES = frozenset({"banner", "navigation", "contentinfo"})

MAX_NODES = 40
ZERO_SCORE_BUDGET = 20


@dataclass
class DOMNode:
    """One node from the pruned a11y tree."""
    index: int = 0
    role: str = ""
    name: str = ""
    url: str = ""
    level: int | None = None
    selected: bool | None = None
    checked: bool | None = None
    expanded: bool | None = None
    value: str = ""
    pw_selector: str = ""


@dataclass
class PageMetrics:
    """Metrics for dom_confidence scoring."""
    total_nodes: int = 0
    semantic_nodes: int = 0
    interactive_nodes: int = 0
    canvas_count: int = 0
    svg_count: int = 0
    missing_aria_labels: int = 0


@dataclass
class DOMSnapshot:
    """Complete result of DOM extraction."""
    nodes: list[DOMNode] = field(default_factory=list)
    element_map: dict[str, str] = field(default_factory=dict)
    metrics: PageMetrics = field(default_factory=PageMetrics)
    confidence: float = 1.0
    url: str = ""
    title: str = ""
    raw_text: str = ""  # raw aria_snapshot for debugging


async def snapshot(page: Page, keywords: list[str] | None = None) -> DOMSnapshot:
    """Take a DOM snapshot and prune it for LLM consumption."""
    keywords = keywords or []
    url = page.url
    title = await page.title()

    # Primary: aria_snapshot (fast, native Playwright API)
    raw = ""
    try:
        raw = await page.locator("body").aria_snapshot()
    except Exception:
        pass

    all_nodes = _parse_aria_snapshot(raw) if raw else []

    # Fallback: CDP Accessibility.getFullAXTree when aria_snapshot is empty/sparse
    if len(all_nodes) < 5:
        try:
            cdp = await page.context.new_cdp_session(page)
            tree = await cdp.send("Accessibility.getFullAXTree")
            cdp_nodes = tree.get("nodes", [])
            all_nodes = _parse_cdp_ax_tree(cdp_nodes)
            raw = f"[CDP fallback: {len(cdp_nodes)} raw nodes]"
            await cdp.detach()
        except Exception:
            pass

    if not all_nodes:
        return DOMSnapshot(url=url, title=title, confidence=0.1, raw_text=raw)

    # Get page metrics for confidence
    metrics = await _get_page_metrics(page, all_nodes)

    # Pass 2: keep semantic roles, skip nav/banner/footer
    nodes = _filter_semantic(all_nodes)

    # Pass 3: keyword scoring
    nodes = _keyword_score(nodes, keywords)

    # Pass 4: trim to budget
    nodes = nodes[:MAX_NODES]

    # Assign indices and build element_map
    dom_nodes = []
    element_map = {}
    for i, n in enumerate(nodes):
        n.index = i
        dom_nodes.append(n)
        if n.pw_selector:
            element_map[str(i)] = n.pw_selector

    confidence = _compute_confidence(metrics)

    return DOMSnapshot(
        nodes=dom_nodes,
        element_map=element_map,
        metrics=metrics,
        confidence=confidence,
        url=url,
        title=title,
        raw_text=raw,
    )


def serialize(snap: DOMSnapshot) -> str:
    """Convert DOMSnapshot to token-efficient text for the LLM prompt."""
    lines = [f"URL: {snap.url}", f"Title: {snap.title}", ""]

    for node in snap.nodes:
        parts = [f"[{node.index}]", f"[{node.role}]"]

        if node.name:
            parts.append(f'"{node.name}"')
        if node.url:
            parts.append(f"→ {node.url}")
        if node.value:
            parts.append(f'(value="{node.value}")')

        attrs = []
        if node.selected is not None:
            attrs.append(f"selected={node.selected}")
        if node.checked is not None:
            attrs.append(f"checked={node.checked}")
        if node.expanded is not None:
            attrs.append(f"expanded={node.expanded}")
        if node.level is not None:
            attrs.append(f"h{node.level}")
        if attrs:
            parts.append(f"({', '.join(attrs)})")

        lines.append("  ".join(parts))

    return "\n".join(lines)


# ---- Parsing ----

def _parse_aria_snapshot(raw: str) -> list[DOMNode]:
    """Parse Playwright's aria_snapshot YAML-like format into DOMNode list.

    Input format:
      - heading "Page Title" [level=2]
      - link "About":
        - /url: https://example.com
      - button "Sign in"
      - textbox "Search" (value="hello")
    """
    nodes = []
    lines = raw.split("\n")
    i = 0
    in_skip_block = False
    skip_indent = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Calculate indent level
        indent = len(line) - len(line.lstrip())

        # Skip blocks under navigation/banner roles
        if in_skip_block and indent > skip_indent:
            i += 1
            continue
        else:
            in_skip_block = False

        # Parse "- role "name" [attrs]:" pattern
        match = re.match(r'^-\s+(\w+)(?:\s+"([^"]*)")?(.*)$', stripped)
        if match:
            role = match.group(1)
            name = match.group(2) or ""
            rest = match.group(3) or ""

            # Skip navigation/banner blocks
            if role in SKIP_ROLES:
                in_skip_block = True
                skip_indent = indent
                i += 1
                continue

            node = DOMNode(role=role, name=name)

            # Parse attributes like [level=2]
            level_match = re.search(r'\[level=(\d+)\]', rest)
            if level_match:
                node.level = int(level_match.group(1))

            # Parse state — must handle =false correctly
            if "checked=false" in rest:
                node.checked = False
            elif "checked" in rest:
                node.checked = True
            if "selected=false" in rest:
                node.selected = False
            elif "selected" in rest:
                node.selected = True
            if "expanded=false" in rest:
                node.expanded = False
            elif "expanded" in rest:
                node.expanded = True

            # Build Playwright selector — stored as "role:name" for resolution
            if role in INTERACTIVE_ROLES and name:
                node.pw_selector = f'{role}:{name}'

            # Look ahead for /url on next line
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                url_match = re.match(r'^-\s+/url:\s+(.+)$', next_line)
                if url_match:
                    node.url = url_match.group(1).strip()
                    i += 1  # skip the URL line

            nodes.append(node)

        i += 1

    return nodes


def _parse_cdp_ax_tree(cdp_nodes: list[dict]) -> list[DOMNode]:
    """Parse CDP Accessibility.getFullAXTree response into DOMNode list.

    Used as fallback when aria_snapshot() returns empty/sparse results.
    CDP nodes have: role.value, name.value, properties[], etc.
    """
    nodes = []
    for n in cdp_nodes:
        role = n.get("role", {}).get("value", "")
        name = n.get("name", {}).get("value", "")
        if not role or role in ("none", "generic", "InlineTextBox", "RootWebArea"):
            continue
        if not name and role not in INTERACTIVE_ROLES:
            continue

        # Map CDP role names to standard ARIA roles
        role_map = {
            "StaticText": "text",
            "InternalLink": "link",
            "GenericContainer": "region",
        }
        role = role_map.get(role, role).lower()

        node = DOMNode(role=role, name=name)

        # Extract properties
        for prop in n.get("properties", []):
            pname = prop.get("name", "")
            pval = prop.get("value", {}).get("value")
            if pname == "level" and pval:
                node.level = int(pval)
            elif pname == "checked":
                node.checked = pval == "true"
            elif pname == "selected":
                node.selected = pval == "true"
            elif pname == "expanded":
                node.expanded = pval == "true"
            elif pname == "url" and pval:
                node.url = str(pval)

        if role in INTERACTIVE_ROLES and name:
            node.pw_selector = f"{role}:{name}"

        nodes.append(node)
    return nodes


def _filter_semantic(nodes: list[DOMNode]) -> list[DOMNode]:
    """Keep only semantic roles, drop generic/structural nodes."""
    return [n for n in nodes if n.role in SEMANTIC_ROLES and (n.name or n.role in INTERACTIVE_ROLES)]


def _keyword_score(nodes: list[DOMNode], keywords: list[str]) -> list[DOMNode]:
    """Score by keyword relevance. Keep boosted + zero-score budget."""
    if not keywords:
        return nodes

    kw_lower = [k.lower() for k in keywords]
    boosted = []
    rest = []

    for n in nodes:
        text = f"{n.name} {n.value} {n.url}".lower()
        score = sum(1 for k in kw_lower if k in text)
        if score > 0:
            boosted.append(n)
        else:
            rest.append(n)

    return boosted + rest[:ZERO_SCORE_BUDGET]


async def _get_page_metrics(page: Page, nodes: list[DOMNode]) -> PageMetrics:
    """Compute page metrics for confidence scoring."""
    semantic = sum(1 for n in nodes if n.role in SEMANTIC_ROLES)
    interactive = sum(1 for n in nodes if n.role in INTERACTIVE_ROLES)

    try:
        counts = await page.evaluate("""() => {
            return {
                canvas: document.querySelectorAll('canvas').length,
                svg: document.querySelectorAll('svg').length,
                missingLabels: [...document.querySelectorAll(
                    'button, a[href], input, select, textarea'
                )].filter(el => !el.getAttribute('aria-label') && !el.textContent.trim()).length
            };
        }""")
    except Exception:
        counts = {"canvas": 0, "svg": 0, "missingLabels": 0}

    return PageMetrics(
        total_nodes=len(nodes),
        semantic_nodes=semantic,
        interactive_nodes=max(interactive, 1),
        canvas_count=counts.get("canvas", 0),
        svg_count=counts.get("svg", 0),
        missing_aria_labels=counts.get("missingLabels", 0),
    )


def _compute_confidence(metrics: PageMetrics) -> float:
    """DOM confidence score. Below 0.6 → vision activates."""
    if metrics.total_nodes == 0:
        return 0.1

    total = max(metrics.total_nodes, 1)
    interactive = max(metrics.interactive_nodes, 1)

    score = 1.0
    score -= 0.3 * (metrics.canvas_count / total)
    score -= 0.2 * (metrics.missing_aria_labels / interactive)
    score -= 0.1 * (metrics.svg_count / interactive)

    if metrics.semantic_nodes < 10:
        score -= 0.3

    return max(0.0, min(1.0, score))
