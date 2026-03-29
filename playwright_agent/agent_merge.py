"""Deep merge for save_progress / done — dedupe lists, merge dict rows by stable id."""

from __future__ import annotations

import json
from typing import Any

# First matching key wins for row identity (evidence-style records)
_MERGE_ID_KEYS: tuple[str, ...] = (
    "url", "id", "sample_id", "href", "login", "username", "name", "sha256",
)


def _stable_row_identity(item: Any) -> tuple[str, str] | None:
    if not isinstance(item, dict):
        return None
    for k in _MERGE_ID_KEYS:
        v = item.get(k)
        if v is not None and v != "":
            return (k, str(v))
    return None


def _normalize_for_dedup(obj: Any) -> Any:
    """Round floats and sort dict keys so near-identical rows dedupe consistently."""
    if isinstance(obj, float):
        return round(obj, 9)
    if isinstance(obj, dict):
        return {k: _normalize_for_dedup(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_normalize_for_dedup(x) for x in obj]
    return obj


def _dedupe_key(item: Any) -> str:
    normalized = _normalize_for_dedup(item)
    return json.dumps(normalized, sort_keys=True, default=str, separators=(",", ":"))


def deep_merge(base: dict, update: dict) -> None:
    """Merge ``update`` into ``base`` in place.

    - Nested dicts: recurse.
    - Lists of dicts: if an item shares a stable id (url, id, sample_id, …) with an
      existing row, merge fields into that row instead of appending a duplicate.
    - Other lists: append items whose canonical JSON is not already present.
    - Scalars on key clash: ``update`` wins (replace).
    """
    for key, val in update.items():
        if key in base and isinstance(base[key], list) and isinstance(val, list):
            existing_keys = {_dedupe_key(x) for x in base[key]}
            for item in val:
                if isinstance(item, dict):
                    ident = _stable_row_identity(item)
                    if ident:
                        merged_into = False
                        for i, existing in enumerate(base[key]):
                            if isinstance(existing, dict) and _stable_row_identity(existing) == ident:
                                deep_merge(base[key][i], item)
                                merged_into = True
                                break
                        if merged_into:
                            continue
                dk = _dedupe_key(item)
                if dk not in existing_keys:
                    base[key].append(item)
                    existing_keys.add(dk)
        elif key in base and isinstance(base[key], dict) and isinstance(val, dict):
            deep_merge(base[key], val)
        else:
            base[key] = val
