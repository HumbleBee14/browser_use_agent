"""Evidence models — what was collected, with full provenance.

Every artifact tracks WHERE it came from (source_url), WHAT it proves
(checkpoint_ref), and HOW it can be verified (sha256). Every extracted
field tracks its source URL and DOM selector. This is the difference
between a demo and an audit-grade tool.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from models.judgment import JudgmentResult
from models.task import EvidenceType, SampleInput


class SampleStatus(str, Enum):
    """Possible completion states for a sample."""

    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    SKIPPED = "skipped"


class NeedsReviewReason(str, Enum):
    """Structured reasons for needs_review status.

    When a sample is marked needs_review, we always say WHY.
    This lets reviewers prioritize and batch-resolve issues.
    """

    LOGIN_REQUIRED = "login_required"
    MFA_CAPTCHA = "mfa_or_captcha"
    AMBIGUOUS_EXTRACTION = "ambiguous_extraction"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    CHECKPOINT_NOT_MET = "checkpoint_not_met"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    UNEXPECTED_DOMAIN = "unexpected_domain"
    TIMEOUT = "timeout"


class EvidenceArtifact(BaseModel):
    """One piece of evidence collected — with full provenance.

    Every artifact knows:
    - Where it was captured (source_url)
    - What checkpoint it satisfies (checkpoint_ref)
    - Its integrity hash (sha256) for downloads
    """

    type: EvidenceType
    filename: str
    path: str  # Relative path within sample folder
    description: str = ""
    source_url: str = ""
    sha256: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)
    checkpoint_ref: str | None = None


class FieldExtraction(BaseModel):
    """One extracted field — tracks WHERE it came from.

    This is the key provenance model. An auditor can trace any
    extracted value back to its source: which URL, which DOM element,
    which screenshot supports it.
    """

    field_name: str
    value: Any
    source_url: str = ""
    source_selector: str | None = None
    artifact_ref: str | None = None  # Which screenshot/artifact supports this
    confidence: float = 1.0


class ActionLogEntry(BaseModel):
    """Structured action log — not free-text.

    Every agent step is recorded with what was done, where,
    and what happened. This makes runs reviewable and debuggable.
    """

    step: int
    action: str  # e.g., "navigate", "click", "extract", "screenshot"
    target: str  # URL or element description
    result: str  # Outcome description
    timestamp: datetime = Field(default_factory=datetime.now)


class SampleResult(BaseModel):
    """Complete result for one sample — written as result.json.

    This is the master output object per sample. It contains everything:
    status, extracted fields with provenance, artifacts, checkpoints,
    judgments, action log, and errors. The strategy's validate_result()
    populates checkpoint verification fields.
    """

    sample_id: str
    status: SampleStatus = SampleStatus.COMPLETED
    needs_review_reasons: list[NeedsReviewReason] = Field(default_factory=list)
    input: SampleInput
    extracted_fields: list[FieldExtraction] = Field(default_factory=list)
    artifacts: list[EvidenceArtifact] = Field(default_factory=list)
    checkpoints_met: list[str] = Field(default_factory=list)
    checkpoints_missed: list[str] = Field(default_factory=list)
    judgment: JudgmentResult | None = None
    action_log: list[ActionLogEntry] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    steps_taken: int = 0
    retries_used: int = 0


class BatchResult(BaseModel):
    """Aggregated results for an entire batch run."""

    results: list[SampleResult] = Field(default_factory=list)
    run_dir: str = ""
    total_samples: int = 0
    completed: int = 0
    failed: int = 0
    needs_review: int = 0
    skipped: int = 0
    total_duration_seconds: float = 0.0
