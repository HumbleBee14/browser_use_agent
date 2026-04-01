"""Memory system tests — MemoryStore, hint injection, _fit_history, episodic failures.

Run: python -m pytest tests/test_memory.py -v
Or:  python tests/test_memory.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory import MemoryStore


# ---------------------------------------------------------------------------
# MemoryStore: basic CRUD
# ---------------------------------------------------------------------------

def _make_store(tmp: Path) -> MemoryStore:
    return MemoryStore(memory_dir=tmp)


def test_empty_store_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        assert store.get_hints("https://example.com") is None


def test_store_and_retrieve_pattern():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        store._patterns["example.com"] = [{
            "task_type": "profile_extraction",
            "action_sequence": ["goto", "extract", "done"],
            "tips": ["tip1"],
            "avoid": ["avoid1"],
            "avg_steps": 5,
            "uses": 0,
        }]
        store._save()

        hints = store.get_hints("https://example.com/page")
        assert hints is not None
        assert "profile_extraction" in hints
        assert "tip1" in hints
        assert "avoid1" in hints


def test_get_hints_is_pure_read():
    """get_hints() must NOT mutate stored data (no write side effects)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        store._patterns["example.com"] = [{
            "task_type": "test_task",
            "action_sequence": ["goto"],
            "tips": [],
            "avoid": [],
            "avg_steps": 3,
            "uses": 0,
        }]
        store._save()

        store.get_hints("https://example.com")
        store.get_hints("https://example.com")
        store.get_hints("https://example.com")

        fresh = MemoryStore._load(store.patterns_file)
        assert fresh["example.com"][0]["uses"] == 0, "get_hints should not increment uses"


def test_record_usage_increments():
    """record_usage() is the only way to bump usage counters."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        store._patterns["example.com"] = [{
            "task_type": "audit",
            "action_sequence": ["goto"],
            "tips": [],
            "avoid": [],
            "avg_steps": 3,
            "uses": 0,
        }]
        store._save()

        store.record_usage("https://example.com")
        store.record_usage("https://example.com")

        fresh = MemoryStore._load(store.patterns_file)
        assert fresh["example.com"][0]["uses"] == 2


def test_run_scoped_memory_cache_isolated_and_resettable():
    """Each run dir gets its own MemoryStore; cache resets cleanly per run."""
    from agent_loop import _get_memory, clear_memory_cache

    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        run1 = Path(tmp1)
        run2 = Path(tmp2)

        m1a = _get_memory(run1)
        m1b = _get_memory(run1)
        m2 = _get_memory(run2)

        assert m1a is m1b
        assert m1a is not m2

        clear_memory_cache(run1)
        m1c = _get_memory(run1)
        assert m1c is not m1a


# ---------------------------------------------------------------------------
# Task-aware retrieval: patterns ranked by goal relevance
# ---------------------------------------------------------------------------

def test_task_aware_ranking():
    """Patterns matching the goal's keywords should appear first."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        store._patterns["github.com"] = [
            {
                "task_type": "commit_audit",
                "action_sequence": ["goto commits", "extract"],
                "tips": ["commit tip"],
                "avoid": [],
                "avg_steps": 10,
                "uses": 0,
            },
            {
                "task_type": "profile_extraction",
                "action_sequence": ["goto profile", "extract"],
                "tips": ["profile tip"],
                "avoid": [],
                "avg_steps": 8,
                "uses": 0,
            },
        ]
        store._save()

        hints = store.get_hints("https://github.com/user", goal="extract user profile data")
        assert hints is not None
        profile_pos = hints.index("profile_extraction")
        commit_pos = hints.index("commit_audit")
        assert profile_pos < commit_pos, "profile pattern should rank above commit for a profile goal"


def test_task_aware_ranking_no_goal_returns_all():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        store._patterns["example.com"] = [
            {"task_type": "a", "action_sequence": [], "tips": [], "avoid": [], "avg_steps": 1, "uses": 0},
            {"task_type": "b", "action_sequence": [], "tips": [], "avoid": [], "avg_steps": 1, "uses": 0},
        ]
        hints = store.get_hints("https://example.com")
        assert "a" in hints and "b" in hints


# ---------------------------------------------------------------------------
# Episodic failure memory
# ---------------------------------------------------------------------------

def test_learn_failures_stores_data():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        progress = {
            "failed_urls": ["https://example.com/404"],
            "blocked_selectors": ["div.broken"],
            "dead_ends": ["click on /old-page"],
            "pages_visited": [],
            "fields_found": [],
            "artifacts": [],
            "exhausted_pages": [],
        }
        result = store.learn_failures("https://example.com/page", progress, "failed", reason="timeout")
        assert result is True

        hints = store.get_hints("https://example.com/page")
        assert hints is not None
        assert "example.com/404" in hints
        assert "div.broken" in hints
        assert "timeout" in hints


def test_learn_failures_skips_done():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        result = store.learn_failures("https://example.com", {}, "done")
        assert result is False


def test_learn_failures_skips_empty_progress():
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        result = store.learn_failures("https://example.com", {
            "failed_urls": [], "blocked_selectors": [], "dead_ends": [],
        }, "failed", reason="")
        assert result is False


# ---------------------------------------------------------------------------
# _fit_history: dynamic token-budget-fitted window
# ---------------------------------------------------------------------------

def test_fit_history_empty():
    from agent_loop import _fit_history
    assert _fit_history([], 0) == []


def test_fit_history_small_history_returns_all():
    from agent_loop import _fit_history, MIN_HISTORY_ITEMS
    history = [{"step": i, "action": "click", "result": "ok"} for i in range(3)]
    result = _fit_history(history, 0)
    assert len(result) == min(len(history), MIN_HISTORY_ITEMS)


def test_fit_history_respects_budget():
    from agent_loop import _fit_history, MIN_HISTORY_ITEMS
    history = [
        {"step": i, "action": "scroll", "result": "scrolled " * 50}
        for i in range(50)
    ]
    result = _fit_history(history, fixed_tokens=0)
    assert len(result) >= MIN_HISTORY_ITEMS
    assert len(result) < 50, "Should not include all 50 items"


def test_fit_history_importance_scoring():
    """High-importance actions (save_progress) should survive over low-importance (scroll)."""
    from agent_loop import _fit_history, MIN_HISTORY_ITEMS
    history = [
        {"step": 1, "action": "save_progress", "result": "saved data"},
        *[{"step": i, "action": "scroll", "result": "scrolled"} for i in range(2, 20)],
        *[{"step": i, "action": "click", "result": "clicked"} for i in range(20, 25)],
    ]
    result = _fit_history(history, fixed_tokens=0)
    actions = [h["action"] for h in result]
    assert "save_progress" in actions, "save_progress should survive importance scoring"


def test_fit_history_shrinks_with_large_fixed_tokens():
    """When fixed_tokens is high, history budget should shrink."""
    from agent_loop import _fit_history, PROMPT_TOKEN_BUDGET
    history = [{"step": i, "action": "click", "result": "ok " * 20} for i in range(30)]
    big_result = _fit_history(history, fixed_tokens=0)
    small_result = _fit_history(history, fixed_tokens=int(PROMPT_TOKEN_BUDGET * 0.8))
    assert len(small_result) <= len(big_result), "Large fixed_tokens should shrink history"


def test_fit_history_preserves_recent_meta_order():
    """Recent meta messages should stay in chronological order inside recency context."""
    from agent_prompt import fit_history

    history = [
        {"step": 1, "action": "goto", "result": "page 1"},
        {"step": 2, "action": "click", "result": "clicked"},
        {"step": 3, "action": "extract", "result": "Extracted 10 chars"},
        {"step": 4, "action": "save_progress", "result": "saved"},
        {"step": 4, "action": "system_notice", "is_meta": True, "result": "wrap up soon"},
        {"step": 5, "action": "goto", "result": "page 2"},
    ]

    result = fit_history(history, fixed_tokens=0)
    recent = [(item["step"], item["action"]) for item in result[-5:]]
    assert recent == [
        (2, "click"),
        (3, "extract"),
        (4, "save_progress"),
        (4, "system_notice"),
        (5, "goto"),
    ]


# ---------------------------------------------------------------------------
# Structured summary format
# ---------------------------------------------------------------------------

def test_structured_summary_prompt_format():
    """Verify the summary prompt asks for FOUND/GAPS/NEXT format."""
    from agent_loop import _summarize_steps
    import inspect
    source = inspect.getsource(_summarize_steps)
    assert "FOUND:" in source
    assert "GAPS:" in source
    assert "NEXT:" in source


def test_summarize_steps_uses_non_meta_range():
    """Summary headers should reflect the actual non-meta steps being summarized."""
    from agent_prompt import summarize_steps

    class _FakeMessages:
        async def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(text="FOUND: x\nGAPS: y\nNEXT: z")])

    class _FakeClient:
        messages = _FakeMessages()

    class _FakeLog:
        def debug(self, *args, **kwargs):
            pass

    steps = [
        {"step": 1, "action": "system_notice", "is_meta": True, "result": "notice"},
        {"step": 2, "action": "goto", "result": "page"},
        {"step": 3, "action": "extract", "result": "Extracted 10 chars"},
        {"step": 4, "action": "system_notice", "is_meta": True, "result": "another notice"},
    ]

    result = asyncio.run(summarize_steps(_FakeClient(), steps, "goal", _FakeLog()))
    assert result.startswith("Steps 2-3:\n")


def test_fixed_prompt_token_estimate_matches_rendered_prompt():
    """Fixed-token estimation should match the exact non-history prompt we send."""
    from agent_prompt import (
        build_messages,
        build_system_blocks,
        estimate_fixed_prompt_tokens,
        estimate_tokens,
    )

    sample = SimpleNamespace(
        sample_id="sample-1",
        url="https://example.com",
        extra={"ticket": "ABC-123"},
    )
    task_spec = SimpleNamespace(
        max_steps=40,
        goal="Collect the page data.",
        output_schema={"name": "string"},
        required_fields=["name"],
        judgment_required=True,
        judgment_question="Is this complete?",
        judgment_output_schema={"answer": "string"},
        system_prompt="You are a browser agent.",
    )
    snap = SimpleNamespace(nodes=[])
    progress = {
        "pages_visited": [f"url-{i}" for i in range(20)],
        "artifacts": [f"shot-{i}" for i in range(20)],
        "fields_found": [f"field-{i}" for i in range(8)],
        "failed_urls": [f"bad-{i}" for i in range(8)],
        "blocked_selectors": [f"selector-{i}" for i in range(8)],
        "dead_ends": [f"dead-{i}" for i in range(5)],
        "exhausted_pages": [f"done-{i}" for i in range(8)],
    }
    accumulated = {"blob": "x" * 5000}
    step_summaries = ["sum-1", "sum-2", "sum-3", "sum-4"]
    step_tools = [{"name": "done", "description": "Finish the task"}]

    estimated = estimate_fixed_prompt_tokens(
        page_state="URL: https://example.com\n[0] heading",
        vision_text="",
        task_spec=task_spec,
        sample=sample,
        snap=snap,
        consecutive_failures=0,
        progress=progress,
        accumulated=accumulated,
        step_summaries=step_summaries,
        current_step=4,
        memory_hints="Use the sidebar first.",
        effective_max=40,
        step_tools=step_tools,
    )

    messages = build_messages(
        page_state="URL: https://example.com\n[0] heading",
        vision_text="",
        history=[],
        task_spec=task_spec,
        sample=sample,
        snap=snap,
        consecutive_failures=0,
        loop_counter={},
        progress=progress,
        accumulated=accumulated,
        step_summaries=step_summaries,
        current_step=4,
        memory_hints="Use the sidebar first.",
        effective_max=40,
    )
    system_blocks = build_system_blocks(
        task_spec.system_prompt,
        sample,
        "Use the sidebar first.",
        4,
    )
    expected = estimate_tokens(
        "\n".join(block["text"] for block in system_blocks)
        + messages[0]["content"]
        + json.dumps(step_tools, default=str)
    )

    assert estimated == expected


# ---------------------------------------------------------------------------
# Run as standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
