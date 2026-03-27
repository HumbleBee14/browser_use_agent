"""Quick single-sample runner for testing the agent loop.

Usage:
    python run_single.py --task tasks/github_profile.json --url https://github.com/torvalds --id test_001
    python run_single.py --task tasks/github_profile.json --url https://github.com/torvalds
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright
from rich.console import Console

import agent_loop
import config
from models.task import load_task_spec, SampleInput
from tools.output import OutputManager

console = Console()


async def run(args):
    task_spec = load_task_spec(args.task)
    sample_id = args.id or "sample_001"
    sample = SampleInput(sample_id=sample_id, url=args.url)

    evidence_dir = config.EVIDENCE_DIR
    evidence_dir.mkdir(parents=True, exist_ok=True)

    output_mgr = OutputManager(evidence_dir, sample_id)

    console.print(f"[bold green]Agent Loop Test[/bold green]")
    console.print(f"  Task:   {task_spec.task_id}")
    console.print(f"  URL:    {args.url}")
    console.print(f"  Sample: {sample_id}")
    console.print(f"  Model:  {config.LLM_MODEL}")
    console.print(f"  Steps:  max {task_spec.max_steps}")
    console.print()

    p = await async_playwright().start()
    browser_instance = await p.chromium.launch(
        headless=config.HEADLESS,
        args=["--disable-features=WebContentsForceDark"],
    )
    # Load auth state if task spec has auth_profile
    context_opts = {}
    if task_spec.auth_profile:
        auth_path = Path(task_spec.auth_profile)
        if auth_path.exists():
            context_opts["storage_state"] = str(auth_path)
            console.print(f"  Auth:   {auth_path}")

    ctx = await browser_instance.new_context(
        viewport={"width": 1280, "height": 900},
        color_scheme="light",
        **context_opts,
    )
    page = await ctx.new_page()
    await page.emulate_media(color_scheme="light")

    try:
        await agent_loop.run(page, sample, task_spec, output_mgr)
    except Exception as e:
        console.print(f"[red]Agent error: {e}[/red]")
        output_mgr.write_result(status="failed", errors=[str(e)])
    finally:
        await ctx.close()
        await browser_instance.close()
        await p.stop()

    # Print results
    result_path = evidence_dir / sample_id / "result.json"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        console.print()
        console.print(f"[bold]Result: {result['status']}[/bold]")
        console.print(f"  Steps:     {result.get('steps', 0)}")
        console.print(f"  Artifacts: {len(result.get('artifacts', []))}")
        if result.get("extracted"):
            console.print(f"  Extracted:")
            for k, v in result["extracted"].items():
                console.print(f"    {k}: {v}")
        if result.get("errors"):
            console.print(f"  [red]Errors: {result['errors']}[/red]")
        console.print(f"\n  Output: {evidence_dir / sample_id}")


def main():
    parser = argparse.ArgumentParser(description="Run agent on a single sample")
    parser.add_argument("--task", required=True, help="Path to task spec JSON")
    parser.add_argument("--url", required=True, help="URL to navigate to")
    parser.add_argument("--id", default=None, help="Sample ID (default: sample_001)")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
