"""File manager — deterministic evidence folder management.

This is the DETERMINISTIC layer. No LLM involved. Every file gets:
- Sequential numbering (01_, 02_, ...)
- SHA-256 hash for audit provenance
- Stable, predictable naming

ADL-5: LLM navigates; pure Python packages. Same evidence collected
twice produces identical folder structure, file names, and hashes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from models.evidence import ActionLogEntry, EvidenceArtifact, SampleResult
from models.task import EvidenceType


class FileManager:
    """Manages per-sample evidence folders.

    One FileManager per sample. Creates the folder, saves artifacts
    with sequential naming, computes hashes, writes manifests.
    """

    def __init__(self, sample_dir: Path, clean: bool = True):
        self.sample_dir = sample_dir
        if clean and sample_dir.exists():
            # Clear stale artifacts from prior retries so the folder
            # only contains evidence from the final successful attempt.
            import shutil
            shutil.rmtree(sample_dir)
        self.sample_dir.mkdir(parents=True, exist_ok=True)
        self._artifact_counter = 0
        self._artifacts: list[EvidenceArtifact] = []

    def save_screenshot(
        self, data: bytes, label: str, source_url: str = "", checkpoint_ref: str | None = None
    ) -> EvidenceArtifact:
        """Save screenshot, return artifact with hash and provenance."""
        self._artifact_counter += 1
        filename = f"{self._artifact_counter:02d}_{label}.png"
        path = self.sample_dir / filename
        path.write_bytes(data)
        sha256 = hashlib.sha256(data).hexdigest()

        artifact = EvidenceArtifact(
            type=EvidenceType.SCREENSHOT,
            filename=filename,
            path=str(path.relative_to(self.sample_dir.parent)),
            description=label,
            source_url=source_url,
            sha256=sha256,
            checkpoint_ref=checkpoint_ref,
        )
        self._artifacts.append(artifact)
        return artifact

    def save_download(
        self, data: bytes, filename: str, source_url: str = "", checkpoint_ref: str | None = None
    ) -> EvidenceArtifact:
        """Save downloaded file, return artifact with hash."""
        path = self.sample_dir / filename
        path.write_bytes(data)
        sha256 = hashlib.sha256(data).hexdigest()

        artifact = EvidenceArtifact(
            type=EvidenceType.DOWNLOAD,
            filename=filename,
            path=str(path.relative_to(self.sample_dir.parent)),
            description=f"Downloaded: {filename}",
            source_url=source_url,
            sha256=sha256,
            checkpoint_ref=checkpoint_ref,
        )
        self._artifacts.append(artifact)
        return artifact

    def save_result_manifest(self, result: SampleResult) -> Path:
        """Write result.json — the complete sample manifest."""
        path = self.sample_dir / "result.json"
        path.write_text(
            result.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return path

    def save_action_log(self, entries: list[ActionLogEntry]) -> Path:
        """Write structured action log."""
        path = self.sample_dir / "action_log.json"
        data = [e.model_dump(mode="json") for e in entries]
        path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    @property
    def artifacts(self) -> list[EvidenceArtifact]:
        """All artifacts saved by this file manager."""
        return list(self._artifacts)
