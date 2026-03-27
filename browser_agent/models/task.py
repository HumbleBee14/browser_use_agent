"""Task configuration models — defines WHAT to do.

Loaded from YAML task definitions. These are pure data classes
with no business logic beyond validation.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class EvidenceType(str, Enum):
    """Types of evidence that can be collected."""

    SCREENSHOT = "screenshot"
    DOWNLOAD = "download"
    EXTRACTION = "extraction"
    JUDGMENT = "judgment"


class FieldSpec(BaseModel):
    """Declares one output field with its expected type and validation.

    Used by strategies to validate extracted values after collection.
    Type coercion happens in validate_value() — the agent extracts raw
    strings, and we coerce to the declared type deterministically.
    """

    name: str
    type: Literal["str", "int", "float", "bool", "date", "url"] = "str"
    required: bool = True
    description: str = ""
    pattern: str | None = None  # Optional regex for validation

    def validate_value(self, value: Any) -> tuple[Any, list[str]]:
        """Coerce and validate a value against this field spec.

        Returns:
            (coerced_value, errors) — empty errors list means success.
        """
        errors: list[str] = []
        if value is None:
            return None, [f"{self.name}: value is None"] if self.required else []
        try:
            if self.type == "int":
                value = int(value)
            elif self.type == "float":
                value = float(value)
            elif self.type == "bool":
                value = str(value).lower() in ("true", "yes", "1")
            elif self.type == "date":
                from dateutil.parser import parse as parse_date

                value = parse_date(str(value)).date().isoformat()
            elif self.type == "url":
                value = str(value)
                if not value.startswith(("http://", "https://")):
                    errors.append(f"{self.name}: invalid URL '{value}'")
            else:
                value = str(value)
        except (ValueError, TypeError) as e:
            errors.append(f"{self.name}: type coercion to {self.type} failed — {e}")
        if self.pattern and not re.match(self.pattern, str(value)):
            errors.append(
                f"{self.name}: pattern mismatch, expected {self.pattern}"
            )
        return value, errors


class Checkpoint(BaseModel):
    """A required evidence collection point.

    The agent must satisfy all required checkpoints before a sample
    can be marked 'completed'. This prevents false-positive completions
    where the agent thinks it's done but didn't collect everything.
    """

    name: str
    evidence_type: EvidenceType
    description: str = ""
    required: bool = True


class TaskConfig(BaseModel):
    """Complete task definition — loaded from YAML.

    This is the central config object. It tells the system:
    - What strategy to use (navigation pattern)
    - What to tell the agent (instructions)
    - What fields to extract (output_fields)
    - What evidence to require (checkpoints)
    - Safety/performance limits
    """

    name: str
    description: str = ""
    strategy: str = "single_page"
    instructions: str
    input_file: str
    input_columns: list[str] = Field(default_factory=lambda: ["sample_id", "url"])
    output_fields: list[FieldSpec] = Field(default_factory=list)
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    evidence_types: list[EvidenceType] = Field(
        default_factory=lambda: [EvidenceType.SCREENSHOT]
    )
    max_steps: int = 25
    max_retries: int = 2
    timeout_seconds: int = 120
    allowed_domains: list[str] = Field(default_factory=list)
    requires_login: bool = False
    use_vision: Literal["auto", "always", "never", "no_auth"] = "auto"
    judgment_question: str | None = None


class SampleInput(BaseModel):
    """One row of work — parsed from the input CSV/JSON.

    sample_id is the only required field. Everything else
    is flexible to support different task shapes.
    """

    sample_id: str
    url: str | None = None
    task_type: str | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)
