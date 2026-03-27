"""Data models for the browser evidence agent.

All models are Pydantic v2 BaseModel subclasses.
Organized by concern: task config, evidence output, judgments.
"""

from models.evidence import (
    ActionLogEntry,
    BatchResult,
    EvidenceArtifact,
    FieldExtraction,
    NeedsReviewReason,
    SampleResult,
    SampleStatus,
)
from models.judgment import JudgmentResult
from models.task import (
    Checkpoint,
    EvidenceType,
    FieldSpec,
    SampleInput,
    TaskConfig,
)

__all__ = [
    "ActionLogEntry",
    "BatchResult",
    "Checkpoint",
    "EvidenceArtifact",
    "EvidenceType",
    "FieldExtraction",
    "FieldSpec",
    "JudgmentResult",
    "NeedsReviewReason",
    "SampleInput",
    "SampleResult",
    "SampleStatus",
    "TaskConfig",
]
