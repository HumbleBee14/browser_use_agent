"""Batch orchestrator — process multiple samples with concurrency control.

Manages the full lifecycle of a batch run:
1. Create timestamped run directory
2. Spawn EvidenceAgent per sample (bounded by semaphore)
3. Collect all SampleResults (exceptions wrapped, never raw)
4. Write master CSV + run summary
5. Report progress via rich

ADL-4: Fresh strategy instance per sample — no cross-contamination.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from browser_use.llm.base import BaseChatModel

from agent.evidence_agent import EvidenceAgent
from models.evidence import BatchResult, SampleResult, SampleStatus
from models.task import SampleInput, TaskConfig
from output.csv_writer import CSVWriter
from strategies import resolve_strategy

logger = logging.getLogger(__name__)


class BatchOrchestrator:
    """Process multiple samples with bounded concurrency.

    All exceptions are caught and wrapped as failed SampleResults.
    A failed sample never kills the batch.
    """

    def __init__(
        self,
        task_config: TaskConfig,
        llm: BaseChatModel,
        max_concurrent: int = 3,
        output_base: Path = Path("evidence"),
        headless: bool = False,
    ):
        self.task = task_config
        self.llm = llm
        self.headless = headless
        self.strategy_cls = resolve_strategy(task_config.strategy)
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.run_dir = self._create_run_dir(output_base)
        self.samples_dir = self.run_dir / "samples"
        self.samples_dir.mkdir(parents=True, exist_ok=True)
        self._results: list[SampleResult] = []
        self._started_at = datetime.now()

    def _create_run_dir(self, base: Path) -> Path:
        """Create a timestamped run directory."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_dir = base / f"run_{timestamp}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    async def run(self, samples: list[SampleInput]) -> BatchResult:
        """Process all samples, return aggregated results."""
        logger.info(
            f"Starting batch: {len(samples)} samples, "
            f"max_concurrent={self.semaphore._value}, "
            f"strategy={self.task.strategy}"
        )

        tasks = [self._process_sample(s) for s in samples]
        await asyncio.gather(*tasks)

        # Buffered CSV write — one pass after all samples complete
        csv_writer = CSVWriter(
            self.run_dir / "results.csv",
            self.task.output_fields,
        )
        csv_writer.write_batch(self._results)

        # Write run summary
        batch_result = self._build_batch_result()
        self._write_run_summary(batch_result)

        logger.info(
            f"Batch complete: {batch_result.completed} completed, "
            f"{batch_result.failed} failed, "
            f"{batch_result.needs_review} needs_review"
        )

        return batch_result

    async def _process_sample(self, sample: SampleInput) -> None:
        """Process one sample — fresh strategy, exceptions wrapped."""
        async with self.semaphore:
            logger.info(f"Processing sample: {sample.sample_id}")
            try:
                # Fresh strategy per sample — no shared state (ADL-4)
                strategy = self.strategy_cls()
                agent = EvidenceAgent(
                    task_config=self.task,
                    sample=sample,
                    output_dir=self.samples_dir,
                    llm=self.llm,
                    strategy=strategy,
                    headless=self.headless,
                )
                result = await agent.run()
            except Exception as e:
                logger.error(
                    f"Sample {sample.sample_id} failed with unhandled exception: {e}"
                )
                result = SampleResult(
                    sample_id=sample.sample_id,
                    status=SampleStatus.FAILED,
                    input=sample,
                    errors=[f"Unhandled exception: {e}"],
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                )
            self._results.append(result)

    def _build_batch_result(self) -> BatchResult:
        """Aggregate individual results into a BatchResult."""
        total_duration = (datetime.now() - self._started_at).total_seconds()
        return BatchResult(
            results=self._results,
            run_dir=str(self.run_dir),
            total_samples=len(self._results),
            completed=sum(
                1 for r in self._results if r.status == SampleStatus.COMPLETED
            ),
            failed=sum(
                1 for r in self._results if r.status == SampleStatus.FAILED
            ),
            needs_review=sum(
                1 for r in self._results if r.status == SampleStatus.NEEDS_REVIEW
            ),
            skipped=sum(
                1 for r in self._results if r.status == SampleStatus.SKIPPED
            ),
            total_duration_seconds=total_duration,
        )

    def _write_run_summary(self, batch: BatchResult) -> Path:
        """Write run_summary.json to the run directory."""
        summary = {
            "run_id": self.run_dir.name,
            "task": self.task.name,
            "strategy": self.task.strategy,
            "total_samples": batch.total_samples,
            "completed": batch.completed,
            "failed": batch.failed,
            "needs_review": batch.needs_review,
            "skipped": batch.skipped,
            "total_duration_seconds": round(batch.total_duration_seconds, 1),
            "avg_duration_per_sample": (
                round(batch.total_duration_seconds / max(batch.total_samples, 1), 1)
            ),
            "avg_steps_per_sample": (
                round(
                    sum(r.steps_taken for r in self._results)
                    / max(len(self._results), 1),
                    1,
                )
            ),
        }
        path = self.run_dir / "run_summary.json"
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return path
