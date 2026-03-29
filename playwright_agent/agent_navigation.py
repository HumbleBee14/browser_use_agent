"""Pagination detection, batch safety, and multi-action batch rules."""

from __future__ import annotations

BATCH_BREAKING_ACTIONS = frozenset({"goto", "done", "fail", "save_progress"})

PAGINATION_KEYWORDS = frozenset({
    "next", "next page", "load more", "show more", "older", "newer",
    "page 2", "page 3", "page 4", "page 5", "»", "›", "→",
    "previous", "prev", "back", "forward",
})


def is_pagination_click(selector: str, result_desc: str) -> bool:
    """Detect if a click was a pagination action (next page, load more, etc.)."""
    combined = f"{selector} {result_desc}".lower()
    return any(kw in combined for kw in PAGINATION_KEYWORDS)


def is_dom_stable(before_count: int, after_count: int, tolerance: float = 0.20) -> bool:
    """True when interactive element counts did not shift enough to invalidate batched indices."""
    if before_count == 0:
        return after_count == 0
    diff = abs(after_count - before_count)
    threshold = max(3, int(before_count * tolerance))
    return diff <= threshold


def is_batch_target_stable(
    selector: str,
    original_map: dict[str, str],
    current_map: dict[str, str],
) -> bool:
    """True when an index selector still maps to the same target string."""
    if not selector or not selector.isdigit():
        return True

    original_target = original_map.get(selector)
    current_target = current_map.get(selector)
    return bool(original_target) and original_target == current_target
