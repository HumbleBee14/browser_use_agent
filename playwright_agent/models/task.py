"""Task spec model — loaded from tasks/*.json.

All site-specific knowledge lives in the task spec.
Agent code is generic. Swapping site = swapping one JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class TaskSpec(BaseModel):
    """Complete task definition loaded from a JSON file."""

    task_id: str
    phase: str = Field(pattern=r"^(discovery|execution)$")
    start_url: str = ""

    # LLM instructions
    system_prompt: str
    goal: str
    keywords: list[str] = Field(default_factory=list)

    # Output contract
    output_schema: dict[str, str] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)

    # Limits
    max_steps: int = 25

    # Judgment (optional)
    judgment_required: bool = False
    judgment_question: str | None = None
    judgment_output_schema: dict[str, str] | None = None

    # Discovery
    pagination: bool = False
    stop_condition: str = ""

    # Input shape (for non-URL samples like names, code strings)
    input_schema: dict[str, str] = Field(default_factory=dict)

    # Auth (path to Playwright storage_state JSON)
    auth_profile: str | None = None

    @model_validator(mode="after")
    def validate_cross_fields(self) -> TaskSpec:
        """Fail fast on incoherent task specs."""
        # required_fields must be in output_schema
        if self.output_schema:
            for f in self.required_fields:
                if f not in self.output_schema:
                    raise ValueError(
                        f"required_field '{f}' not in output_schema. "
                        f"Available: {list(self.output_schema.keys())}"
                    )
        # judgment_required needs question + schema
        if self.judgment_required:
            if not self.judgment_question:
                raise ValueError("judgment_required=true but judgment_question is missing")
            if not self.judgment_output_schema:
                raise ValueError("judgment_required=true but judgment_output_schema is missing")
        return self


class SampleInput(BaseModel):
    """One row from samples.csv — the work unit."""

    sample_id: str
    url: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_csv_row(cls, row: dict[str, str]) -> SampleInput:
        """Build from a CSV DictReader row."""
        sample_id = row.pop("sample_id", "") or row.pop("id", "")
        url = row.pop("url", "")
        return cls(sample_id=sample_id, url=url, extra=row)


def load_task_spec(path: str | Path) -> TaskSpec:
    """Load a TaskSpec from a JSON file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Task spec not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return TaskSpec(**data)
