"""Orchestrator — the entry point.

Two-phase execution:
  Phase 1 (optional): Discovery — paginate a start URL → samples.csv
  Phase 2: Execution — N parallel workers → evidence folders → combined.csv

Usage:
  # Batch from CSV
  python main.py --task tasks/github_profile.json --input samples.csv

  # Single sample
  python main.py --task tasks/github_profile.json --url https://github.com/torvalds --id torvalds

  # Discovery + Execution
  python main.py --task tasks/github_profile.json --discover tasks/github_discovery.json --start-url https://github.com/orgs/microsoft/people
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table

import config
import worker
from models.task import TaskSpec, SampleInput, load_task_spec
from tools.output import merge_results_to_csv

console = Console()


def load_samples(input_path: str) -> list[SampleInput]:
    """Load samples from a CSV file."""
    samples = []
    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            samples.append(SampleInput.from_csv_row(dict(row)))
    return samples


def get_completed_samples(evidence_dir: Path) -> set[str]:
    """Find samples that already have status=done (for idempotent restart)."""
    completed = set()
    if not evidence_dir.exists():
        return completed
    for sample_dir in evidence_dir.iterdir():
        result_file = sample_dir / "result.json"
        if result_file.exists():
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
                if data.get("status") == "done":
                    completed.add(data.get("sample_id", ""))
            except Exception:
                pass
    return completed


async def run_batch(
    task_spec: TaskSpec,
    samples: list[SampleInput],
    evidence_dir: Path,
    max_concurrent: int,
    headless: bool,
) -> None:
    """Run all samples in parallel with bounded concurrency."""
    sem = asyncio.Semaphore(max_concurrent)
    started_at = time.time()

    # Skip already-completed samples (idempotent restart)
    completed = get_completed_samples(evidence_dir)
    pending = [s for s in samples if s.sample_id not in completed]

    if completed:
        console.print(f"  [dim]Skipping {len(completed)} already-completed samples[/dim]")

    if not pending:
        console.print("[yellow]No pending samples to process.[/yellow]")
        return

    console.print(
        f"  [dim]Batch:[/dim] {len(pending)} samples | "
        f"concurrency={max_concurrent} | "
        f"task={task_spec.task_id}"
    )
    console.print()

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=headless,
        args=["--disable-features=WebContentsForceDark"],
    )

    async def _worker(sample: SampleInput) -> str:
        async with sem:
            idx = next(i for i, s in enumerate(pending) if s.sample_id == sample.sample_id) + 1
            console.print(f"  [{idx}/{len(pending)}] [cyan]{sample.sample_id}[/cyan] [dim]starting...[/dim]")
            start = time.time()

            sample_id = await worker.run_sample(browser, sample, task_spec, evidence_dir)

            duration = time.time() - start
            # Read result to get status
            result_path = evidence_dir / sample_id / "result.json"
            status = "unknown"
            if result_path.exists():
                data = json.loads(result_path.read_text(encoding="utf-8"))
                status = data.get("status", "unknown")

            status_style = {
                "done": "[bold green]DONE[/bold green]",
                "failed": "[bold red]FAILED[/bold red]",
                "needs_review": "[bold yellow]NEEDS REVIEW[/bold yellow]",
            }
            console.print(
                f"  [{idx}/{len(pending)}] [cyan]{sample.sample_id}[/cyan] "
                f"{status_style.get(status, status)} [dim]({duration:.1f}s)[/dim]"
            )
            return sample_id

    # Run all workers in parallel (bounded by semaphore)
    results = await asyncio.gather(
        *[_worker(s) for s in pending],
        return_exceptions=True,
    )

    await browser.close()
    await p.stop()

    # Merge results into combined.csv
    csv_path = evidence_dir.parent / "combined.csv"
    merge_results_to_csv(evidence_dir, csv_path, task_spec.output_schema)

    # Print summary
    total_duration = time.time() - started_at
    _print_summary(evidence_dir, pending, total_duration, csv_path)


def _print_summary(evidence_dir: Path, samples: list[SampleInput], duration: float, csv_path: Path):
    """Print batch summary table."""
    done = failed = review = 0
    for s in samples:
        result_path = evidence_dir / s.sample_id / "result.json"
        if result_path.exists():
            data = json.loads(result_path.read_text(encoding="utf-8"))
            status = data.get("status", "failed")
            if status == "done":
                done += 1
            elif status == "needs_review":
                review += 1
            else:
                failed += 1
        else:
            failed += 1

    console.print()
    table = Table(title="Batch Summary")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Total Samples", str(len(samples)))
    table.add_row("Done", str(done))
    table.add_row("Failed", str(failed))
    table.add_row("Needs Review", str(review))
    table.add_row("Duration", f"{duration:.1f}s")
    table.add_row("Evidence", str(evidence_dir))
    table.add_row("CSV", str(csv_path))
    console.print(table)


async def run(args: argparse.Namespace) -> None:
    """Main entry point."""
    task_spec = load_task_spec(args.task)
    evidence_dir = config.EVIDENCE_DIR / f"run_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    headless = args.headless if args.headless is not None else config.HEADLESS
    max_concurrent = args.concurrency or config.MAX_CONCURRENT

    console.print()
    console.print("[bold]Browser Evidence Agent[/bold]")
    console.print(f"  Task:        {task_spec.task_id}")
    console.print(f"  Model:       {config.LLM_MODEL}")
    console.print(f"  Concurrency: {max_concurrent}")
    console.print(f"  Headless:    {headless}")

    # Load samples from input CSV or single --url
    if args.input:
        samples = load_samples(args.input)
        console.print(f"  Samples:     {len(samples)} (from {args.input})")
    elif args.url:
        sample_id = args.id or "sample_001"
        samples = [SampleInput(sample_id=sample_id, url=args.url)]
        console.print(f"  Sample:      {sample_id} ({args.url})")
    else:
        console.print("[red]Error: Provide --input CSV or --url[/red]")
        return

    console.print()
    await run_batch(task_spec, samples, evidence_dir, max_concurrent, headless)


def main():
    parser = argparse.ArgumentParser(
        description="Browser Evidence Agent — collect structured evidence from any website"
    )
    parser.add_argument("--task", required=True, help="Path to task spec JSON")
    parser.add_argument("--input", help="Path to samples CSV")
    parser.add_argument("--url", help="Single sample URL (use with --id)")
    parser.add_argument("--id", help="Sample ID for single URL mode")
    parser.add_argument("--concurrency", type=int, help=f"Max parallel browsers (default: {config.MAX_CONCURRENT})")
    parser.add_argument("--headless", action="store_true", default=None, help="Run headless")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Run with visible browser")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
