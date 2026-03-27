"""Worker — owns one sample's full lifecycle.

Each worker gets an isolated BrowserContext (own cookies, session).
If auth_profile is set, loads storage_state for logged-in sessions.
Exceptions are caught and written to result.json — never crashes the batch.
Semaphore slot always released in finally.
"""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import Browser

import agent_loop
import config
from models.task import TaskSpec, SampleInput
from tools.output import OutputManager


async def run_sample(
    browser: Browser,
    sample: SampleInput,
    task_spec: TaskSpec,
    evidence_dir: Path,
) -> str:
    """Process one sample end-to-end. Returns the sample_id.

    Creates an isolated BrowserContext, runs the agent loop,
    writes evidence files. Never raises — all errors are captured
    in result.json.
    """
    output_mgr = OutputManager(evidence_dir, sample.sample_id)

    # Build context options
    context_opts = {}
    if task_spec.auth_profile:
        auth_path = Path(task_spec.auth_profile)
        if auth_path.exists():
            context_opts["storage_state"] = str(auth_path)

    try:
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            color_scheme="light",
            **context_opts,
        )
        page = await ctx.new_page()
        await page.emulate_media(color_scheme="light")

        try:
            await agent_loop.run(page, sample, task_spec, output_mgr)
        except Exception as e:
            output_mgr.write_result(
                status="failed",
                errors=[f"Worker exception: {str(e)[:300]}"],
                steps=0,
            )
        finally:
            await ctx.close()

    except Exception as e:
        # Context creation itself failed (e.g., bad auth state file)
        output_mgr.write_result(
            status="failed",
            errors=[f"Context creation failed: {str(e)[:300]}"],
            steps=0,
        )

    return sample.sample_id
