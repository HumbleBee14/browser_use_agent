"""CLI entry point for the Browser Evidence Agent.

Usage:
    # Run a full batch from task YAML
    python main.py --task tasks/github_commits.yaml

    # Run a single sample (quick test)
    python main.py --task tasks/github_commits.yaml --sample-id test_001 --url https://github.com/...

    # Options
    python main.py --task tasks/demo.yaml --headless --max-concurrent 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

import config
from agent.orchestrator import BatchOrchestrator
from agent.task_loader import load_samples, load_task_config
from models.evidence import SampleStatus
from models.task import SampleInput

console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with rich handler."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def create_llm(provider: str | None = None, model: str | None = None):
    """Create an LLM instance via the provider-agnostic factory."""
    from agent.llm import create_llm as llm_factory

    try:
        return llm_factory(provider=provider, model=model)
    except ValueError as e:
        console.print(f"[red]ERROR: {e}[/red]")
        console.print(
            "[dim]Check your .env file. See .env.example for supported providers.[/dim]"
        )
        sys.exit(1)


def print_banner(task_name: str, sample_count: int, strategy: str, llm) -> None:
    """Print a startup banner with run details."""
    console.print()
    console.rule("[bold blue]Browser Evidence Agent[/bold blue]")
    console.print(f"  Task:     [cyan]{task_name}[/cyan]")
    console.print(f"  Strategy: [cyan]{strategy}[/cyan]")
    console.print(f"  Samples:  [cyan]{sample_count}[/cyan]")
    console.print(f"  Provider: [cyan]{getattr(llm, 'provider', type(llm).__name__)}[/cyan]")
    console.print(f"  Model:    [cyan]{getattr(llm, 'name', 'unknown')}[/cyan]")
    console.rule()
    console.print()


def print_results(batch_result) -> None:
    """Print a summary table of batch results."""
    console.print()
    console.rule("[bold green]Run Complete[/bold green]")

    # Summary stats
    table = Table(title="Batch Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Total Samples", str(batch_result.total_samples))
    table.add_row("Completed", f"[green]{batch_result.completed}[/green]")
    table.add_row("Failed", f"[red]{batch_result.failed}[/red]")
    table.add_row("Needs Review", f"[yellow]{batch_result.needs_review}[/yellow]")
    table.add_row(
        "Duration", f"{batch_result.total_duration_seconds:.1f}s"
    )
    table.add_row("Output", batch_result.run_dir)
    console.print(table)

    # Per-sample details if there are issues
    issues = [
        r
        for r in batch_result.results
        if r.status != SampleStatus.COMPLETED
    ]
    if issues:
        console.print()
        issue_table = Table(title="Samples Needing Attention")
        issue_table.add_column("Sample ID", style="cyan")
        issue_table.add_column("Status", style="yellow")
        issue_table.add_column("Reasons")
        issue_table.add_column("Errors")

        for r in issues:
            reasons = ", ".join(rr.value for rr in r.needs_review_reasons) or "-"
            errors = "; ".join(r.errors[:2]) or "-"
            status_color = "red" if r.status == SampleStatus.FAILED else "yellow"
            issue_table.add_row(
                r.sample_id,
                f"[{status_color}]{r.status.value}[/{status_color}]",
                reasons,
                errors[:80],
            )
        console.print(issue_table)

    console.print()


def run_dry_run(task, samples) -> None:
    """Validate config and show what would run — no browser, no LLM calls."""
    console.print()
    console.rule("[bold blue]Browser Evidence Agent — Dry Run[/bold blue]")
    console.print()

    # Task config
    task_table = Table(title="Task Configuration")
    task_table.add_column("Setting", style="cyan")
    task_table.add_column("Value", style="white")
    task_table.add_row("Name", task.name)
    task_table.add_row("Strategy", task.strategy)
    task_table.add_row("Max Steps", str(task.max_steps))
    task_table.add_row("Timeout", f"{task.timeout_seconds}s")
    task_table.add_row("Max Retries", str(task.max_retries))
    task_table.add_row("Vision Mode", task.use_vision)
    task_table.add_row("Judgment", task.judgment_question or "none")
    console.print(task_table)

    # Output fields
    if task.output_fields:
        console.print()
        fields_table = Table(title="Output Fields")
        fields_table.add_column("Field", style="cyan")
        fields_table.add_column("Type", style="white")
        fields_table.add_column("Required", style="white")
        for f in task.output_fields:
            req = "[green]yes[/green]" if f.required else "[dim]no[/dim]"
            fields_table.add_row(f.name, f.type, req)
        console.print(fields_table)

    # Checkpoints
    if task.checkpoints:
        console.print()
        cp_table = Table(title="Evidence Checkpoints")
        cp_table.add_column("Checkpoint", style="cyan")
        cp_table.add_column("Type", style="white")
        cp_table.add_column("Required", style="white")
        for c in task.checkpoints:
            req = "[green]yes[/green]" if c.required else "[dim]no[/dim]"
            cp_table.add_row(c.name, c.evidence_type.value, req)
        console.print(cp_table)

    # Samples
    console.print()
    sample_table = Table(title=f"Samples ({len(samples)})")
    sample_table.add_column("ID", style="cyan")
    sample_table.add_column("URL", style="white")
    sample_table.add_column("Extra Fields", style="dim")
    for s in samples:
        extras = ", ".join(f"{k}={v}" for k, v in s.extra_fields.items()) if s.extra_fields else "-"
        sample_table.add_row(s.sample_id, s.url or "-", extras)
    console.print(sample_table)

    console.print()
    console.print("[green]Dry run complete. Config is valid.[/green]")
    console.print()


async def run_batch(args: argparse.Namespace) -> None:
    """Main batch execution flow."""
    # Load task config
    task = load_task_config(args.task)

    # Load or create samples
    if args.sample_id and args.url:
        # Single sample mode (quick test)
        samples = [SampleInput(sample_id=args.sample_id, url=args.url)]
    elif args.sample_id:
        console.print("[red]ERROR: --sample-id requires --url[/red]")
        sys.exit(1)
    else:
        # Batch mode from input file
        input_path = Path(task.input_file)
        if not input_path.is_absolute():
            # Resolve relative to task file directory
            task_dir = Path(args.task).parent
            input_path = task_dir / input_path
        samples = load_samples(input_path, task.input_columns)

    if not samples:
        console.print("[red]ERROR: No samples to process[/red]")
        sys.exit(1)

    # Dry run: validate and show config, then exit
    if args.dry_run:
        run_dry_run(task, samples)
        return

    # Create LLM
    llm = create_llm()

    # Print banner
    print_banner(task.name, len(samples), task.strategy, llm)

    # Override settings from CLI args
    headless = args.headless if args.headless is not None else config.HEADLESS
    max_concurrent = args.max_concurrent or config.MAX_CONCURRENT

    # Run orchestrator
    orchestrator = BatchOrchestrator(
        task_config=task,
        llm=llm,
        max_concurrent=max_concurrent,
        output_base=Path(args.output) if args.output else config.EVIDENCE_DIR,
        headless=headless,
    )

    console.print("[bold green]Running evidence collection...[/bold green]")
    console.print()
    batch_result = await orchestrator.run(samples)

    # Print results
    print_results(batch_result)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Browser Evidence Agent — automated evidence collection for audit workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --task tasks/github_commits.yaml
  python main.py --task tasks/demo.yaml --sample-id test_001 --url https://github.com
  python main.py --task tasks/demo.yaml --headless --max-concurrent 5
        """,
    )
    parser.add_argument(
        "--task", required=True, help="Path to task YAML definition"
    )
    parser.add_argument(
        "--sample-id", help="Run a single sample (requires --url)"
    )
    parser.add_argument("--url", help="URL for single-sample mode")
    parser.add_argument(
        "--output", help=f"Output directory (default: {config.EVIDENCE_DIR})"
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        help=f"Max concurrent samples (default: {config.MAX_CONCURRENT})",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=None,
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate task config and list samples without running (no browser, no LLM)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = parse_args()
    setup_logging(args.verbose)
    asyncio.run(run_batch(args))


if __name__ == "__main__":
    main()
