"""Token-budget history fitting and user-message construction for the agent loop."""

from __future__ import annotations

import json

from anthropic import AsyncAnthropic

import config
from core import dom_extractor
from models.task import SampleInput, TaskSpec

# Token budget = 8% of model context window, capped at 24K, floor 8K.
# Why 8%: LLMs lose attention on mid-prompt content above ~20% fill ("lost in the middle")
# Cap at 24K: even a 1M-context model doesn't need 80K of prompt for a browser agent step.
# NOTE (To my code Readers :): If your instinct is "the model is smart, just feed it the whole mess,"
# that is not sophistication, it is laziness. A large context window is not a
# license to dump garbage upstream. Efficiency starts at the ground level:
# prune early, keep signal high, and let the model choose from the best options,
# not sift through noise like an unpaid intern.
PROMPT_TOKEN_BUDGET = min(24_000, max(8_000, int(config.LLM_CONTEXT_WINDOW * 0.08)))
HISTORY_TOKEN_SHARE = 0.30
MIN_HISTORY_ITEMS = 5
MAX_HISTORY_ITEMS = 25

_IMPORTANCE: dict[str, int] = {
    "save_progress": 3,
    "done": 3,
    "fail": 3,
    "extract": 2,
    "screenshot": 1,
    "system_notice": 2,
    "click": 1,
    "goto": 1,
    "type": 1,
    "scroll": 0,
    "wait": 0,
}


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English/code mix."""
    return max(1, len(text) // 4)


def fit_history(full_history: list[dict], fixed_tokens: int) -> list[dict]:
    """Select history items that fit within the token budget."""
    remaining_capacity = max(0, PROMPT_TOKEN_BUDGET - fixed_tokens)
    budget = max(500, int(remaining_capacity * HISTORY_TOKEN_SHARE))

    if not full_history:
        return []

    recency_window = full_history[-MIN_HISTORY_ITEMS:]
    recency_tokens = sum(estimate_tokens(json.dumps(h, default=str)) for h in recency_window)

    remaining_budget = budget - recency_tokens
    if remaining_budget <= 0 or len(full_history) <= MIN_HISTORY_ITEMS:
        return recency_window

    older = full_history[:-MIN_HISTORY_ITEMS]
    scored = []
    for i, item in enumerate(older):
        action = item.get("action", "")
        importance = _IMPORTANCE.get(action, 0)
        recency_bonus = i / max(len(older), 1)
        score = importance + recency_bonus
        tokens = estimate_tokens(json.dumps(item, default=str))
        scored.append((score, tokens, item))

    scored.sort(key=lambda x: x[0], reverse=True)

    selected_older = []
    tokens_used = 0
    for score, tokens, item in scored:
        if tokens_used + tokens > remaining_budget:
            continue
        selected_older.append(item)
        tokens_used += tokens
        if len(selected_older) + len(recency_window) >= MAX_HISTORY_ITEMS:
            break

    selected_older.sort(key=lambda x: x.get("step", 0))
    return selected_older + recency_window


async def summarize_steps(
    client: AsyncAnthropic,
    steps: list[dict],
    goal: str,
    log,
) -> str:
    """Structured FOUND/GAPS/NEXT summary via fast model, with mechanical fallback."""
    step_text = "\n".join(
        f"Step {h['step']}: {h['action']}({json.dumps(h.get('params', {}), default=str)[:80]}) → {h.get('result', '')[:80]}"
        for h in steps
    )
    try:
        response = await client.messages.create(
            model=config.LLM_FAST_MODEL,
            max_tokens=250,
            messages=[{
                "role": "user",
                "content": (
                    f"Summarize these browser agent steps in a structured format. "
                    f"Task goal: {goal}\n\nSteps:\n{step_text}\n\n"
                    f"Reply in EXACTLY this format (3 lines, no extra text):\n"
                    f"FOUND: <what data/evidence was collected>\n"
                    f"GAPS: <what is still missing or incomplete>\n"
                    f"NEXT: <best next action to make progress>"
                ),
            }],
        )
        return f"Steps {steps[0]['step']}-{steps[-1]['step']}:\n{response.content[0].text.strip()}"
    except Exception as e:
        log.debug(f"LLM summary failed, using mechanical fallback: {str(e)[:100]}")
        actions = "; ".join(f"s{h['step']}:{h['action']}" for h in steps)
        return f"Steps {steps[0]['step']}-{steps[-1]['step']}: {actions}"


def build_messages(
    page_state: str,
    vision_text: str,
    history: list[dict],
    task_spec: TaskSpec,
    sample: SampleInput,
    snap: dom_extractor.DOMSnapshot,
    consecutive_failures: int,
    loop_counter: dict,
    progress: dict | None = None,
    accumulated: dict | None = None,
    step_summaries: list[str] | None = None,
    current_step: int = 0,
    memory_hints: str | None = None,
    effective_max: int | None = None,
) -> list[dict]:
    """Build the user message list for the LLM call."""
    parts = []

    parts.append(f"## Current page state\n{page_state}")

    if vision_text:
        parts.append(f"\n## Visual analysis (DOM was insufficient)\n{vision_text}")

    budget = effective_max if effective_max else task_spec.max_steps
    if current_step > 0:
        remaining = budget - current_step
        parts.append(f"\n**Step {current_step} of {budget}** ({remaining} remaining)")

    if accumulated:
        acc_text = json.dumps(accumulated, indent=2, default=str)
        if len(acc_text) > 2000:
            acc_text = acc_text[:2000] + "\n... (truncated)"
        parts.append(f"\n## Data collected so far (via save_progress)\n```json\n{acc_text}\n```")

    if step_summaries:
        parts.append(f"\n## Earlier steps (condensed)\n" + "\n".join(step_summaries[-3:]))

    if progress and any(progress.values()):
        progress_lines = []
        if progress.get("pages_visited"):
            progress_lines.append(f"Pages visited: {', '.join(progress['pages_visited'][-10:])}")
        if progress.get("artifacts"):
            progress_lines.append(f"Screenshots taken: {', '.join(progress['artifacts'])}")
        if progress.get("fields_found"):
            progress_lines.append(f"Data extracted so far: {', '.join(progress['fields_found'][-5:])}")
        if progress.get("failed_urls"):
            progress_lines.append(f"FAILED URLs (skip these): {', '.join(progress['failed_urls'][-5:])}")
        if progress.get("blocked_selectors"):
            progress_lines.append(f"BROKEN selectors (don't retry): {', '.join(progress['blocked_selectors'][-5:])}")
        if progress.get("dead_ends"):
            progress_lines.append(f"DEAD ENDS (tried, didn't work): {', '.join(progress['dead_ends'][-3:])}")
        if progress.get("exhausted_pages"):
            progress_lines.append(f"Exhausted pages (all data taken): {', '.join(progress['exhausted_pages'][-5:])}")
        if progress_lines:
            parts.append(f"\n## Run state\n" + "\n".join(progress_lines))

    if history:
        history_lines = []
        show_reflection = config.REFLECTION_MODE == "full"
        for h in history:
            line = f"Step {h['step']}: {h['action']} → {h.get('result', '')[:100]}"
            if show_reflection:
                if h.get("memory"):
                    line += f" [mem: {h['memory'][:60]}]"
                if h.get("goal"):
                    line += f" [goal: {h['goal'][:60]}]"
            history_lines.append(line)
        parts.append(f"\n## Action history ({len(history)} items, budget-fitted)\n" + "\n".join(history_lines))

    if consecutive_failures >= 3:
        interactive = [
            f"[{n.index}] [{n.role}] \"{n.name}\""
            for n in snap.nodes
            if n.role in dom_extractor.INTERACTIVE_ROLES and n.name
        ]
        if interactive:
            parts.append(
                f"\n[RECOVERY] {consecutive_failures} consecutive failures. "
                f"Here are all visible interactive elements:\n"
                + "\n".join(interactive[:15])
            )

    sample_info = f"SAMPLE: ID={sample.sample_id}"
    if sample.url:
        sample_info += f", URL={sample.url}"
    if sample.extra:
        sample_info += f", Extra={json.dumps(sample.extra)}"
    parts.append(f"\n## Sample\n{sample_info}")

    if memory_hints and current_step <= 3:
        parts.append(f"\n{memory_hints}")

    parts.append(f"\n## Goal\n{task_spec.goal}")

    if task_spec.output_schema:
        schema_text = json.dumps(task_spec.output_schema, indent=2)
        parts.append(f"\n## Output schema (populate when calling done)\n{schema_text}")

    if task_spec.required_fields:
        parts.append(f"\nRequired fields (must be non-empty in done): {task_spec.required_fields}")

    if task_spec.judgment_required:
        parts.append(
            f"\n## Judgment required\nQuestion: {task_spec.judgment_question}\n"
            f"Include judgment fields in your done() extracted data: "
            f"{json.dumps(task_spec.judgment_output_schema)}"
        )

    if config.REFLECTION_MODE == "full":
        parts.append(
            "\nTake the single best next action. "
            "Include evaluation_previous_step (did last action work?), "
            "memory_update (key fact to carry forward), and "
            "next_goal (what you intend to accomplish). Keep each to one sentence."
        )
    else:
        parts.append("\nTake the single best next action.")

    return [{"role": "user", "content": "\n".join(parts)}]
