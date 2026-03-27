"""Base task strategy — defines HOW a task family navigates and validates.

Strategies are:
- STATELESS: a fresh instance is created per sample (no cross-contamination)
- Responsible for: prompt building, vision mode, result validation
- NOT responsible for: browser control, file I/O, orchestration

ADL-2: Why strategies exist — simple tasks (visit URL, extract) and complex
tasks (commit→PR→CI graph traversal) need different control logic. Strategies
provide that without bloating the core agent.

ADL-4: Why stateless — concurrent samples share nothing. All per-sample state
lives in the agent's context window and the SampleResult being built.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from models.evidence import (
    NeedsReviewReason,
    SampleResult,
    SampleStatus,
)
from models.task import FieldSpec, SampleInput, TaskConfig


class BaseTaskStrategy(ABC):
    """Abstract base for all task strategies.

    Subclasses must implement build_prompt(). Everything else has
    sensible defaults that can be overridden per family.
    """

    @abstractmethod
    def build_prompt(self, task: TaskConfig, sample: SampleInput) -> str:
        """Build the full agent prompt for this sample.

        This is where task instructions meet sample-specific context.
        The prompt drives the browser-use agent's behavior.
        """

    def should_use_vision(self, task: TaskConfig, current_url: str) -> bool:
        """Whether to enable vision (screenshot analysis) for the current page.

        Called by EvidenceAgent to set initial vision mode.
        For 'no_auth' mode, also called per-step via callback.

        ADL-8: "auto" defaults to False (DOM-only) in v1.
        No runtime DOM→vision fallback. If a task needs vision,
        set "always" in the task YAML.
        """
        if task.use_vision == "never":
            return False
        if task.use_vision == "always":
            return True
        if task.use_vision == "no_auth":
            # Disable vision on login/auth pages to avoid credential leak
            login_patterns = ["login", "signin", "sign-in", "auth", "sso", "oauth"]
            return not any(p in current_url.lower() for p in login_patterns)
        # "auto" — DOM-only in v1, no vision fallback
        return False

    def validate_result(
        self, result: SampleResult, task: TaskConfig
    ) -> SampleResult:
        """Post-process a sample result: check checkpoints, validate fields.

        This is the deterministic gate between "agent thinks it's done" and
        "evidence is actually complete." Called after every sample run.

        ADL-6: Checkpoints prevent false-positive completions.
        """
        # 1. Checkpoint verification
        missed = [
            c.name
            for c in task.checkpoints
            if c.required and c.name not in result.checkpoints_met
        ]
        if missed:
            result.checkpoints_missed = missed
            if result.status == SampleStatus.COMPLETED:
                result.status = SampleStatus.NEEDS_REVIEW
                result.needs_review_reasons.append(
                    NeedsReviewReason.CHECKPOINT_NOT_MET
                )

        # 2. Required field presence check
        extracted_names = {e.field_name for e in result.extracted_fields}
        missing_fields = [
            f.name
            for f in task.output_fields
            if f.required and f.name not in extracted_names
        ]
        if missing_fields:
            result.errors.append(f"Missing required fields: {missing_fields}")
            if result.status == SampleStatus.COMPLETED:
                result.status = SampleStatus.NEEDS_REVIEW
                result.needs_review_reasons.append(
                    NeedsReviewReason.MISSING_REQUIRED_FIELD
                )

        # 3. Field type validation and coercion
        field_specs = {f.name: f for f in task.output_fields}
        has_required_validation_failure = False
        for extraction in result.extracted_fields:
            spec = field_specs.get(extraction.field_name)
            if spec:
                coerced, errs = spec.validate_value(extraction.value)
                if errs:
                    result.errors.extend(errs)
                    if spec.required:
                        has_required_validation_failure = True
                else:
                    extraction.value = coerced

        if has_required_validation_failure:
            if result.status == SampleStatus.COMPLETED:
                result.status = SampleStatus.NEEDS_REVIEW
                result.needs_review_reasons.append(
                    NeedsReviewReason.AMBIGUOUS_EXTRACTION
                )

        return result
