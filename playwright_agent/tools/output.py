"""Evidence output tools — pure file I/O, zero Playwright.

Handles: per-sample folders, sequential screenshot naming,
SHA-256 hashing, result.json, action_log.json, combined.csv.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

from models.actions import EvidenceArtifact, SampleResult, StepRecord


class OutputManager:
    """Manages evidence output for one sample.

    One instance per sample. Handles file naming, hashing, and writing.
    The agent loop calls save_screenshot/write_result — never touches
    the filesystem directly.
    """

    def __init__(self, evidence_dir: Path, sample_id: str):
        self.sample_id = sample_id
        self.sample_dir = evidence_dir / sample_id
        self.sample_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self._artifacts: list[EvidenceArtifact] = []
        self._action_log: list[StepRecord] = []
        self._started_at = datetime.utcnow().isoformat() + "Z"

    def save_screenshot(self, data: bytes, label: str, source_url: str) -> EvidenceArtifact:
        """Save screenshot with sequential naming and SHA-256 hash."""
        self._counter += 1
        filename = f"{self._counter:02d}_{label}.png"
        path = self.sample_dir / filename
        path.write_bytes(data)

        sha256 = hashlib.sha256(data).hexdigest()
        artifact = EvidenceArtifact(
            filename=filename,
            sha256=sha256,
            source_url=source_url,
        )
        self._artifacts.append(artifact)
        return artifact

    def save_download(self, data: bytes, filename: str, source_url: str) -> EvidenceArtifact:
        """Save a downloaded file with SHA-256 hash.

        Filename is sanitized to prevent path traversal or overwriting artifacts.
        """
        import re
        # Strip path components and dangerous characters
        safe_name = Path(filename).name  # remove any directory components
        safe_name = re.sub(r'[^\w\-.]', '_', safe_name)  # only alphanum, dash, dot
        if not safe_name or safe_name.startswith('.'):
            safe_name = f"download_{self._counter + 1}"
        # Prefix with counter to prevent collisions
        self._counter += 1
        safe_name = f"{self._counter:02d}_{safe_name}"
        path = self.sample_dir / safe_name
        path.write_bytes(data)
        filename = safe_name  # use sanitized name for the artifact

        sha256 = hashlib.sha256(data).hexdigest()
        artifact = EvidenceArtifact(
            filename=filename,
            sha256=sha256,
            source_url=source_url,
        )
        self._artifacts.append(artifact)
        return artifact

    def log_step(self, record: StepRecord) -> None:
        """Append a step to the action log."""
        self._action_log.append(record)

    def _write_json_atomic(self, path: Path, data: str) -> None:
        """Atomically replace a JSON file on disk."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(path)

    def _flush_action_log(self) -> None:
        """Persist the current in-memory action log.

        Long-horizon runs rely on checkpoints for live monitoring and crash
        recovery. Writing the action log alongside checkpoints keeps the trace
        aligned with what checkpoint.json reports.
        """
        path = self.sample_dir / "action_log.json"
        log_data = json.dumps(
            [r.model_dump() for r in self._action_log], indent=2, default=str
        )
        self._write_json_atomic(path, log_data)

    def write_checkpoint(
        self,
        step: int,
        accumulated: dict,
        progress_notes: list[str],
        max_steps: int | None = None,
        status: str = "in_progress",
    ) -> None:
        """Write a live checkpoint file that updates as the agent runs.

        This file is overwritten each time — always reflects latest state.
        Useful for monitoring long-horizon tasks in real-time.
        """
        self._flush_action_log()
        checkpoint = {
            "sample_id": self.sample_id,
            "status": status,
            "step": step,
            "max_steps": max_steps,
            "accumulated_data": accumulated,
            "progress_notes": progress_notes,
            "artifacts_so_far": [a.model_dump() for a in self._artifacts],
            "steps_logged": len(self._action_log),
            "started_at": self._started_at,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }
        path = self.sample_dir / "checkpoint.json"
        self._write_json_atomic(path, json.dumps(checkpoint, indent=2, default=str))

    def write_result(
        self,
        status: str = "done",
        extracted: dict | None = None,
        judgment: dict | None = None,
        errors: list[str] | None = None,
        notes: list[str] | None = None,
        steps: int = 0,
    ) -> SampleResult:
        """Write result.json and action_log.json to the sample folder."""
        result = SampleResult(
            sample_id=self.sample_id,
            status=status,
            steps=steps,
            extracted=extracted or {},
            artifacts=self._artifacts,
            judgment=judgment,
            flagged=bool(errors),
            notes=notes or [],
            errors=errors or [],
            started_at=self._started_at,
            finished_at=datetime.utcnow().isoformat() + "Z",
        )

        # Write both files atomically: write to .tmp then rename.
        # If crash occurs between writes, neither partial file exists.
        result_data = json.dumps(result.model_dump(), indent=2, default=str)
        log_data = json.dumps(
            [r.model_dump() for r in self._action_log], indent=2, default=str
        )

        self._write_json_atomic(self.sample_dir / "result.json", result_data)
        self._write_json_atomic(self.sample_dir / "action_log.json", log_data)

        return result


def merge_results_to_csv(
    evidence_dir: Path,
    output_path: Path,
    output_schema: dict[str, str],
) -> Path:
    """Merge all result.json files into combined.csv.

    Called once at batch end — no concurrent writes, no filelock needed.
    Results sorted by sample_id for deterministic output.
    """
    results = []
    for sample_dir in sorted(evidence_dir.iterdir()):
        result_file = sample_dir / "result.json"
        if result_file.exists():
            data = json.loads(result_file.read_text(encoding="utf-8"))
            results.append(data)

    results.sort(key=lambda r: r.get("sample_id", ""))

    # Build CSV columns: sample_id + status + output_schema fields
    columns = ["sample_id", "status"] + list(output_schema.keys())

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = {"sample_id": r["sample_id"], "status": r["status"]}
            extracted = r.get("extracted", {})
            for field in output_schema:
                value = extracted.get(field, "")
                # Serialize non-scalar values as JSON for deterministic CSV output
                if isinstance(value, (list, dict)):
                    value = json.dumps(value, ensure_ascii=False)
                row[field] = value
            writer.writerow(row)

    return output_path
