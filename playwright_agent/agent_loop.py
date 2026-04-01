"""The Agent Loop — the entire brain of the system.

Custom ReAct cycle: OBSERVE → REFLECT → DECIDE → ACT → CHECK → repeat.

Companion modules (`agent_prompt`, `agent_recovery`, `agent_merge`, etc.) hold
helpers; this file owns the ReAct loop orchestration.

Key invariants:
- Claude always returns a typed tool call (tool_choice=any), never prose
- Reflection fields in tools + prompt when REFLECTION_MODE=full; light mode omits them for cost
- History window is dynamic — fits as many recent actions as the token budget allows
- Actions always return ActionResult, never raise
- done/fail terminate the loop — max_steps is the hard ceiling
- Escalating recovery: gentle nudge → forceful demand → forced consolidation
- Budget pressure: 75% warning → 90% urgency → last-step done|fail only
- Final-response-after-failure: one last LLM call to produce best-effort output

Long-horizon support:
- save_progress action: checkpoint partial data without stopping
- Accumulated extraction buffer: merged across save_progress calls
- Step summary: every 10 steps, condense history into a summary
- Live checkpoint.json: updated on every save_progress + every 5 steps
- Progress-aware prompt: shows collected data, pages visited, step budget
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from anthropic import AsyncAnthropic
from playwright.async_api import Page

import config
from agent_dispatch import dispatch as _dispatch
from agent_llm_retry import is_retryable_llm_error as _is_retryable_llm_error
from agent_merge import deep_merge as _deep_merge
from agent_navigation import (
    BATCH_BREAKING_ACTIONS as _BATCH_BREAKING_ACTIONS,
    is_batch_target_stable as _is_batch_target_stable,
    is_dom_stable as _is_dom_stable,
    is_pagination_click as _is_pagination_click,
)
from agent_prompt import (
    MIN_HISTORY_ITEMS,
    PROMPT_TOKEN_BUDGET,
    build_system_blocks as _build_system_blocks,
    build_messages as _build_messages,
    estimate_fixed_prompt_tokens as _estimate_fixed_prompt_tokens,
    fit_history as _fit_history,
    summarize_steps as _summarize_steps,
)
from agent_recovery import (
    attempt_final_consolidation as _attempt_final_consolidation,
    build_recovery_notice as _build_recovery_notice,
    check_termination as _check_termination,
    is_infra_error as _is_infra_error,
)
from core import dom_extractor, vision
from log_setup import logger
from memory import MemoryStore
from models.actions import AgentAction, ActionResult, StepRecord, action_tool_schema
from models.task import SampleInput, TaskSpec
from tools import browser
from tools.output import OutputManager

# Module-level singletons — reused across all samples for connection pooling
_client: AsyncAnthropic | None = None
# Memory is now run-scoped — stored in evidence/run_XXXX/memory/
# Each run maintains its own patterns and failures, no cross-run leakage.
_memory_cache: dict[str, MemoryStore] = {}


def _get_memory(evidence_dir: Path) -> MemoryStore:
    """Get or create a MemoryStore scoped to the run's evidence directory.

    Memory is ALWAYS run-scoped — stored in evidence/run_XXXX/memory/.
    Each run creates fresh or updates existing. No cross-run leakage.
    """
    key = str(evidence_dir.resolve())
    if key not in _memory_cache:
        memory_dir = evidence_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        _memory_cache[key] = MemoryStore(memory_dir=memory_dir)
    return _memory_cache[key]


def clear_memory_cache(evidence_dir: Path | None = None) -> None:
    """Drop cached run-scoped MemoryStore instances.

    This matters when the same Python process handles multiple runs or resumes:
    reloading from disk is safer than carrying possibly stale in-memory state
    forward across logically separate runs.
    """
    if evidence_dir is None:
        _memory_cache.clear()
        return
    _memory_cache.pop(str(evidence_dir.resolve()), None)


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0)
    return _client


def _get_terminal_tools() -> list[dict]:
    """Return tool schema restricted to done + fail (reflection fields match REFLECTION_MODE)."""
    include_refl = config.REFLECTION_MODE == "full"
    schema = action_tool_schema(include_reflection=include_refl)
    return [t for t in schema if t["name"] in ("done", "fail")]


def _serialize_loop_counter(loop_counter: dict[tuple, int]) -> list[dict]:
    """Make tuple-keyed loop counter JSON-serializable for checkpoint resume."""
    items = []
    for (url, action), count in loop_counter.items():
        items.append({"url": url, "action": action, "count": count})
    return items


def _deserialize_loop_counter(items: list[dict] | None) -> dict[tuple, int]:
    """Restore loop counter from checkpoint payload."""
    restored: dict[tuple, int] = {}
    if not items:
        return restored
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        action = item.get("action")
        count = item.get("count", 0)
        if not isinstance(url, str) or not isinstance(action, str):
            continue
        try:
            restored[(url, action)] = int(count)
        except (TypeError, ValueError):
            continue
    return restored


async def run(
    page: Page,
    sample: SampleInput,
    task_spec: TaskSpec,
    output_mgr: OutputManager,
    resume_checkpoint: dict | None = None,
) -> None:
    """Run the agent loop for one sample.

    This is the complete agent. It observes the page, asks Claude what to do,
    executes the action, and repeats until done/fail/max_steps.
    """
    log = logger.bind(sample_id=sample.sample_id)
    log.info(f"Agent loop started | url={sample.url} | max_steps={task_spec.max_steps}")

    client = _get_client()
    # Memory is run-scoped — stored in evidence/run_XXXX/memory/
    # Each run creates or updates its own memory, no cross-run leakage
    evidence_dir = output_mgr.sample_dir.parent  # evidence/run_XXXX/
    memory = _get_memory(evidence_dir)
    tools = action_tool_schema(include_reflection=config.REFLECTION_MODE == "full")
    history: list[dict] = []
    progress: dict = {
        "pages_visited": [],
        "fields_found": [],
        "artifacts": [],
        "failed_urls": [],         # URLs that errored (404, timeout, auth)
        "exhausted_pages": [],     # pages where all useful data was already extracted
        "blocked_selectors": [],   # selectors that failed 2+ times
        "dead_ends": [],           # actions/paths that led nowhere
    }
    _selector_fail_counts: dict[str, int] = {}  # track selector failures for blocked detection
    loop_counter: dict[tuple, int] = {}  # (url, action_name) → count
    consecutive_failures = 0
    step = 0

    # Long-term memory: retrieve navigation hints filtered by task relevance
    memory_hints = memory.get_hints(sample.url, goal=task_spec.goal) if sample.url else None
    if memory_hints:
        log.info(f"Memory loaded | domain hints available ({len(memory_hints)} chars)")
        memory.record_usage(sample.url, goal=task_spec.goal)

    # Long-horizon state
    accumulated: dict = {}           # merged data from save_progress calls
    progress_notes: list[str] = []   # human-readable notes from save_progress
    step_summaries: list[str] = []   # condensed summaries every SUMMARY_INTERVAL steps
    last_summarized_idx = 0          # high-water mark: history items already summarized
    SUMMARY_INTERVAL = 10
    CHECKPOINT_INTERVAL = 5
    last_data_step = 0               # last step that produced new data
    pagination_bonus = 0             # extra steps granted for pagination

    # Smart termination state
    start_time = time.monotonic()
    network_errors = 0               # consecutive infra-level failures (timeout, DNS, etc.)
    items_collected = 0              # count of save_progress calls (proxy for items done)
    seen_screenshot_hashes: set[str] = set()  # detect duplicate screenshots

    # Stagnation detection — tracks repeated identical page states
    _last_page_sig: str = ""         # hash(url + page_state[:2000])
    _stagnation_count: int = 0       # consecutive steps with same page signature + no new data
    _stagnation_level: int = 0       # 0=none, 1=gentle, 2=forceful, 3=forced-consolidation

    # Budget warnings — fire once at each threshold
    _warned_75: bool = False
    _warned_90: bool = False

    resume_state = resume_checkpoint.get("resume_state") if isinstance(resume_checkpoint, dict) else None
    resume_step = 0
    if isinstance(resume_state, dict):
        resume_step = max(0, int(resume_checkpoint.get("step", 0) or 0))
        restored_history = resume_state.get("history")
        if isinstance(restored_history, list):
            history = restored_history

        restored_progress = resume_state.get("progress")
        if isinstance(restored_progress, dict):
            for key in progress:
                value = restored_progress.get(key)
                if isinstance(value, list):
                    progress[key] = value

        restored_accumulated = resume_state.get("accumulated")
        if isinstance(restored_accumulated, dict):
            accumulated = restored_accumulated
        restored_notes = resume_state.get("progress_notes")
        if isinstance(restored_notes, list):
            progress_notes = [str(n) for n in restored_notes]
        restored_summaries = resume_state.get("step_summaries")
        if isinstance(restored_summaries, list):
            step_summaries = [str(s) for s in restored_summaries]

        try:
            last_summarized_idx = max(0, int(resume_state.get("last_summarized_idx", 0) or 0))
        except (TypeError, ValueError):
            last_summarized_idx = 0
        try:
            pagination_bonus = max(0, int(resume_state.get("pagination_bonus", 0) or 0))
        except (TypeError, ValueError):
            pagination_bonus = 0
        try:
            last_data_step = max(0, int(resume_state.get("last_data_step", resume_step) or resume_step))
        except (TypeError, ValueError):
            last_data_step = resume_step
        try:
            items_collected = max(0, int(resume_state.get("items_collected", 0) or 0))
        except (TypeError, ValueError):
            items_collected = 0
        try:
            consecutive_failures = max(0, int(resume_state.get("consecutive_failures", 0) or 0))
        except (TypeError, ValueError):
            consecutive_failures = 0
        try:
            network_errors = max(0, int(resume_state.get("network_errors", 0) or 0))
        except (TypeError, ValueError):
            network_errors = 0
        try:
            _stagnation_count = max(0, int(resume_state.get("stagnation_count", 0) or 0))
        except (TypeError, ValueError):
            _stagnation_count = 0
        try:
            _stagnation_level = max(0, int(resume_state.get("stagnation_level", 0) or 0))
        except (TypeError, ValueError):
            _stagnation_level = 0

        restored_warned_75 = resume_state.get("warned_75")
        if isinstance(restored_warned_75, bool):
            _warned_75 = restored_warned_75
        restored_warned_90 = resume_state.get("warned_90")
        if isinstance(restored_warned_90, bool):
            _warned_90 = restored_warned_90

        restored_hashes = resume_state.get("seen_screenshot_hashes")
        if isinstance(restored_hashes, list):
            seen_screenshot_hashes = {str(h) for h in restored_hashes if h}
        else:
            seen_screenshot_hashes = {a.sha256 for a in output_mgr._artifacts}

        restored_selector_fails = resume_state.get("selector_fail_counts")
        if isinstance(restored_selector_fails, dict):
            hydrated_selector_fails: dict[str, int] = {}
            for k, v in restored_selector_fails.items():
                if not isinstance(k, str):
                    continue
                try:
                    hydrated_selector_fails[k] = int(v)
                except (TypeError, ValueError):
                    continue
            _selector_fail_counts = hydrated_selector_fails

        restored_loop_counter = resume_state.get("loop_counter")
        if isinstance(restored_loop_counter, list):
            loop_counter = _deserialize_loop_counter(restored_loop_counter)

        # Browser-agent timeout should apply to the new live browser session,
        # not charge time spent in the dead session before resume.
        start_time = time.monotonic()

        log.info(
            f"Resume state loaded | step={resume_step} | history={len(history)} | "
            f"summaries={len(step_summaries)} | artifacts={len(output_mgr._artifacts)}"
        )

    effective_max = task_spec.max_steps + pagination_bonus
    if isinstance(resume_state, dict):
        try:
            restored_effective_max = int(resume_state.get("effective_max", effective_max) or effective_max)
            if restored_effective_max >= task_spec.max_steps:
                effective_max = restored_effective_max
        except (TypeError, ValueError):
            pass
        _warned_75 = _warned_75 or (effective_max > 0 and resume_step / effective_max >= 0.75)
        _warned_90 = _warned_90 or (effective_max > 0 and resume_step / effective_max >= 0.90)

    def _build_resume_state(current_step: int) -> dict:
        """Capture enough loop state to continue an interrupted sample."""
        return {
            "step": current_step,
            "current_url": page.url,
            "history": json.loads(json.dumps(history, default=str)),
            "progress": json.loads(json.dumps(progress, default=str)),
            "accumulated": json.loads(json.dumps(accumulated, default=str)),
            "progress_notes": list(progress_notes),
            "step_summaries": list(step_summaries),
            "last_summarized_idx": last_summarized_idx,
            "pagination_bonus": pagination_bonus,
            "effective_max": effective_max,
            "last_data_step": last_data_step,
            "items_collected": items_collected,
            "seen_screenshot_hashes": sorted(seen_screenshot_hashes),
            "selector_fail_counts": dict(_selector_fail_counts),
            "loop_counter": _serialize_loop_counter(loop_counter),
            "consecutive_failures": consecutive_failures,
            "network_errors": network_errors,
            "warned_75": _warned_75,
            "warned_90": _warned_90,
            "stagnation_count": _stagnation_count,
            "stagnation_level": _stagnation_level,
        }

    def _write_live_checkpoint(current_step: int, accumulated_payload: dict, *, status: str = "in_progress") -> None:
        """Write checkpoint plus resume metadata without repeating call-site plumbing."""
        output_mgr.write_checkpoint(
            current_step,
            accumulated_payload,
            progress_notes,
            max_steps=effective_max,
            status=status,
            current_url=page.url,
            resume_state=_build_resume_state(current_step),
        )

    # Navigate to starting URL if provided — fail fast if unreachable
    starting_url = sample.url
    if isinstance(resume_state, dict):
        restored_url = str(resume_state.get("current_url") or "")
        if restored_url:
            starting_url = restored_url
    if starting_url:
        result = await browser.goto(page, starting_url)
        if (not result.success and isinstance(resume_state, dict)
                and sample.url and starting_url != sample.url):
            log.warning(
                f"Resume navigation failed for {starting_url[:120]} — falling back to sample start URL"
            )
            result = await browser.goto(page, sample.url)
        if not result.success:
            log.error(f"Initial navigation failed: {result.error}")
            output_mgr.write_result(
                status="failed",
                errors=[f"Initial navigation failed: {result.error}"],
                steps=0,
            )
            return
        if isinstance(resume_state, dict):
            history.append({
                "step": resume_step,
                "action": "system_notice",
                "is_meta": True,
                "result": (
                    f"Resumed interrupted sample from checkpoint at step {resume_step}. "
                    f"Continue from the restored page state."
                ),
            })
        else:
            history.append({"step": 0, "action": "goto", "url": sample.url,
                            "result": result.description if result.success else result.error})

    for step in range(resume_step + 1, task_spec.max_steps + 200):  # hard ceiling with pagination bonus
        if step > effective_max:
            break

        # ---- 0. SMART TERMINATION CHECKS (before each step) ----
        elapsed = time.monotonic() - start_time
        termination = _check_termination(
            step=step,
            elapsed=elapsed,
            task_spec=task_spec,
            accumulated=accumulated,
            items_collected=items_collected,
            network_errors=network_errors,
            effective_max=effective_max,
            log=log,
        )
        if termination:
            status, reason = termination
            log.warning(f"Smart termination | status={status} | {reason}")
            _write_live_checkpoint(step, accumulated or {}, status=status)
            output_mgr.write_result(
                status=status,
                extracted=accumulated or {},
                errors=[reason],
                notes=progress_notes,
                steps=step,
            )
            if sample.url and status != "done":
                try:
                    memory.learn_failures(sample.url, progress, status, reason=reason)
                except Exception:
                    pass
            return

        # ---- 1. OBSERVE ----
        try:
            snap = await dom_extractor.snapshot(page, task_spec.keywords)
        except Exception as e:
            log.warning(f"Step {step} | DOM snapshot failed: {str(e)[:150]}")
            snap = dom_extractor.DOMSnapshot(
                url=page.url, title="", confidence=0.0, raw_text=f"snapshot error: {str(e)[:100]}"
            )
        page_state = dom_extractor.serialize(snap)

        log.debug(f"Step {step} | DOM confidence={snap.confidence:.2f} | nodes={len(snap.nodes)}")

        # If DOM confidence is low, supplement with vision
        vision_text = ""
        if snap.confidence < 0.6:
            log.debug(f"Step {step} | Vision activated (confidence={snap.confidence:.2f})")
            try:
                screenshot_bytes = await vision.capture_screenshot(page, full_page=False)
                question = vision.build_vision_question(page_state, task_spec.goal)
                vision_text = await vision.analyze_screenshot(screenshot_bytes, question, page_state)
            except Exception:
                vision_text = ""

        # ---- 2. PRE-PROMPT SYSTEM NOTICES ----
        # Inject budget/final-step notices BEFORE prompt construction so the
        # current LLM call can actually react to them.
        budget_ratio = step / effective_max if effective_max > 0 else 0
        if budget_ratio >= 0.75 and not _warned_75:
            _warned_75 = True
            remaining_steps = effective_max - step
            history.append({
                "step": step,
                "action": "system_notice", "is_meta": True,
                "result": (
                    f"BUDGET WARNING: You have used {step}/{effective_max} steps ({int(budget_ratio*100)}%). "
                    f"{remaining_steps} steps remaining. Start consolidating results — "
                    f"call save_progress with collected data, then finalize with done."
                ),
            })
            log.info(f"Step {step} | Budget 75% warning injected")
        if budget_ratio >= 0.90 and not _warned_90:
            _warned_90 = True
            remaining_steps = effective_max - step
            history.append({
                "step": step,
                "action": "system_notice", "is_meta": True,
                "result": (
                    f"URGENT: {remaining_steps} steps left. Save any unsaved data NOW with save_progress, "
                    f"then call done immediately with your best available results. "
                    f"Partial results are far more valuable than exhausting all steps."
                ),
            })
            log.info(f"Step {step} | Budget 90% warning injected")

        # ---- 2b. LAST-STEP TOOL RESTRICTION ----
        step_tools = tools
        if step >= effective_max:
            step_tools = _get_terminal_tools()
            history.append({
                "step": step,
                "action": "system_notice", "is_meta": True,
                "result": (
                    "FINAL STEP. Your ONLY available actions are done and fail. "
                    "Call done with all collected data, or fail with a precise reason. "
                    "No other action is available."
                ),
            })
            log.info(f"Step {step} | Final step — tools restricted to done/fail")

        # ---- 2c. BUILD PROMPT ----
        fixed_tokens = _estimate_fixed_prompt_tokens(
            page_state=page_state,
            vision_text=vision_text,
            task_spec=task_spec,
            sample=sample,
            snap=snap,
            consecutive_failures=consecutive_failures,
            progress=progress,
            accumulated=accumulated,
            step_summaries=step_summaries,
            current_step=step,
            memory_hints=memory_hints,
            effective_max=effective_max,
            step_tools=step_tools,
        )
        fitted_history = _fit_history(history, fixed_tokens)

        messages = _build_messages(
            page_state=page_state,
            vision_text=vision_text,
            history=fitted_history,
            task_spec=task_spec,
            sample=sample,
            snap=snap,
            consecutive_failures=consecutive_failures,
            loop_counter=loop_counter,
            progress=progress,
            accumulated=accumulated,
            step_summaries=step_summaries,
            current_step=step,
            memory_hints=memory_hints,
            effective_max=effective_max,
        )

        # Log everything going into the LLM call — full context for debugging
        log.info(
            f"Step {step} LLM input | "
            f"dom_nodes={len(snap.nodes)} | confidence={snap.confidence:.2f} | "
            f"history_items={len(fitted_history)} | vision={'yes' if vision_text else 'no'}"
        )
        log.debug(f"Step {step} system_prompt | {task_spec.system_prompt[:300]}")
        log.debug(f"Step {step} page_state | {page_state[:500]}")
        log.debug(f"Step {step} history | {json.dumps(fitted_history, default=str)[:500]}")
        if vision_text:
            log.debug(f"Step {step} vision | {vision_text[:300]}")

        # ---- 3. DECIDE (LLM call with prompt caching + retry) ----
        LLM_MAX_RETRIES = 3
        response = None
        for attempt in range(1, LLM_MAX_RETRIES + 1):
            try:
                system_blocks = _build_system_blocks(
                    task_spec.system_prompt,
                    sample,
                    memory_hints,
                    step,
                )

                response = await client.messages.create(
                    model=config.LLM_MODEL,
                    max_tokens=1024,
                    system=system_blocks,
                    messages=messages,
                    tools=step_tools,
                    tool_choice={"type": "any"},
                )
                # Log cache hit/miss for prompt cache verification
                usage = getattr(response, "usage", None)
                if usage:
                    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
                    input_tokens = getattr(usage, "input_tokens", 0) or 0
                    output_tokens = getattr(usage, "output_tokens", 0) or 0
                    log.debug(
                        f"Step {step} tokens | input={input_tokens} output={output_tokens} "
                        f"cache_read={cache_read} cache_create={cache_create}"
                    )
                break
            except Exception as e:
                log.warning(f"Step {step} | LLM attempt {attempt}/{LLM_MAX_RETRIES} failed: {str(e)[:150]}")
                retryable = _is_retryable_llm_error(e)
                if retryable and attempt < LLM_MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
                else:
                    # Try fallback model if configured and error is retryable
                    if config.ENABLE_FALLBACK_LLM and retryable:
                        log.info(f"Step {step} | Trying fallback LLM: {config.FALLBACK_LLM_MODEL}")
                        try:
                            response = await client.messages.create(
                                model=config.FALLBACK_LLM_MODEL,
                                max_tokens=1024,
                                system=system_blocks,
                                messages=messages,
                                tools=step_tools,
                                tool_choice={"type": "any"},
                            )
                            log.info(f"Step {step} | Fallback LLM succeeded")
                            break
                        except Exception as fallback_err:
                            log.error(f"Step {step} | Fallback LLM also failed: {str(fallback_err)[:150]}")

                    log.error(f"Step {step} | LLM failed after all retries: {str(e)[:200]}")

                    # Attempt final consolidation before giving up
                    if config.FINALIZE_ON_FAILURE and accumulated:
                        log.info("Attempting final consolidation after LLM failure...")
                        final_result = await _attempt_final_consolidation(
                            client, task_spec, accumulated, progress_notes, progress, page_state,
                            prefer_fallback=True,
                        )
                        if final_result:
                            _write_live_checkpoint(step, final_result, status="partial_success")
                            output_mgr.write_result(
                                status="partial_success", extracted=final_result,
                                errors=["LLM error — finalized via consolidation"],
                                notes=progress_notes, steps=step,
                            )
                            return

                    output_mgr.log_step(StepRecord(
                        step=step, action="llm_error", result=str(e)[:200], url=page.url,
                    ))
                    final_status = "failed" if not accumulated else "partial_success"
                    _write_live_checkpoint(step, accumulated or {}, status="llm_error")
                    output_mgr.write_result(
                        status=final_status,
                        extracted=accumulated or {},
                        errors=[f"LLM error after {LLM_MAX_RETRIES} retries: {str(e)[:200]}"],
                        steps=step,
                    )
                    if sample.url:
                        try:
                            memory.learn_failures(
                                sample.url, progress, final_status, reason=f"LLM error: {str(e)[:100]}"
                            )
                        except Exception:
                            pass
                    return

        if response is None:
            continue

        # Extract tool call(s) from response
        all_tool_blocks = [b for b in response.content if b.type == "tool_use"]
        if not all_tool_blocks:
            output_mgr.log_step(StepRecord(
                step=step, action="no_tool_call", result="LLM returned no tool call", url=page.url,
            ))
            consecutive_failures += 1
            continue

        tool_block = all_tool_blocks[0]
        _remaining_batch = (
            all_tool_blocks[1:config.MAX_ACTIONS_PER_STEP]
            if config.ENABLE_MULTI_ACTIONS and len(all_tool_blocks) > 1
            else []
        )
        if _remaining_batch:
            log.info(f"Step {step} | Multi-action batch: {len(all_tool_blocks)} actions ({len(_remaining_batch)} queued)")

        # Log Claude's raw response (DEBUG only)
        log.debug(
            f"Step {step} LLM response | tool={tool_block.name} | "
            f"input={json.dumps(tool_block.input, default=str)[:300]}"
        )

        try:
            action = AgentAction(action=tool_block.name, **tool_block.input)
        except Exception as e:
            log.warning(f"Step {step} | Parse error: {str(e)[:200]}")
            output_mgr.log_step(StepRecord(
                step=step, action="parse_error",
                result=f"Invalid action from LLM: {str(e)[:200]}",
                url=page.url,
            ))
            consecutive_failures += 1
            continue

        # Extract thinking from text blocks (if any)
        thinking = ""
        for block in response.content:
            if block.type == "text" and block.text:
                thinking = block.text[:300]
                break

        # Extract structured reflection fields (truncate to prevent token bloat)
        evaluation = (action.evaluation_previous_step or "")[:160]
        mem_update = (action.memory_update or "")[:160]
        next_goal = (action.next_goal or "")[:160]
        if evaluation or mem_update or next_goal:
            log.debug(
                f"Step {step} reflection | eval={evaluation[:80]} | mem={mem_update[:80]} | goal={next_goal[:80]}"
            )

        # ---- 4. ACT (with per-step timeout to prevent hung workers) ----
        ACTION_TIMEOUT = 60  # seconds — generous ceiling for any single browser action
        try:
            action_result = await asyncio.wait_for(
                _dispatch(action, page, snap, output_mgr, seen_screenshot_hashes),
                timeout=ACTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            log.warning(f"Step {step} | Action '{action.action}' timed out after {ACTION_TIMEOUT}s")
            action_result = ActionResult(
                success=False, error=f"Action timed out after {ACTION_TIMEOUT}s"
            )

        result_desc = (action_result.description if action_result.success else action_result.error) or ""

        # Logger: step trace (file only, not console)
        action_params = action.selector or action.url or action.label or action.direction or ""
        log.info(
            f"Step {step} | {action.action}({action_params[:50]}) → "
            f"{'OK' if action_result.success else 'FAIL'}: {result_desc[:120]}"
        )
        # Logger: detailed debug (action payload, thinking, DOM stats)
        log.debug(
            f"Step {step} detail | action={action.model_dump(exclude_none=True)} | "
            f"thinking={thinking[:200]} | dom_nodes={len(snap.nodes)} | confidence={snap.confidence:.2f}"
        )
        if action_result.extracted_text:
            log.debug(f"Step {step} extracted | {action_result.extracted_text[:300]}")

        # Update persistent progress (survives history trimming)
        if action.action == "goto" and action_result.success:
            url_short = (action.url or page.url)[:80]
            if url_short not in progress["pages_visited"]:
                progress["pages_visited"].append(url_short)
        if action.action == "goto" and not action_result.success:
            failed_url = (action.url or "")[:80]
            if failed_url and failed_url not in progress["failed_urls"]:
                progress["failed_urls"].append(failed_url)
        if action.action == "screenshot" and action_result.success:
            progress["artifacts"].append(action.label or "screenshot")
        if action_result.extracted_text:
            progress["fields_found"].append(action_result.extracted_text[:50])

        # Track selector failures → blocked_selectors after 2 failures
        if not action_result.success and action.selector:
            sel = action.selector[:60]
            _selector_fail_counts[sel] = _selector_fail_counts.get(sel, 0) + 1
            if _selector_fail_counts[sel] >= 2 and sel not in progress["blocked_selectors"]:
                progress["blocked_selectors"].append(sel)
                log.info(f"Step {step} | Selector blocked (failed {_selector_fail_counts[sel]}x): {sel}")

        # ---- NETWORK ERROR TRACKING ----
        if not action_result.success and _is_infra_error(action_result.error or ""):
            network_errors += 1
            log.warning(f"Step {step} | Network error #{network_errors}: {action_result.error[:100]}")
        elif action_result.success:
            network_errors = 0  # reset on any successful action

        data_changed = False  # tracks whether save_progress added genuinely new data

        # ---- SAVE_PROGRESS: checkpoint partial data without stopping ----
        if action.action == "save_progress":
            partial = action.extracted or {}
            snapshot_before = json.dumps(accumulated, sort_keys=True, default=str)
            _deep_merge(accumulated, partial)
            snapshot_after = json.dumps(accumulated, sort_keys=True, default=str)
            data_changed = snapshot_before != snapshot_after
            note = action.note or f"Checkpoint at step {step}"
            progress_notes.append(note)
            if data_changed:
                items_collected += 1
                # Mark current page as exhausted (data extracted) so agent knows not to revisit
                current_url = page.url[:80]
                if current_url not in progress["exhausted_pages"]:
                    progress["exhausted_pages"].append(current_url)
            result_desc = f"new_data={data_changed} | {note} | keys={list(partial.keys())}"
            log.info(f"Step {step} | save_progress #{items_collected} | {result_desc}")

            output_mgr.log_step(StepRecord(
                step=step,
                thinking=thinking,
                action="save_progress",
                params=action.model_dump(exclude_none=True, exclude={
                    "action", "evaluation_previous_step", "memory_update", "next_goal",
                }),
                result=result_desc,
                url=page.url,
                evaluation=evaluation,
                memory_update=mem_update,
                next_goal=next_goal,
            ))

            sp_entry: dict = {
                "step": step,
                "action": "save_progress",
                "params": {"note": note, "keys": list(partial.keys())},
                "result": result_desc,
            }
            if mem_update:
                sp_entry["memory"] = mem_update
            if next_goal:
                sp_entry["goal"] = next_goal
            history.append(sp_entry)
            _write_live_checkpoint(step, accumulated)

            # Follow-up system notice based on outcome
            if task_spec.expected_items > 0 and items_collected >= task_spec.expected_items:
                log.info(f"Step {step} | Expected items reached ({items_collected}/{task_spec.expected_items})")
                history.append({
                    "step": step,
                    "action": "system_notice", "is_meta": True,
                    "result": (
                        f"You have collected {items_collected} of {task_spec.expected_items} expected items. "
                        f"All items collected. Call done now with the complete data."
                    ),
                })
            elif not data_changed:
                history.append({
                    "step": step,
                    "action": "system_notice", "is_meta": True,
                    "result": (
                        f"No new data added (duplicate of previously saved data). "
                        f"Stop calling save_progress and take a real action: "
                        f"use goto to navigate, click to interact, or call done if finished."
                    ),
                })
            else:
                remaining_items = ""
                if task_spec.expected_items > 0:
                    remaining_items = f" ({items_collected}/{task_spec.expected_items} items)"
                history.append({
                    "step": step,
                    "action": "system_notice", "is_meta": True,
                    "result": f"Progress saved{remaining_items}. You have {effective_max - step} steps remaining. Keep going.",
                })
            consecutive_failures = 0
            continue

        # ---- EXTRACT → ACCUMULATED BUFFER: extract action feeds long-term memory ----
        if action.action == "extract" and action_result.success and action_result.extracted_text:
            if "extracted_texts" not in accumulated:
                accumulated["extracted_texts"] = []
            accumulated["extracted_texts"].append({
                "step": step,
                "selector": action.selector or "",
                "text": action_result.extracted_text[:500],
            })

        # ---- STEP SUMMARY: LLM-powered condensation every N steps ----
        # Only summarize history items added since the last summary (non-overlapping)
        unsummarized = history[last_summarized_idx:-MIN_HISTORY_ITEMS] if len(history) > MIN_HISTORY_ITEMS else []
        if step > 0 and step % SUMMARY_INTERVAL == 0 and unsummarized:
            summary = await _summarize_steps(client, unsummarized, task_spec.goal, log)
            step_summaries.append(summary)
            last_summarized_idx = len(history) - MIN_HISTORY_ITEMS
            log.info(f"Step {step} | LLM summary generated ({len(unsummarized)} new items) | {summary[:100]}")

        # ---- AUTO-CHECKPOINT: write checkpoint.json every N steps ----
        if step > 0 and step % CHECKPOINT_INTERVAL == 0:
            _write_live_checkpoint(step, accumulated)

        # Log the step (with reflection fields for audit trail)
        output_mgr.log_step(StepRecord(
            step=step,
            thinking=thinking,
            action=action.action,
            params=action.model_dump(exclude_none=True, exclude={
                "action", "evaluation_previous_step", "memory_update", "next_goal",
            }),
            result=action_result.description if action_result.success else (action_result.error or ""),
            url=page.url,
            evaluation=evaluation,
            memory_update=mem_update,
            next_goal=next_goal,
        ))

        # Update history for next prompt (include reflection for context continuity)
        history_entry: dict = {
            "step": step,
            "action": action.action,
            "params": {k: v for k, v in action.model_dump(exclude_none=True).items()
                       if k not in ("action", "evaluation_previous_step", "memory_update", "next_goal")},
            "result": action_result.description if action_result.success else action_result.error,
        }
        if mem_update:
            history_entry["memory"] = mem_update
        if next_goal:
            history_entry["goal"] = next_goal
        history.append(history_entry)

        # ---- 5. CHECK TERMINATION ----
        if action.action == "done":
            extracted = action.extracted or {}
            # Merge accumulated checkpoint data with final extraction
            if accumulated:
                merged = dict(accumulated)
                _deep_merge(merged, extracted)
                extracted = merged

            # Machine-checkable completion: verify required fields (use "is None" not "not" — 0/false are valid)
            missing = [f for f in task_spec.required_fields if f not in extracted or extracted[f] is None]

            # Verify required artifacts — match by label substring in filenames.
            # required_artifacts: ["profile"] matches "01_profile.png"
            # required_artifacts: ["screenshot"] matches "03_screenshot.png"
            missing_artifacts = []
            if task_spec.required_artifacts:
                saved_filenames = [a.filename for a in output_mgr._artifacts]
                for req in task_spec.required_artifacts:
                    if not any(req in fn for fn in saved_filenames):
                        missing_artifacts.append(req)

            if missing or missing_artifacts:
                notice_parts = []
                if missing:
                    notice_parts.append(f"Required fields missing: {missing}")
                if missing_artifacts:
                    notice_parts.append(f"Required artifacts missing: {missing_artifacts}")
                log.warning(f"Step {step} | Incomplete done: {'; '.join(notice_parts)}")

                if step < effective_max:
                    # Bounce back — force agent to try again
                    history.append({
                        "step": step,
                        "action": "system_notice", "is_meta": True,
                        "result": ". ".join(notice_parts) + ". Try again.",
                    })
                    consecutive_failures += 1
                    continue
                else:
                    # Last step — cannot retry. Write needs_review, not done.
                    _write_live_checkpoint(step, extracted or {}, status="needs_review")
                    output_mgr.write_result(
                        status="needs_review", extracted=extracted,
                        errors=notice_parts, steps=step,
                    )
                    return

            # Extract judgment if present
            checkpoint_payload = dict(extracted)
            judgment = None
            if task_spec.judgment_required and task_spec.judgment_output_schema:
                judgment = {
                    k: extracted.pop(k, None)
                    for k in task_spec.judgment_output_schema
                    if k in extracted
                }

            # Determine final status — check expected_items against the primary list only.
            # Primary list = the longest list in extracted (the main collection, not auxiliary lists).
            final_status = "done"
            completion_notes = list(progress_notes)
            if task_spec.expected_items > 0:
                primary_list = max(
                    (v for v in extracted.values() if isinstance(v, list)),
                    key=len, default=None,
                )
                if primary_list is not None and len(primary_list) < task_spec.expected_items:
                    final_status = "partial_success"
                    completion_notes.append(
                        f"Expected {task_spec.expected_items} items but collected {len(primary_list)}"
                    )

            log.info(f"Completed | status={final_status} | steps={step} | fields={len(extracted)} | items={items_collected}")
            _write_live_checkpoint(step, checkpoint_payload, status=final_status)
            output_mgr.write_result(
                status=final_status, extracted=extracted, judgment=judgment or None,
                notes=completion_notes,
                steps=step,
            )

            # Learn from runs — successes become patterns, failures become warnings
            if sample.url:
                try:
                    if final_status == "done":
                        learned = await memory.learn_from_run(
                            client, sample.url, task_spec.goal, history, step, final_status,
                        )
                        if learned:
                            log.info(f"Memory saved | domain pattern learned for {sample.url}")
                    else:
                        learned = memory.learn_failures(
                            sample.url, progress, final_status,
                            reason=completion_notes[-1] if completion_notes else "",
                        )
                        if learned:
                            log.info(f"Memory saved | failure warnings stored for {sample.url}")
                except Exception as e:
                    log.debug(f"Memory save failed (non-critical): {e}")
            return

        if action.action == "fail":
            log.warning(f"Failed | reason={action.note} | steps={step}")
            _write_live_checkpoint(step, accumulated or {}, status="failed")
            output_mgr.write_result(
                status="failed",
                errors=[action.note or "Agent called fail"],
                steps=step,
            )
            # Learn from failure — store dead ends, broken selectors, failed URLs
            if sample.url:
                try:
                    memory.learn_failures(
                        sample.url, progress, "failed",
                        reason=action.note or "Agent called fail",
                    )
                except Exception:
                    pass
            return

        # ---- 5b. MULTI-ACTION BATCH (remaining sub-actions from same LLM call) ----
        _batch_ran = False
        _batch_data_produced = False
        _tracked_action_name = action.action
        _tracked_action_selector = action.selector or action.url or action.label or action.direction or ""
        if _remaining_batch and action.action not in _BATCH_BREAKING_ACTIONS and action_result.success:
            _batch_origin_url = page.url
            _batch_origin_map = dict(snap.element_map)
            _batch_element_count = len(snap.element_map) if hasattr(snap, "element_map") else len(snap.nodes)
            _batch_ran = True

            for _extra_block in _remaining_batch:
                if page.url != _batch_origin_url:
                    log.info(f"Step {step} batch | Aborted: URL changed to {page.url[:80]}")
                    break

                try:
                    _extra = AgentAction(action=_extra_block.name, **_extra_block.input)
                except Exception:
                    break

                if _extra.action in _BATCH_BREAKING_ACTIONS:
                    log.debug(f"Step {step} batch | Skipped batch-breaking '{_extra.action}'")
                    break

                # Refresh DOM for accurate element map
                try:
                    snap = await dom_extractor.snapshot(page, task_spec.keywords)
                except Exception:
                    break

                # Abort if the indexed target no longer maps to the same element.
                if not _is_batch_target_stable(_extra.selector or "", _batch_origin_map, snap.element_map):
                    log.info(
                        f"Step {step} batch | Aborted: selector '{_extra.selector}' no longer maps "
                        f"to the original target after DOM update"
                    )
                    break

                # DOM stability check: abort if interactive elements shifted significantly
                _new_element_count = len(snap.element_map) if hasattr(snap, "element_map") else len(snap.nodes)
                if not _is_dom_stable(_batch_element_count, _new_element_count):
                    log.info(
                        f"Step {step} batch | Aborted: DOM structure changed "
                        f"({_batch_element_count} → {_new_element_count} elements)"
                    )
                    break
                _batch_element_count = _new_element_count

                try:
                    _extra_result = await asyncio.wait_for(
                        _dispatch(_extra, page, snap, output_mgr, seen_screenshot_hashes),
                        timeout=ACTION_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    _extra_result = ActionResult(success=False, error=f"Timed out after {ACTION_TIMEOUT}s")

                _extra_desc = (_extra_result.description if _extra_result.success else _extra_result.error) or ""
                log.info(
                    f"Step {step} batch | {_extra.action}({(_extra.selector or _extra.url or _extra.label or '')[:40]}) "
                    f"→ {'OK' if _extra_result.success else 'FAIL'}: {_extra_desc[:80]}"
                )

                _e_eval = (_extra.evaluation_previous_step or "")[:160]
                _e_mem = (_extra.memory_update or "")[:160]
                _e_goal = (_extra.next_goal or "")[:160]

                output_mgr.log_step(StepRecord(
                    step=step, action=_extra.action,
                    params=_extra.model_dump(exclude_none=True, exclude={
                        "action", "evaluation_previous_step", "memory_update", "next_goal",
                    }),
                    result=_extra_desc, url=page.url,
                    evaluation=_e_eval, memory_update=_e_mem, next_goal=_e_goal,
                ))

                _e_entry: dict = {
                    "step": step, "action": _extra.action,
                    "params": {k: v for k, v in _extra.model_dump(exclude_none=True).items()
                               if k not in ("action", "evaluation_previous_step", "memory_update", "next_goal")},
                    "result": _extra_result.description if _extra_result.success else _extra_result.error,
                }
                if _e_mem:
                    _e_entry["memory"] = _e_mem
                history.append(_e_entry)

                if _extra.action == "screenshot" and _extra_result.success:
                    progress["artifacts"].append(_extra.label or "screenshot")
                if _extra_result.extracted_text:
                    progress["fields_found"].append(_extra_result.extracted_text[:50])
                if _extra.action == "extract" and _extra_result.success and _extra_result.extracted_text:
                    _batch_data_produced = True
                    if "extracted_texts" not in accumulated:
                        accumulated["extracted_texts"] = []
                    accumulated["extracted_texts"].append({
                        "step": step, "selector": _extra.selector or "",
                        "text": _extra_result.extracted_text[:500],
                    })

                # Propagate sub-action result to main loop's tracking
                action_result = _extra_result
                _tracked_action_name = _extra.action
                _tracked_action_selector = _extra.selector or _extra.url or _extra.label or _extra.direction or ""
                if not _extra_result.success:
                    break

            # Refresh page_state after the last executed sub-action so downstream
            # pagination, loop detection, and stagnation logic see the current page.
            try:
                snap = await dom_extractor.snapshot(page, task_spec.keywords)
                page_state = dom_extractor.serialize(snap)
            except Exception as e:
                log.debug(f"Step {step} batch | Final DOM refresh failed: {str(e)[:100]}")

        # ---- 6. LOOP, STAGNATION & FAILURE TRACKING ----
        loop_key = (page.url, _tracked_action_name)
        loop_counter[loop_key] = loop_counter.get(loop_key, 0) + 1

        # Track dead ends using the final executed action for this step.
        if loop_counter.get((page.url, _tracked_action_name), 0) >= 3:
            dead = f"{_tracked_action_name} on {page.url[:60]}"
            if dead not in progress["dead_ends"]:
                progress["dead_ends"].append(dead)
                log.info(f"Step {step} | Dead end detected: {dead}")

        # Use the FINAL action_result (primary or last sub-action) for tracking
        if action_result.success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1

        # ---- AUTO-PAGINATION: detect "next page" clicks and grant bonus steps ----
        if (_tracked_action_name == "click" and action_result.success
                and _is_pagination_click(_tracked_action_selector, action_result.description)):
            pagination_bonus += 3
            effective_max = task_spec.max_steps + pagination_bonus
            log.info(f"Step {step} | Pagination detected → +3 bonus steps (effective_max={effective_max})")

        # ---- WATCHDOG + STAGNATION: unified escalating detection ----
        # Reset watchdog if primary or ANY batched sub-action produced data
        if (action.action == "save_progress" and data_changed) \
                or (action.action == "extract" and action_result.success) \
                or _batch_data_produced:
            last_data_step = step

        # Page signature = hash of (normalized URL + first 2K of DOM text)
        page_sig = hashlib.md5(f"{page.url}|{page_state[:2000]}".encode()).hexdigest()
        if page_sig == _last_page_sig and step - last_data_step > 1:
            _stagnation_count += 1
        else:
            _stagnation_count = 0
            _stagnation_level = 0
        _last_page_sig = page_sig

        steps_since_data = step - last_data_step
        recovery_notice = _build_recovery_notice(
            step=step,
            effective_max=effective_max,
            steps_since_data=steps_since_data,
            stagnation_count=_stagnation_count,
            stagnation_level=_stagnation_level,
            consecutive_failures=consecutive_failures,
            loop_counter=loop_counter,
            current_url=page.url,
            has_accumulated=bool(accumulated),
        )
        if recovery_notice:
            level, message = recovery_notice
            _stagnation_level = max(_stagnation_level, level)
            log.warning(f"Step {step} | Recovery L{level}: {message[:120]}")
            if level >= 2 and accumulated:
                _write_live_checkpoint(step, accumulated, status="stagnation")
            history.append({"step": step, "action": "system_notice", "is_meta": True, "result": message})
            if level >= 1:
                last_data_step = step  # reset to avoid consecutive escalation spam

    # Exhausted max_steps without done/fail — attempt final consolidation
    log.warning(f"Exhausted {effective_max} steps (base={task_spec.max_steps}, pagination_bonus={pagination_bonus})")

    if config.FINALIZE_ON_FAILURE and accumulated:
        log.info("Attempting final consolidation call...")
        final_result = await _attempt_final_consolidation(
            client, task_spec, accumulated, progress_notes, progress, page_state,
        )
        if final_result:
            _write_live_checkpoint(step, final_result, status="partial_success")
            output_mgr.write_result(
                status="partial_success",
                extracted=final_result,
                errors=[f"Exhausted {effective_max} steps — finalized via consolidation"],
                notes=progress_notes,
                steps=step,
            )
            if sample.url:
                try:
                    memory.learn_failures(sample.url, progress, "partial_success",
                                          reason="Exhausted steps, finalized")
                except Exception:
                    pass
            return

    _write_live_checkpoint(step, accumulated or {}, status="max_steps_exceeded")
    final_status = "partial_success" if accumulated else "failed"
    output_mgr.write_result(
        status=final_status,
        extracted=accumulated or {},
        errors=[f"Exhausted {effective_max} steps without completing (base={task_spec.max_steps}, bonus={pagination_bonus})"],
        steps=step,
    )
    if sample.url:
        try:
            memory.learn_failures(
                sample.url, progress, final_status,
                reason=f"Exhausted {effective_max} steps",
            )
        except Exception:
            pass
