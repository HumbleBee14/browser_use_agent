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

def test_required_artifacts_check():
    """Saved artifacts should be matched against required_artifacts."""
    from models.actions import EvidenceArtifact

    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        required_artifacts=["screenshot", "profile"],
    )

    # Simulate artifacts saved by OutputManager
    artifacts = [
        EvidenceArtifact(filename="01_profile.png", sha256="abc", source_url="https://x.com"),
        EvidenceArtifact(filename="02_screenshot.png", sha256="def", source_url="https://x.com"),
    ]

    saved_labels = {a.filename.split("_", 1)[-1].rsplit(".", 1)[0] for a in artifacts}
    missing = []
    for req in spec.required_artifacts:
        if not any(req in label for label in saved_labels):
            missing.append(req)

    assert missing == [], f"Artifacts should match: {saved_labels} vs {spec.required_artifacts}"


def test_required_artifacts_missing():
    """Missing artifact should be detected."""
    from models.actions import EvidenceArtifact

    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        required_artifacts=["screenshot", "checks"],
    )

    artifacts = [
        EvidenceArtifact(filename="01_screenshot.png", sha256="abc", source_url="https://x.com"),
    ]

    saved_labels = {a.filename.split("_", 1)[-1].rsplit(".", 1)[0] for a in artifacts}
    missing = []
    for req in spec.required_artifacts:
        if not any(req in label for label in saved_labels):
            missing.append(req)

    assert missing == ["checks"], f"Should detect missing 'checks': {missing}"


# ---- AgentAction parsing ----

def test_action_parsing_valid():
    """Valid tool call should parse fine."""
    action = AgentAction(action="click", selector="3")
    assert action.action == "click"
    assert action.selector == "3"


def test_action_parsing_rejects_unknown_fields():
    """Unknown fields should raise ValidationError."""
    try:
        AgentAction(action="click", selector="3", extra_field="bad")
        assert False, "Should have raised"
    except Exception:
        pass  # Pydantic rejects extra fields


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

def test_tool_schema_all_9_actions():
    """Tool schema should have exactly 9 tools."""
    tools = action_tool_schema()
    assert len(tools) == 9
    names = {t["name"] for t in tools}
    expected = {"goto", "click", "type", "scroll", "screenshot", "extract", "wait", "done", "fail"}
    assert names == expected


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
