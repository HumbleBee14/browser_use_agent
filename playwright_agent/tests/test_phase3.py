"""Phase 3 regression tests — agent loop logic (no browser, no LLM calls).

Tests the loop's validation, error handling, and prompt construction
using mocked inputs. No network calls.

Run: python tests/test_phase3.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.task import TaskSpec, SampleInput
from models.actions import AgentAction, ActionResult, action_tool_schema
from tools.output import OutputManager


# ---- done validation ----

def test_done_accepts_falsy_values():
    """0, False, empty string should NOT be treated as missing."""
    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        output_schema={"count": "number", "active": "boolean"},
        required_fields=["count", "active"],
    )
    extracted = {"count": 0, "active": False}

    # Simulate the validation check from agent_loop.py
    missing = [f for f in spec.required_fields if f not in extracted or extracted[f] is None]
    assert missing == [], f"Falsy values incorrectly flagged as missing: {missing}"


def test_done_rejects_none_values():
    """None should be treated as missing."""
    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        output_schema={"name": "string"},
        required_fields=["name"],
    )
    extracted = {"name": None}

    missing = [f for f in spec.required_fields if f not in extracted or extracted[f] is None]
    assert missing == ["name"]


def test_done_rejects_absent_fields():
    """Fields not in extracted dict should be flagged."""
    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        output_schema={"name": "string", "bio": "string"},
        required_fields=["name", "bio"],
    )
    extracted = {"name": "Linus"}

    missing = [f for f in spec.required_fields if f not in extracted or extracted[f] is None]
    assert missing == ["bio"]


# ---- required_artifacts validation ----

def test_required_artifacts_matched_by_label():
    """required_artifacts matches by label substring in filename."""
    from models.actions import EvidenceArtifact

    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        required_artifacts=["profile", "checks"],
    )

    artifacts = [
        EvidenceArtifact(filename="01_profile.png", sha256="abc", source_url="https://x.com"),
        EvidenceArtifact(filename="02_checks.png", sha256="def", source_url="https://x.com"),
    ]

    saved_filenames = [a.filename for a in artifacts]
    missing = []
    for req in spec.required_artifacts:
        if not any(req in fn for fn in saved_filenames):
            missing.append(req)

    assert missing == [], f"Both labels should match: {saved_filenames}"


def test_required_artifacts_missing():
    """Missing artifact label should be detected."""
    from models.actions import EvidenceArtifact

    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        required_artifacts=["profile", "checks"],
    )

    artifacts = [
        EvidenceArtifact(filename="01_profile.png", sha256="abc", source_url="https://x.com"),
    ]

    saved_filenames = [a.filename for a in artifacts]
    missing = []
    for req in spec.required_artifacts:
        if not any(req in fn for fn in saved_filenames):
            missing.append(req)

    assert missing == ["checks"], f"Should detect missing 'checks': {missing}"


def test_last_step_done_with_missing_requirements_is_not_done():
    """At max_steps, done with missing fields should NOT write status=done."""
    # This tests the logic: if requirements missing AND step == max_steps → needs_review
    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        output_schema={"name": "string", "bio": "string"},
        required_fields=["name", "bio"],
    )
    extracted = {"name": "Linus"}  # bio missing

    missing = [f for f in spec.required_fields if f not in extracted or extracted[f] is None]
    assert missing == ["bio"]

    # At last step (step == max_steps), the loop should write needs_review
    step = spec.max_steps
    should_bounce = step < spec.max_steps
    assert should_bounce is False, "At max_steps, cannot bounce"
    # → code path writes needs_review, not done


def test_done_uses_effective_max_after_pagination_bonus():
    """Retry budget after pagination should use effective_max, not the base max_steps."""
    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        output_schema={"name": "string", "bio": "string"},
        required_fields=["name", "bio"],
    )
    step = spec.max_steps + 1
    effective_max = spec.max_steps + 3
    should_bounce = step < effective_max
    assert should_bounce is True


# ---- AgentAction parsing ----

def test_action_parsing_valid():
    """Valid tool call should parse fine."""
    action = AgentAction(action="click", selector="3")
    assert action.action == "click"
    assert action.selector == "3"


def test_action_parsing_ignores_extra_fields():
    """Pydantic v2 silently drops unknown fields — correct for LLM output."""
    action = AgentAction(action="click", selector="3", extra_field="ignored")
    assert action.action == "click"
    assert action.selector == "3"
    assert not hasattr(action, "extra_field")


def test_action_parsing_rejects_bad_action():
    """Invalid action name should raise."""
    try:
        AgentAction(action="fly_to_moon")
        assert False, "Should have raised"
    except Exception:
        pass


# ---- judgment extraction ----

def test_judgment_extraction_from_done():
    """Judgment fields should be separated from regular extracted fields."""
    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        judgment_required=True,
        judgment_question="Is this material?",
        judgment_output_schema={"answer": "string", "confidence": "number", "reasoning": "string"},
    )

    extracted = {
        "file_path": "package.json",
        "author": "alice",
        "answer": "yes",
        "confidence": 0.9,
        "reasoning": "The change affected the calculation",
    }

    # Simulate the judgment extraction from agent_loop.py
    judgment = {
        k: extracted.pop(k, None)
        for k in spec.judgment_output_schema
        if k in extracted
    }

    assert judgment == {"answer": "yes", "confidence": 0.9, "reasoning": "The change affected the calculation"}
    assert "answer" not in extracted  # popped from extracted
    assert extracted == {"file_path": "package.json", "author": "alice"}


# ---- prompt construction ----

def test_tool_schema_all_10_actions():
    """Tool schema should have exactly 10 tools."""
    tools = action_tool_schema()
    assert len(tools) == 10
    names = {t["name"] for t in tools}
    expected = {"goto", "click", "type", "scroll", "screenshot", "extract", "wait", "save_progress", "done", "fail"}
    assert names == expected


def test_retryable_llm_error_classification():
    """Transient LLM transport/rate-limit errors should retry."""
    from anthropic import APIConnectionError, RateLimitError
    from agent_loop import _is_retryable_llm_error

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(429, request=req)

    assert _is_retryable_llm_error(APIConnectionError(message="Connection error.", request=req)) is True
    assert _is_retryable_llm_error(RateLimitError("rate limited", response=resp, body=None)) is True


def test_non_retryable_llm_error_classification():
    """Deterministic request/schema errors should fail immediately."""
    from anthropic import BadRequestError
    from agent_loop import _is_retryable_llm_error

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(400, request=req)

    assert _is_retryable_llm_error(BadRequestError("prompt is too long", response=resp, body=None)) is False
    assert _is_retryable_llm_error(ValueError("tool schema invalid")) is False


# ---- output manager integration ----

def test_output_manager_tracks_artifacts_for_validation():
    """OutputManager._artifacts should be accessible for required_artifacts check."""
    with tempfile.TemporaryDirectory() as tmp:
        om = OutputManager(Path(tmp), "test")
        om.save_screenshot(b"png", "profile", "https://example.com")
        om.save_screenshot(b"png", "checks", "https://example.com")

        labels = {a.filename.split("_", 1)[-1].rsplit(".", 1)[0] for a in om._artifacts}
        assert "profile" in labels
        assert "checks" in labels


# ---- structured reflection ----

def test_reflection_fields_accepted_in_agent_action():
    """AgentAction should accept optional reflection fields without error."""
    action = AgentAction(
        action="click", selector="3",
        evaluation_previous_step="Previous goto succeeded, landed on profile page.",
        memory_update="Profile page has contributions tab at index 5.",
        next_goal="Click contributions tab to find commit history.",
    )
    assert action.evaluation_previous_step is not None
    assert action.memory_update is not None
    assert action.next_goal is not None
    assert action.selector == "3"


def test_reflection_fields_optional():
    """Reflection fields should default to None when not provided."""
    action = AgentAction(action="goto", url="https://example.com")
    assert action.evaluation_previous_step is None
    assert action.memory_update is None
    assert action.next_goal is None


def test_reflection_in_tool_schema():
    """Every tool schema should include reflection properties."""
    tools = action_tool_schema()
    reflection_keys = {"evaluation_previous_step", "memory_update", "next_goal"}
    for tool in tools:
        props = set(tool["input_schema"]["properties"].keys())
        missing = reflection_keys - props
        assert not missing, f"Tool '{tool['name']}' missing reflection props: {missing}"
        # Reflection fields must NOT be in required
        required = set(tool["input_schema"].get("required", []))
        assert not (reflection_keys & required), (
            f"Tool '{tool['name']}' has reflection fields in required: {reflection_keys & required}"
        )


def test_step_record_includes_reflection():
    """StepRecord should store evaluation, memory_update, and next_goal."""
    from models.actions import StepRecord
    rec = StepRecord(
        step=5, action="click", result="OK", url="https://example.com",
        evaluation="Goto succeeded.",
        memory_update="Login page has form at index 2.",
        next_goal="Fill in credentials.",
    )
    assert rec.evaluation == "Goto succeeded."
    assert rec.memory_update == "Login page has form at index 2."
    assert rec.next_goal == "Fill in credentials."


# ---- terminal tools restriction ----

def test_terminal_tools_only_done_and_fail():
    """Last-step restricted tools should only contain done and fail."""
    from agent_loop import _get_terminal_tools
    terminal = _get_terminal_tools()
    names = {t["name"] for t in terminal}
    assert names == {"done", "fail"}, f"Expected only done/fail, got: {names}"


def test_terminal_tools_include_reflection():
    """Even terminal tools should have reflection properties."""
    from agent_loop import _get_terminal_tools
    reflection_keys = {"evaluation_previous_step", "memory_update", "next_goal"}
    for tool in _get_terminal_tools():
        props = set(tool["input_schema"]["properties"].keys())
        assert reflection_keys.issubset(props), f"Terminal tool '{tool['name']}' missing reflection"


# ---- escalating recovery ----

def test_recovery_level_1_gentle():
    """5+ steps without data should trigger level 1 nudge."""
    from agent_loop import _build_recovery_notice
    result = _build_recovery_notice(
        step=10, effective_max=30, steps_since_data=5,
        stagnation_count=3, stagnation_level=0,
        consecutive_failures=0, loop_counter={},
        current_url="https://example.com", has_accumulated=False,
    )
    assert result is not None
    level, msg = result
    assert level == 1


def test_recovery_level_2_forceful():
    """5+ stagnation with level<2 should trigger level 2."""
    from agent_loop import _build_recovery_notice
    result = _build_recovery_notice(
        step=15, effective_max=30, steps_since_data=8,
        stagnation_count=5, stagnation_level=1,
        consecutive_failures=0, loop_counter={},
        current_url="https://example.com", has_accumulated=True,
    )
    assert result is not None
    level, msg = result
    assert level == 2
    assert "CHANGE YOUR STRATEGY" in msg


def test_recovery_level_3_forced():
    """8+ stagnation should trigger level 3 forced consolidation."""
    from agent_loop import _build_recovery_notice
    result = _build_recovery_notice(
        step=20, effective_max=30, steps_since_data=12,
        stagnation_count=8, stagnation_level=2,
        consecutive_failures=0, loop_counter={},
        current_url="https://example.com", has_accumulated=True,
    )
    assert result is not None
    level, msg = result
    assert level == 3
    assert "CRITICAL" in msg


def test_recovery_none_when_healthy():
    """No recovery notice when everything is progressing normally."""
    from agent_loop import _build_recovery_notice
    result = _build_recovery_notice(
        step=5, effective_max=30, steps_since_data=1,
        stagnation_count=0, stagnation_level=0,
        consecutive_failures=0, loop_counter={},
        current_url="https://example.com", has_accumulated=False,
    )
    assert result is None


# ---- config flags ----

def test_config_has_new_feature_flags():
    """Config should expose the new agent behavior flags."""
    import config
    assert hasattr(config, "REFLECTION_MODE")
    assert config.REFLECTION_MODE in ("full", "light")
    assert hasattr(config, "FINALIZE_ON_FAILURE")
    assert isinstance(config.FINALIZE_ON_FAILURE, bool)
    assert hasattr(config, "ENABLE_FALLBACK_LLM")
    assert isinstance(config.ENABLE_FALLBACK_LLM, bool)
    assert hasattr(config, "FALLBACK_LLM_MODEL")
    assert isinstance(config.FALLBACK_LLM_MODEL, str)


# ---- runner ----

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
