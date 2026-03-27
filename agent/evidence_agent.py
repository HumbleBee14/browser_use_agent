"""EvidenceAgent — the core wrapper around browser-use.

One agent per sample. Creates a fresh browser-use Agent with:
- Task-specific prompt (built by strategy)
- Custom evidence actions (screenshot, record_fields, judgment)
- Vision mode controlled by strategy
- Timeout and retry logic

ADL-5: The agent navigates (agentic). The FileManager packages (deterministic).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from rich.console import Console
from browser_use import Agent, Browser, BrowserProfile
from browser_use.llm.base import BaseChatModel

from agent.actions import create_evidence_controller
from models.evidence import (
    ActionLogEntry,
    NeedsReviewReason,
    SampleResult,
    SampleStatus,
)
from models.task import SampleInput, TaskConfig
from output.file_manager import FileManager
from strategies.base import BaseTaskStrategy

logger = logging.getLogger(__name__)
console = Console()


class EvidenceAgent:
    """Wraps browser-use Agent for one sample's evidence collection.

    Lifecycle:
    1. __init__: receive task config, sample, strategy, output dir
    2. run(): build prompt → create browser-use agent → execute → validate → return result
    3. Discard: agent is single-use, no state reuse between samples
    """

    def __init__(
        self,
        task_config: TaskConfig,
        sample: SampleInput,
        output_dir: Path,
        llm: BaseChatModel,
        strategy: BaseTaskStrategy,
        headless: bool = False,
    ):
        self.task = task_config
        self.sample = sample
        self.llm = llm
        self.strategy = strategy
        self.headless = headless
        self.sample_dir = output_dir / sample.sample_id
        self.file_manager = FileManager(self.sample_dir)

    async def run(self) -> SampleResult:
        """Execute evidence collection for this sample.

        Returns a fully validated SampleResult — never raises.
        """
        started_at = datetime.now()
        retries_used = 0

        for attempt in range(self.task.max_retries + 1):
            try:
                # Fresh file manager per attempt — no stale data from prior retries
                self.file_manager = FileManager(self.sample_dir)
                result = await self._execute_once(started_at)
                result.retries_used = attempt  # attempt 0 = no retries, 1 = one retry, etc.
                # Strategy validates: checkpoints, field types, completeness
                return self.strategy.validate_result(result, self.task)
            except asyncio.TimeoutError:
                retries_used = attempt
                logger.warning(
                    f"Sample {self.sample.sample_id}: timeout on attempt {attempt + 1}"
                )
                if attempt < self.task.max_retries:
                    continue
                return self._build_error_result(
                    started_at,
                    error=f"Timeout after {self.task.timeout_seconds}s",
                    reason=NeedsReviewReason.TIMEOUT,
                    retries=retries_used,
                )
            except Exception as e:
                retries_used = attempt
                logger.error(
                    f"Sample {self.sample.sample_id}: error on attempt {attempt + 1}: {e}"
                )
                if attempt < self.task.max_retries:
                    continue
                return self._build_error_result(
                    started_at,
                    error=str(e),
                    retries=retries_used,
                )

        # Should not reach here, but safety fallback
        return self._build_error_result(started_at, error="Exhausted all retries")

    async def _execute_once(self, started_at: datetime) -> SampleResult:
        """Run the browser-use agent once with timeout."""
        sid = self.sample.sample_id

        # Build prompt via strategy
        prompt = self.strategy.build_prompt(self.task, self.sample)

        # Vision mode: strategy decides based on task config.
        # NOTE: Evaluated once at start. browser-use Agent does not support
        # toggling use_vision mid-run, so "no_auth" mode only checks the
        # initial URL. Per-step re-evaluation requires upstream API support.
        initial_vision = self.strategy.should_use_vision(
            self.task, self.sample.url or ""
        )

        # Fresh controller with evidence actions per sample
        controller = create_evidence_controller(self.file_manager)

        # Browser profile with headless, domain allowlist, and page load tuning.
        # JS-heavy sites (GitHub) never fully idle — reduce wait to avoid timeouts.
        browser_profile = BrowserProfile(
            headless=self.headless,
            allowed_domains=self.task.allowed_domains or None,
            wait_for_network_idle_page_load_time=5.0,
            minimum_wait_page_load_time=0.5,
            wait_between_actions=0.5,
        )

        # Step callback for live progress — signature: (BrowserStateSummary, AgentOutput, int)
        def on_step(browser_state, agent_output, step_num):
            actions = []
            if agent_output and hasattr(agent_output, "action"):
                for a in agent_output.action:
                    dumped = a.model_dump(exclude_none=True)
                    actions.extend(dumped.keys())
            action_str = ", ".join(actions) if actions else "thinking"
            goal = ""
            if agent_output and hasattr(agent_output, "next_goal") and agent_output.next_goal:
                goal = f" [dim italic]{agent_output.next_goal[:80]}[/dim italic]"
            console.print(
                f"           [dim]{sid}[/dim] step {step_num}: {action_str}{goal}"
            )

        # Create browser-use agent
        agent = Agent(
            task=prompt,
            llm=self.llm,
            controller=controller,
            browser_profile=browser_profile,
            use_vision=initial_vision,
            max_actions_per_step=5,
            register_new_step_callback=on_step,
        )

        # Run with timeout
        history = await asyncio.wait_for(
            agent.run(max_steps=self.task.max_steps),
            timeout=self.task.timeout_seconds,
        )

        # Package results
        return self._package_result(history, started_at)

    def _package_result(self, history, started_at: datetime) -> SampleResult:
        """Convert browser-use AgentHistoryList into our SampleResult."""
        completed_at = datetime.now()

        # Collect evidence state from file_manager (populated by custom actions)
        extracted_fields = self.file_manager._extractions
        checkpoints_met = self.file_manager._checkpoints_met
        judgment = self.file_manager._judgment

        # Build action log from history — use max length across all lists
        # to avoid silently dropping entries when lists diverge
        action_log = []
        urls = history.urls() if hasattr(history, "urls") else []
        action_names = history.action_names() if hasattr(history, "action_names") else []
        action_results = history.extracted_content() if hasattr(history, "extracted_content") else []

        log_length = max(len(action_names), len(urls), len(action_results))
        for i in range(log_length):
            action_log.append(
                ActionLogEntry(
                    step=i + 1,
                    action=action_names[i] if i < len(action_names) else "unknown",
                    target=urls[i] if i < len(urls) else "",
                    result=action_results[i] if i < len(action_results) else "",
                )
            )

        # Determine status with accurate error classification
        status = SampleStatus.COMPLETED
        needs_review_reasons: list[NeedsReviewReason] = []
        raw_errors = [str(e) for e in history.errors() if e]

        if raw_errors:
            # Classify errors instead of blindly using MAX_STEPS_EXCEEDED
            for err in raw_errors:
                err_lower = err.lower()
                if "max steps" in err_lower or "step limit" in err_lower:
                    if NeedsReviewReason.MAX_STEPS_EXCEEDED not in needs_review_reasons:
                        needs_review_reasons.append(NeedsReviewReason.MAX_STEPS_EXCEEDED)
                elif "login" in err_lower or "auth" in err_lower or "sign in" in err_lower:
                    if NeedsReviewReason.LOGIN_REQUIRED not in needs_review_reasons:
                        needs_review_reasons.append(NeedsReviewReason.LOGIN_REQUIRED)
                elif "captcha" in err_lower or "mfa" in err_lower:
                    if NeedsReviewReason.MFA_CAPTCHA not in needs_review_reasons:
                        needs_review_reasons.append(NeedsReviewReason.MFA_CAPTCHA)

            if needs_review_reasons:
                status = SampleStatus.NEEDS_REVIEW
            elif not history.is_successful():
                # Errors present but no specific category — mark as needs_review generically
                status = SampleStatus.NEEDS_REVIEW

        # Save action log and result manifest
        self.file_manager.save_action_log(action_log)

        result = SampleResult(
            sample_id=self.sample.sample_id,
            status=status,
            needs_review_reasons=needs_review_reasons,
            input=self.sample,
            extracted_fields=extracted_fields,
            artifacts=self.file_manager.artifacts,
            checkpoints_met=checkpoints_met,
            judgment=judgment,
            action_log=action_log,
            errors=raw_errors,
            started_at=started_at,
            completed_at=completed_at,
            steps_taken=history.number_of_steps(),
        )

        # Save the result manifest
        self.file_manager.save_result_manifest(result)

        return result

    def _build_error_result(
        self,
        started_at: datetime,
        error: str,
        reason: NeedsReviewReason | None = None,
        retries: int = 0,
    ) -> SampleResult:
        """Build a failed/needs_review SampleResult from an error.

        Preserves any partial evidence already collected (screenshots,
        fields, checkpoints) so result.json reflects what's on disk.
        """
        status = SampleStatus.NEEDS_REVIEW if reason else SampleStatus.FAILED
        reasons = [reason] if reason else []

        result = SampleResult(
            sample_id=self.sample.sample_id,
            status=status,
            needs_review_reasons=reasons,
            input=self.sample,
            # Preserve partial evidence from the attempt that timed out / failed
            extracted_fields=self.file_manager._extractions,
            artifacts=self.file_manager.artifacts,
            checkpoints_met=self.file_manager._checkpoints_met,
            judgment=self.file_manager._judgment,
            errors=[error],
            started_at=started_at,
            completed_at=datetime.now(),
            retries_used=retries,
        )

        # Save manifest including partial evidence
        self.file_manager.save_result_manifest(result)
        return result
