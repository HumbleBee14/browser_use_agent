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

from rich.console import Console
from browser_use.llm.base import BaseChatModel

from agent.evidence_agent import EvidenceAgent
from models.evidence import BatchResult, SampleResult, SampleStatus
from models.task import SampleInput, TaskConfig
from output.csv_writer import CSVWriter
from strategies import resolve_strategy

logger = logging.getLogger(__name__)
console = Console()


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
        self._all_samples = samples
        self._total = len(samples)

        console.print(
            f"  [dim]Batch:[/dim] {self._total} samples | "
            f"concurrency={self.semaphore._value} | "
            f"strategy={self.task.strategy}"
        )
        console.print()

        tasks = [self._process_sample(s) for s in samples]
        results = await asyncio.gather(*tasks)
        self._results = list(results)

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

    async def _process_sample(self, sample: SampleInput) -> SampleResult:
        """Process one sample — fresh strategy, exceptions wrapped. Returns result."""
        async with self.semaphore:
            idx = next(
                (i for i, s in enumerate(self._all_samples) if s.sample_id == sample.sample_id),
                0,
            ) + 1
            console.print(
                f"  [{idx}/{self._total}] [cyan]{sample.sample_id}[/cyan] "
                f"[dim]starting...[/dim]"
            )
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

            # Log result with color-coded status
            duration = (
                (result.completed_at - result.started_at).total_seconds()
                if result.completed_at
                else 0.0
            )
            status_style = {
                SampleStatus.COMPLETED: "[bold green]COMPLETED[/bold green]",
                SampleStatus.FAILED: "[bold red]FAILED[/bold red]",
                SampleStatus.NEEDS_REVIEW: "[bold yellow]NEEDS REVIEW[/bold yellow]",
                SampleStatus.SKIPPED: "[dim]SKIPPED[/dim]",
            }
            status_text = status_style.get(result.status, result.status.value)
            fields_count = len(result.extracted_fields)
            artifacts_count = len(result.artifacts)
            checkpoints_count = len(result.checkpoints_met)

            console.print(
                f"  [{idx}/{self._total}] [cyan]{sample.sample_id}[/cyan] "
                f"{status_text} "
                f"[dim]({result.steps_taken} steps, {duration:.1f}s, "
                f"{fields_count} fields, {artifacts_count} artifacts, "
                f"{checkpoints_count} checkpoints)[/dim]"
            )

            if result.errors:
                for err in result.errors[:2]:
                    short = err[:120].split("\n")[0]
                    console.print(f"           [red]{short}[/red]")

            return result

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
