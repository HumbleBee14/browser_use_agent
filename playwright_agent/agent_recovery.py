"""Smart termination, escalating stagnation notices, final consolidation."""

from __future__ import annotations

import copy
import json
import re
from typing import TYPE_CHECKING

from anthropic import AsyncAnthropic

import config
from agent_merge import deep_merge

if TYPE_CHECKING:
    from models.task import TaskSpec

INFRA_ERROR_PATTERNS = [
    "timeout", "net::err", "dns", "connection refused", "connection reset",
    "network error", "err_connection", "err_name_not_resolved",
    "err_internet_disconnected", "page crashed", "target closed",
    "browser has been closed", "navigation error", "ssl",
]


def is_infra_error(error_text: str) -> bool:
    """Distinguish infrastructure errors from logic errors (element not found)."""
    lower = error_text.lower()
    return any(p in lower for p in INFRA_ERROR_PATTERNS)


def check_termination(
    step: int,
    elapsed: float,
    task_spec: TaskSpec,
    accumulated: dict,
    items_collected: int,
    network_errors: int,
    effective_max: int,
    log,
) -> tuple[str, str] | None:
    """Return (status, reason) if the agent should stop early, else None."""
    if task_spec.max_time_seconds > 0 and elapsed > task_spec.max_time_seconds:
        has_data = bool(accumulated)
        status = "partial_success" if has_data else "failed"
        return status, f"Wall-clock timeout ({elapsed:.0f}s > {task_spec.max_time_seconds}s limit)"

    limit = task_spec.max_consecutive_network_errors
    if network_errors >= limit:
        has_data = bool(accumulated)
        status = "partial_success" if has_data else "failed"
        return status, f"Network circuit breaker: {network_errors} consecutive infrastructure errors"

    if task_spec.expected_items > 0 and items_collected >= task_spec.expected_items:
        for array_field in accumulated.values():
            if isinstance(array_field, list) and len(array_field) >= task_spec.expected_items:
                return None

    remaining = effective_max - step
    if remaining == 5 and accumulated:
        log.info(f"Step {step} | 5 steps remaining with accumulated data — agent should wrap up soon")

    return None


def build_recovery_notice(
    step: int,
    effective_max: int,
    steps_since_data: int,
    stagnation_count: int,
    stagnation_level: int,
    consecutive_failures: int,
    loop_counter: dict,
    current_url: str,
    has_accumulated: bool = False,
) -> tuple[int, str] | None:
    """Escalating recovery: return (level, message) or None."""
    remaining = effective_max - step
    data_hint = " You already have partial data — consider done or save_progress." if has_accumulated else ""

    if stagnation_count >= 8 and stagnation_level < 3:
        return 3, (
            f"CRITICAL: You have been stuck on the same page for {stagnation_count} steps "
            f"with no new data. You MUST call done with whatever data you have, "
            f"or call fail with a reason. No more browsing."
        )

    if stagnation_count >= 5 and stagnation_level < 2:
        return 2, (
            f"WARNING: Same page state for {stagnation_count} steps, no new data in "
            f"{steps_since_data} steps. CHANGE YOUR STRATEGY NOW. "
            f"Options: navigate to a different page, try different selectors, "
            f"or call done/save_progress with what you have. {remaining} steps left."
            f"{data_hint}"
        )

    if (stagnation_count >= 3 and stagnation_level < 1) or (
        steps_since_data >= 5 and stagnation_level < 1
    ):
        return 1, (
            f"You have not produced new data in {steps_since_data} steps. "
            f"The page state appears unchanged. Try a different approach: "
            f"navigate elsewhere, scroll to find new content, or extract data you can see. "
            f"{remaining} steps remaining."
        )

    spam_actions = {"screenshot", "goto", "extract", "scroll"}
    if len(loop_counter) > 0:
        for (url, act_name), count in loop_counter.items():
            if count >= 4 and url == current_url and act_name in spam_actions:
                return 1, (
                    f"You have called '{act_name}' {count} times on this URL. "
                    f"STOP repeating this action and try something different."
                )

    return None


async def attempt_final_consolidation(
    client: AsyncAnthropic,
    task_spec: TaskSpec,
    accumulated: dict,
    progress_notes: list[str],
    progress: dict,
    page_state: str,
    prefer_fallback: bool = False,
) -> dict | None:
    """One last LLM call for best-effort structured output after failure or exhaustion."""
    acc_text = json.dumps(accumulated, indent=2, default=str)[:4000]
    notes_text = "\n".join(progress_notes[-5:]) if progress_notes else "No notes."
    schema_text = json.dumps(task_spec.output_schema, indent=2) if task_spec.output_schema else "{}"

    prompt = (
        f"You are finalizing a browser evidence collection task that ran out of steps.\n\n"
        f"## Task goal\n{task_spec.goal}\n\n"
        f"## Output schema\n{schema_text}\n\n"
        f"## Data collected so far\n```json\n{acc_text}\n```\n\n"
        f"## Progress notes\n{notes_text}\n\n"
        f"## Current page\n{page_state[:1000]}\n\n"
        f"Produce the BEST POSSIBLE structured output matching the output schema "
        f"using the data collected so far. Return ONLY a valid JSON object. "
        f"Include all fields from the schema, using collected data where available "
        f"and null for fields that could not be collected."
    )

    models_to_try = []
    if prefer_fallback and config.ENABLE_FALLBACK_LLM:
        models_to_try = [config.FALLBACK_LLM_MODEL, config.LLM_MODEL]
    elif config.ENABLE_FALLBACK_LLM:
        models_to_try = [config.LLM_MODEL, config.FALLBACK_LLM_MODEL]
    else:
        models_to_try = [config.LLM_MODEL]

    for model in models_to_try:
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                result = json.loads(json_match.group())
                # Start from checkpoint data, then layer LLM consolidation on top
                # (LLM output wins on key conflicts — it structured the collected facts).
                merged = copy.deepcopy(accumulated)
                deep_merge(merged, result)
                return merged
        except Exception:
            continue
    return None
