"""Judgment models — structured audit decisions.

Judgments are typed answers (yes/no/inconclusive) with confidence,
reasoning, and evidence references. This is what makes the agent
useful for audit workflows — not just data extraction, but
structured decision-making with provenance.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JudgmentResult(BaseModel):
    """A structured audit judgment with full traceability.

    Every judgment records:
    - The question that was asked
    - The answer (constrained to yes/no/inconclusive)
    - Confidence score (0.0-1.0)
    - Reasoning (why this answer)
    - Which artifacts and URLs support this judgment
    """

    question: str
    answer: Literal["yes", "no", "inconclusive"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
