"""Phase 1 regression tests — models, tools, config.

Run: python -m pytest tests/test_phase1.py -v
Or:  python tests/test_phase1.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Ensure playwright_agent is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.task import TaskSpec, SampleInput, load_task_spec
from models.actions import (
    AgentAction, ActionResult, StepRecord, EvidenceArtifact,
    SampleResult, action_tool_schema,
)
from tools.output import OutputManager, merge_results_to_csv


# ---------- TaskSpec ----------

def test_task_spec_loads_from_template():
    spec = load_task_spec(Path(__file__).parent.parent / "tasks" / "_template.json")
    assert spec.task_id == "unique_name"
    assert spec.phase == "execution"
    assert spec.max_steps == 25


def test_task_spec_planner_discovery_private_attrs():
    """task_planner sets _discovery_url / _needs_discovery — must not crash (Pydantic v2)."""
    spec = TaskSpec(task_id="t", phase="execution", system_prompt="s", goal="g")
    spec._discovery_url = "https://example.com/items"
    spec._needs_discovery = True
    assert spec._discovery_url == "https://example.com/items"
    assert spec._needs_discovery is True


def test_task_spec_rejects_bad_required_fields():
    """required_fields must reference fields in output_schema."""
    try:
        TaskSpec(
            task_id="bad", phase="execution", system_prompt="x", goal="x",
            output_schema={"name": "string"},
            required_fields=["missing_field"],
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "missing_field" in str(e)


def test_task_spec_rejects_judgment_without_question():
    """judgment_required=true needs judgment_question and judgment_output_schema."""
    try:
        TaskSpec(
            task_id="bad", phase="execution", system_prompt="x", goal="x",
            judgment_required=True,
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "judgment_question" in str(e)


def test_task_spec_rejects_judgment_without_schema():
    try:
        TaskSpec(
            task_id="bad", phase="execution", system_prompt="x", goal="x",
            judgment_required=True,
            judgment_question="Is this material?",
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "judgment_output_schema" in str(e)


def test_task_spec_valid_judgment():
    """Valid judgment spec should pass."""
    spec = TaskSpec(
        task_id="ok", phase="execution", system_prompt="x", goal="x",
        judgment_required=True,
        judgment_question="Is this material?",
        judgment_output_schema={"answer": "yes | no", "confidence": "0.0-1.0"},
    )
    assert spec.judgment_required is True


def test_task_spec_file_not_found():
    try:
        load_task_spec("nonexistent.json")
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass


# ---------- SampleInput ----------

def test_sample_input_from_csv_row_with_url():
    row = {"sample_id": "s1", "url": "https://github.com/torvalds", "name": "Linus"}
    s = SampleInput.from_csv_row(row)
    assert s.sample_id == "s1"
    assert s.url == "https://github.com/torvalds"
    assert s.extra == {"name": "Linus"}


def test_sample_input_from_csv_row_no_url():
    """LinkedIn-style input: name only, no URL."""
    row = {"sample_id": "p1", "name": "Satya Nadella", "company": "Microsoft"}
    s = SampleInput.from_csv_row(row)
    assert s.sample_id == "p1"
    assert s.url == ""
    assert s.extra["name"] == "Satya Nadella"


# ---------- AgentAction ----------

def test_agent_action_all_12_types():
    valid_actions = [
        "goto", "click", "type", "scroll", "screenshot", "extract",
        "wait", "download", "select_option", "save_progress", "done", "fail",
    ]
    for action_name in valid_actions:
        a = AgentAction(action=action_name)
        assert a.action == action_name


def test_agent_action_rejects_invalid_type():
    try:
        AgentAction(action="invalid_action")
        assert False, "Should have rejected invalid action"
    except Exception:
        pass


# ---------- Tool Schema ----------

def test_tool_schema_has_12_tools():
    tools = action_tool_schema()
    assert len(tools) == 12
    names = [t["name"] for t in tools]
    assert "goto" in names
    assert "save_progress" in names
    assert "done" in names
    assert "fail" in names


def test_action_result_supports_download_metadata():
    result = ActionResult(
        success=True,
        description="Downloaded: report.csv (123 bytes)",
        download_path="/tmp/playwright/download-123.tmp",
        download_name="report.csv",
    )
    assert result.download_path.endswith(".tmp")
    assert result.download_name == "report.csv"


def test_tool_schema_structure():
    """Each tool must have name, description, input_schema with required fields."""
    tools = action_tool_schema()
    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"


def test_tool_schema_light_omits_reflection_properties():
    full = action_tool_schema(include_reflection=True)
    light = action_tool_schema(include_reflection=False)
    reflection_keys = {"evaluation_previous_step", "memory_update", "next_goal"}
    for tool in light:
        props = tool["input_schema"]["properties"]
        assert reflection_keys.isdisjoint(props.keys())
    click_full = next(t for t in full if t["name"] == "click")
    click_light = next(t for t in light if t["name"] == "click")
    assert len(click_light["input_schema"]["properties"]) < len(click_full["input_schema"]["properties"])


def test_deep_merge_merges_list_rows_with_same_url():
    from agent_merge import deep_merge

    base = {"items": [{"url": "https://a.com", "name": "A", "score": 1}]}
    update = {"items": [{"url": "https://a.com", "score": 2, "extra": "x"}]}
    deep_merge(base, update)
    assert len(base["items"]) == 1
    assert base["items"][0]["name"] == "A"
    assert base["items"][0]["score"] == 2
    assert base["items"][0]["extra"] == "x"


def test_deep_merge_dedupes_identical_dict_rows():
    from agent_merge import deep_merge

    base = {"rows": [{"id": "1", "x": 1}]}
    update = {"rows": [{"id": "1", "x": 1}]}
    deep_merge(base, update)
    assert len(base["rows"]) == 1


# ---------- OutputManager ----------

def test_output_manager_screenshot_naming():
    with tempfile.TemporaryDirectory() as tmp:
        om = OutputManager(Path(tmp), "sample_001")
        a1 = om.save_screenshot(b"png1", "commit_page", "https://github.com")
        a2 = om.save_screenshot(b"png2", "pr_page", "https://github.com")
        assert a1.filename == "01_commit_page.png"
        assert a2.filename == "02_pr_page.png"


def test_output_manager_sha256():
    with tempfile.TemporaryDirectory() as tmp:
        om = OutputManager(Path(tmp), "sample_001")
        data = b"deterministic test data"
        a = om.save_screenshot(data, "test", "https://example.com")
        import hashlib
        expected = hashlib.sha256(data).hexdigest()
        assert a.sha256 == expected


def test_output_manager_download_sanitization():
    """Path traversal in filename must be neutralized."""
    with tempfile.TemporaryDirectory() as tmp:
        om = OutputManager(Path(tmp), "test")
        a = om.save_download(b"data", "../../../etc/passwd", "https://evil.com")
        assert ".." not in a.filename
        assert "/" not in a.filename
        assert a.filename.endswith("passwd") or "passwd" in a.filename


def test_output_manager_download_dot_file():
    """Filenames starting with dot should be sanitized."""
    with tempfile.TemporaryDirectory() as tmp:
        om = OutputManager(Path(tmp), "test")
        a = om.save_download(b"data", ".hidden", "https://example.com")
        assert not a.filename.startswith(".")  # counter prefix prevents this
        assert a.filename.endswith("download_1")


def test_output_manager_atomic_write():
    """result.json and action_log.json should exist, no .tmp leftovers."""
    with tempfile.TemporaryDirectory() as tmp:
        om = OutputManager(Path(tmp), "test")
        om.save_screenshot(b"png", "page", "https://example.com")
        om.log_step(StepRecord(step=1, action="screenshot", params={"label": "page"}, result="ok"))
        om.write_result(status="done", extracted={"field": "value"}, steps=1)

        sample_dir = Path(tmp) / "test"
        assert (sample_dir / "result.json").exists()
        assert (sample_dir / "action_log.json").exists()
        assert not (sample_dir / "result.json.tmp").exists()
        assert not (sample_dir / "action_log.json.tmp").exists()


def test_output_manager_result_content():
    """Verify result.json contains expected fields."""
    with tempfile.TemporaryDirectory() as tmp:
        om = OutputManager(Path(tmp), "s1")
        om.save_screenshot(b"png", "page", "https://example.com")
        om.write_result(status="done", extracted={"name": "Linus"}, steps=3)

        data = json.loads((Path(tmp) / "s1" / "result.json").read_text())
        assert data["sample_id"] == "s1"
        assert data["status"] == "done"
        assert data["steps"] == 3
        assert data["extracted"]["name"] == "Linus"
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["sha256"]  # not empty


def test_output_manager_action_log_content():
    """Verify action_log.json records steps."""
    with tempfile.TemporaryDirectory() as tmp:
        om = OutputManager(Path(tmp), "s1")
        om.log_step(StepRecord(step=1, thinking="navigate first", action="goto",
                                params={"url": "https://x.com"}, result="ok"))
        om.log_step(StepRecord(step=2, action="screenshot", params={"label": "page"}, result="saved"))
        om.write_result(status="done", extracted={}, steps=2)

        log = json.loads((Path(tmp) / "s1" / "action_log.json").read_text())
        assert len(log) == 2
        assert log[0]["action"] == "goto"
        assert log[1]["action"] == "screenshot"


def test_checkpoint_flushes_live_action_log_and_budget():
    """checkpoint.json should reflect the current trace and step budget."""
    with tempfile.TemporaryDirectory() as tmp:
        om = OutputManager(Path(tmp), "s1")
        om.log_step(StepRecord(
            step=3,
            action="save_progress",
            params={"extracted": {"contributors": [{"name": "Alice"}]}},
            result="new_data=True | contributor 1 saved",
            url="https://github.com/example/repo",
        ))
        om.write_checkpoint(
            step=3,
            accumulated={"contributors": [{"name": "Alice"}]},
            progress_notes=["Contributor 1 saved"],
            max_steps=40,
        )

        sample_dir = Path(tmp) / "s1"
        checkpoint = json.loads((sample_dir / "checkpoint.json").read_text())
        action_log = json.loads((sample_dir / "action_log.json").read_text())

        assert checkpoint["step"] == 3
        assert checkpoint["max_steps"] == 40
        assert checkpoint["steps_logged"] == 1
        assert checkpoint["accumulated_data"]["contributors"][0]["name"] == "Alice"
        assert action_log[0]["action"] == "save_progress"


def test_output_manager_load_interrupted_state_hydrates_resume_data():
    """Interrupted samples should restore artifacts, action log, and resume payload."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        om = OutputManager(base, "s1")
        shot = om.save_screenshot(b"png", "page", "https://example.com/current")
        om.log_step(StepRecord(step=1, action="screenshot", params={"label": "page"}, result="saved"))
        om.write_checkpoint(
            step=1,
            accumulated={"field": "value"},
            progress_notes=["Checkpointed once"],
            max_steps=40,
            current_url="https://example.com/current",
            resume_state={
                "step": 1,
                "current_url": "https://example.com/current",
                "history": [{"step": 1, "action": "screenshot", "result": "saved"}],
                "progress": {"pages_visited": ["https://example.com/current"], "fields_found": [],
                             "artifacts": ["page"], "failed_urls": [], "exhausted_pages": [],
                             "blocked_selectors": [], "dead_ends": []},
                "accumulated": {"field": "value"},
                "progress_notes": ["Checkpointed once"],
                "step_summaries": [],
                "last_summarized_idx": 0,
                "pagination_bonus": 0,
                "effective_max": 40,
                "last_data_step": 1,
                "items_collected": 0,
                "seen_screenshot_hashes": [shot.sha256],
                "selector_fail_counts": {},
                "loop_counter": [],
                "consecutive_failures": 0,
                "network_errors": 0,
                "warned_75": False,
                "warned_90": False,
                "stagnation_count": 0,
                "stagnation_level": 0,
            },
        )

        resumed = OutputManager(base, "s1")
        checkpoint = resumed.load_interrupted_state()

        assert checkpoint is not None
        assert checkpoint["current_url"] == "https://example.com/current"
        assert resumed._counter == 1
        assert len(resumed._artifacts) == 1
        assert resumed._artifacts[0].filename == "01_page.png"
        assert len(resumed._action_log) == 1
        next_shot = resumed.save_screenshot(b"png2", "next", "https://example.com/next")
        assert next_shot.filename == "02_next.png"


def test_output_manager_load_interrupted_state_ignores_finished_samples():
    """Existing final results must not be treated as interrupted-resume candidates."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        om = OutputManager(base, "s1")
        om.write_checkpoint(
            step=2,
            accumulated={"field": "value"},
            progress_notes=["done"],
            max_steps=40,
            current_url="https://example.com/current",
            resume_state={"step": 2, "current_url": "https://example.com/current"},
        )
        om.write_result(status="failed", extracted={"field": "value"}, steps=2)

        resumed = OutputManager(base, "s1")
        assert resumed.load_interrupted_state() is None


# ---------- CSV Merge ----------

def test_csv_merge_basic():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        ev = tmp / "evidence"
        for sid, name in [("s2", "Bob"), ("s1", "Alice")]:
            d = ev / sid; d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({
                "sample_id": sid, "status": "done",
                "extracted": {"name": name},
            }))
        csv_path = merge_results_to_csv(ev, tmp / "out.csv", {"name": "string"})
        lines = csv_path.read_text().strip().split("\n")
        assert lines[0] == "sample_id,status,name"
        assert lines[1].startswith("s1,")  # sorted by sample_id
        assert lines[2].startswith("s2,")


def test_csv_merge_non_scalar_values():
    """Lists/dicts should be JSON-serialized, not Python repr."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        d = tmp / "ev" / "s1"; d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps({
            "sample_id": "s1", "status": "done",
            "extracted": {"tags": ["alpha", "beta"], "meta": {"key": "val"}},
        }))
        csv_path = merge_results_to_csv(tmp / "ev", tmp / "out.csv", {"tags": "array", "meta": "object"})
        content = csv_path.read_text()
        # Should be JSON-serialized (CSV escapes quotes as "")
        assert "alpha" in content
        assert "beta" in content
        assert "{'key'" not in content  # no Python dict repr
        assert "['alpha'" not in content  # no Python list repr


# ---------- Config ----------

def test_config_loads():
    from config import LLM_MODEL, EVIDENCE_DIR
    assert LLM_MODEL  # not empty
    assert EVIDENCE_DIR  # not empty


# ---------- Runner ----------

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
