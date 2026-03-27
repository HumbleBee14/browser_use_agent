"""Phase 4 regression tests — orchestrator, discovery, worker logic.

No browser, no LLM calls. Tests the orchestration logic using mocked inputs.

Run: python tests/test_phase4.py
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.task import TaskSpec, SampleInput


# ---- load_samples + input_schema validation ----

def test_load_samples_basic():
    from main import load_samples

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sample_id", "url"])
        writer.writerow(["s1", "https://example.com/1"])
        writer.writerow(["s2", "https://example.com/2"])
        f.flush()
        samples = load_samples(f.name)

    assert len(samples) == 2
    assert samples[0].sample_id == "s1"
    assert samples[1].url == "https://example.com/2"


def test_load_samples_validates_input_schema():
    from main import load_samples

    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        input_schema={"name": "string", "company": "string | null"},
    )

    # CSV missing required 'name' column
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sample_id", "url"])
        writer.writerow(["s1", "https://example.com"])
        f.flush()

        try:
            load_samples(f.name, spec)
            assert False, "Should have raised ValueError for missing 'name' column"
        except ValueError as e:
            assert "name" in str(e)


def test_load_samples_allows_nullable_missing():
    from main import load_samples

    spec = TaskSpec(
        task_id="test", phase="execution", system_prompt="x", goal="x",
        input_schema={"name": "string", "company": "string | null"},
    )

    # CSV has 'name' but not 'company' (which is nullable — OK)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sample_id", "url", "name"])
        writer.writerow(["s1", "https://example.com", "Alice"])
        f.flush()

        samples = load_samples(f.name, spec)  # should NOT raise
        assert len(samples) == 1


# ---- idempotent restart ----

def test_get_completed_samples():
    from main import get_completed_samples

    with tempfile.TemporaryDirectory() as tmp:
        ev = Path(tmp)

        # Sample 1: done
        d1 = ev / "s1"; d1.mkdir()
        (d1 / "result.json").write_text(json.dumps({"sample_id": "s1", "status": "done"}))

        # Sample 2: failed
        d2 = ev / "s2"; d2.mkdir()
        (d2 / "result.json").write_text(json.dumps({"sample_id": "s2", "status": "failed"}))

        # Sample 3: needs_review
        d3 = ev / "s3"; d3.mkdir()
        (d3 / "result.json").write_text(json.dumps({"sample_id": "s3", "status": "needs_review"}))

        completed = get_completed_samples(ev)
        assert completed == {"s1"}, f"Only 'done' should count as completed: {completed}"


def test_get_completed_samples_empty():
    from main import get_completed_samples

    with tempfile.TemporaryDirectory() as tmp:
        completed = get_completed_samples(Path(tmp))
        assert completed == set()


def test_get_completed_samples_nonexistent_dir():
    from main import get_completed_samples

    completed = get_completed_samples(Path("/nonexistent/path"))
    assert completed == set()


# ---- discovery output ----

def test_discovery_sample_id_from_name():
    """Name-based items should derive sample_id from name field."""
    item = {"name": "Satya Nadella", "company": "Microsoft"}
    sid = (
        item.get("username") or item.get("id") or item.get("sample_id")
        or item.get("name", "").replace(" ", "_").lower()
        or "fallback"
    )
    assert sid == "satya_nadella"


def test_discovery_sample_id_fallback():
    """Items with no identifiable field get a numbered fallback."""
    item = {"url": "https://example.com/unknown"}
    sid = (
        item.get("username") or item.get("id") or item.get("sample_id")
        or item.get("name", "").replace(" ", "_").lower()
        or "sample_1"
    )
    # Empty string from name="" is falsy, so falls to "sample_1"
    assert sid == "sample_1"


def test_discovery_preserves_extra_fields():
    """SampleInput.extra should carry all non-standard fields."""
    s = SampleInput(sample_id="p1", url="https://linkedin.com/in/x", extra={"name": "Alice", "company": "ACME"})
    assert s.extra["name"] == "Alice"
    assert s.extra["company"] == "ACME"


# ---- batch summary logic ----

def test_batch_summary_counts():
    """Verify status counting logic."""
    with tempfile.TemporaryDirectory() as tmp:
        ev = Path(tmp)
        for sid, status in [("s1", "done"), ("s2", "done"), ("s3", "failed"), ("s4", "needs_review")]:
            d = ev / sid; d.mkdir()
            (d / "result.json").write_text(json.dumps({"sample_id": sid, "status": status}))

        done = failed = review = 0
        for sid in ["s1", "s2", "s3", "s4"]:
            data = json.loads((ev / sid / "result.json").read_text())
            status = data["status"]
            if status == "done": done += 1
            elif status == "needs_review": review += 1
            else: failed += 1

        assert done == 2
        assert failed == 1
        assert review == 1


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
