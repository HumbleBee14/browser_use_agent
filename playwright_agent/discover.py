"""Discovery — Phase 1 of the two-phase execution model.

One sequential browser session. Navigates a start URL, paginates through it,
and writes samples.csv (the work queue for Phase 2).

The discovery agent uses the same agent_loop as execution, but with a
discovery-phase task spec that tells the LLM to collect sample URLs
instead of extracting evidence.

Usage (standalone):
    python discover.py --task tasks/github_discovery.json --start-url https://github.com/orgs/microsoft/people --output samples.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright
from rich.console import Console

import agent_loop
import config
from models.task import TaskSpec, SampleInput, load_task_spec
from tools.output import OutputManager

console = Console()


async def discover(
    task_spec: TaskSpec,
    start_url: str,
    output_csv: Path,
    headless: bool = False,
    max_pages: int = 50,
) -> list[SampleInput]:
    """Run discovery: navigate start URL, paginate, collect sample URLs.

    The discovery task spec tells the LLM to extract a list of samples
    (e.g., member URLs from an org page). The agent uses the same loop
    but outputs to a temporary evidence folder.

    Returns list of discovered SampleInput objects.
    """
    console.print(f"  [dim]Discovery:[/dim] {start_url}")
    console.print(f"  [dim]Task:[/dim] {task_spec.task_id}")
    console.print()

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=headless,
        args=["--disable-features=WebContentsForceDark"],
    )
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        color_scheme="light",
    )
    page = await ctx.new_page()

    # Use a temp evidence dir for discovery
    discovery_dir = config.EVIDENCE_DIR / "_discovery"
    discovery_dir.mkdir(parents=True, exist_ok=True)
    output_mgr = OutputManager(discovery_dir, "discovery")

    # Create a sample pointing to the start URL
    sample = SampleInput(sample_id="discovery", url=start_url)

    try:
        await agent_loop.run(page, sample, task_spec, output_mgr)
    except Exception as e:
        console.print(f"[red]Discovery error: {e}[/red]")

    await ctx.close()
    await browser.close()
    await p.stop()

    # Read the discovery result
    result_path = discovery_dir / "discovery" / "result.json"
    samples = []

    if result_path.exists():
        data = json.loads(result_path.read_text(encoding="utf-8"))
        extracted = data.get("extracted", {})

        # The discovery spec outputs a "members" array or similar
        # Try common keys: members, samples, urls, items
        items = []
        for key in ("members", "samples", "urls", "items", "results"):
            if key in extracted and isinstance(extracted[key], list):
                items = extracted[key]
                break

        # If the extracted data is a flat list of URLs/dicts
        if not items and isinstance(extracted, list):
            items = extracted

        seen = set()
        for item in items:
            if isinstance(item, dict):
                sid = item.get("username") or item.get("id") or item.get("sample_id", "")
                url = item.get("url") or item.get("href", "")
            elif isinstance(item, str):
                sid = item.split("/")[-1] if "/" in item else item
                url = item
            else:
                continue

            if not sid or sid in seen:
                continue
            seen.add(sid)
            samples.append(SampleInput(sample_id=sid, url=url))

        console.print(f"  [green]Discovered {len(samples)} samples[/green]")

    # Write samples.csv
    if samples:
        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["sample_id", "url", "discovered_at"])
            writer.writeheader()
            for s in samples:
                writer.writerow({
                    "sample_id": s.sample_id,
                    "url": s.url,
                    "discovered_at": datetime.utcnow().isoformat() + "Z",
                })
        console.print(f"  [dim]Wrote {output_csv}[/dim]")
    else:
        console.print("[yellow]No samples discovered.[/yellow]")

    return samples


async def run(args):
    task_spec = load_task_spec(args.task)
    output_csv = Path(args.output)
    samples = await discover(
        task_spec, args.start_url, output_csv,
        headless=config.HEADLESS,
    )
    console.print(f"\n  Total: {len(samples)} samples written to {output_csv}")


def main():
    parser = argparse.ArgumentParser(description="Discover samples from a start URL")
    parser.add_argument("--task", required=True, help="Path to discovery task spec JSON")
    parser.add_argument("--start-url", required=True, help="URL to start discovery from")
    parser.add_argument("--output", default="samples.csv", help="Output CSV path")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
