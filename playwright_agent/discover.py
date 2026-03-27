"""Discovery — Phase 1 of the two-phase execution model.

One sequential browser session. Navigates a start URL, paginates through it,
and writes samples.csv (the work queue for Phase 2).

Uses the same agent_loop as execution but with a discovery-phase task spec.

Usage (standalone):
    python discover.py --task tasks/github_discovery.json --start-url https://github.com/orgs/microsoft/people --output samples.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from playwright.async_api import async_playwright
from rich.console import Console

import agent_loop
import config
from log_setup import logger
from models.task import TaskSpec, SampleInput, load_task_spec
from tools.output import OutputManager

console = Console()


async def discover(
    task_spec: TaskSpec,
    start_url: str,
    output_csv: Path,
    headless: bool = False,
) -> list[SampleInput]:
    """Run discovery: navigate start URL, paginate, collect samples.

    Returns list of discovered SampleInput objects and writes samples.csv.
    """
    log = logger.bind(sample_id="discovery")
    log.info(f"Discovery started | url={start_url} | task={task_spec.task_id}")

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

    # Use a unique discovery dir — clear any stale results first
    discovery_dir = config.EVIDENCE_DIR / "_discovery"
    if discovery_dir.exists():
        shutil.rmtree(discovery_dir)
    discovery_dir.mkdir(parents=True, exist_ok=True)

    output_mgr = OutputManager(discovery_dir, "discovery")
    sample = SampleInput(sample_id="discovery", url=start_url)

    try:
        await agent_loop.run(page, sample, task_spec, output_mgr)
    except Exception as e:
        log.error(f"Discovery error: {e}")
        console.print(f"[red]Discovery error: {e}[/red]")

    await ctx.close()
    await browser.close()
    await p.stop()

    # Read the discovery result
    result_path = discovery_dir / "discovery" / "result.json"
    samples = []

    if not result_path.exists():
        console.print("[yellow]No discovery result produced.[/yellow]")
        return samples

    data = json.loads(result_path.read_text(encoding="utf-8"))
    if data.get("status") not in ("done", "needs_review"):
        console.print(f"[yellow]Discovery status: {data.get('status')}[/yellow]")
        return samples

    extracted = data.get("extracted", {})

    # Find the list of discovered items — try common keys
    items = []
    for key in ("members", "samples", "urls", "items", "results"):
        if key in extracted and isinstance(extracted[key], list):
            items = extracted[key]
            break

    if not items and isinstance(extracted, list):
        items = extracted

    # Build SampleInput objects, preserving all fields from each item
    seen = set()
    for item in items:
        if isinstance(item, dict):
            sid = (
                item.get("username") or item.get("id") or item.get("sample_id")
                or item.get("name", "").replace(" ", "_").lower()
                or f"sample_{len(seen) + 1}"
            )
            url = item.get("url") or item.get("href", "")
            # Preserve all extra fields (name, company, etc.)
            extra = {k: v for k, v in item.items() if k not in ("username", "id", "sample_id", "url", "href")}
        elif isinstance(item, str):
            sid = item.split("/")[-1] if "/" in item else item
            url = item
            extra = {}
        else:
            continue

        if not sid:
            continue
        # Collision-safe: append _2, _3, etc. instead of dropping duplicates
        original_sid = sid
        counter = 1
        while sid in seen:
            counter += 1
            sid = f"{original_sid}_{counter}"
        seen.add(sid)
        samples.append(SampleInput(sample_id=sid, url=url, extra=extra))

    log.info(f"Discovery complete | samples={len(samples)}")
    console.print(f"  [green]Discovered {len(samples)} samples[/green]")

    # Write samples.csv — include all fields (sample_id, url, + any extras)
    if samples:
        # Collect all unique extra field names across all samples
        extra_keys = set()
        for s in samples:
            extra_keys.update(s.extra.keys())
        extra_keys = sorted(extra_keys)

        fieldnames = ["sample_id", "url", "discovered_at"] + extra_keys

        with open(output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for s in samples:
                row = {
                    "sample_id": s.sample_id,
                    "url": s.url,
                    "discovered_at": datetime.utcnow().isoformat() + "Z",
                    **s.extra,
                }
                writer.writerow(row)
        console.print(f"  [dim]Wrote {output_csv} ({len(fieldnames)} columns)[/dim]")
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
