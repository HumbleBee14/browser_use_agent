"""The Agent Loop — the entire brain of the system.

Custom ReAct cycle: OBSERVE → DECIDE → ACT → CHECK → repeat.

Everything else is scaffolding. This file is the agent.

Key invariants:
- Claude always returns a typed tool call (tool_choice=any), never prose
- History window is dynamic — fits as many recent actions as the token budget allows
- Actions always return ActionResult, never raise
- done/fail terminate the loop — max_steps is the hard ceiling
- Loop detection: same (url, action) 3+ times → inject recovery nudge
- Consecutive failures: 3+ → inject visible element list

Long-horizon support:
- save_progress action: checkpoint partial data without stopping
- Accumulated extraction buffer: merged across save_progress calls
- Step summary: every 10 steps, condense history into a summary
- Live checkpoint.json: updated on every save_progress + every 5 steps
- Progress-aware prompt: shows collected data, pages visited, step budget
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from anthropic import AsyncAnthropic
from playwright.async_api import Page

import config
from memory import MemoryStore

# Module-level singletons — reused across all samples for connection pooling
_client: AsyncAnthropic | None = None
_memory: MemoryStore | None = None


def _get_memory() -> MemoryStore:
    global _memory
    if _memory is None:
        _memory = MemoryStore()
    return _memory


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY, timeout=60.0)
    return _client
from core import dom_extractor, vision
from log_setup import logger
from models.actions import (
    AgentAction,
    ActionResult,
    StepRecord,
    action_tool_schema,
)
from models.task import TaskSpec, SampleInput
from tools import browser
from tools.output import OutputManager


async def run(
    page: Page,
    sample: SampleInput,
    task_spec: TaskSpec,
    output_mgr: OutputManager,
) -> None:
    """Run the agent loop for one sample.

    This is the complete agent. It observes the page, asks Claude what to do,
    executes the action, and repeats until done/fail/max_steps.
    """
    log = logger.bind(sample_id=sample.sample_id)
    log.info(f"Agent loop started | url={sample.url} | max_steps={task_spec.max_steps}")

    client = _get_client()
    memory = _get_memory()
    tools = action_tool_schema()
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

    # Long-term memory: retrieve navigation hints for this domain
    memory_hints = memory.get_hints(sample.url) if sample.url else None
    if memory_hints:
        log.info(f"Memory loaded | domain hints available ({len(memory_hints)} chars)")

    # Long-horizon state
    accumulated: dict = {}           # merged data from save_progress calls
    progress_notes: list[str] = []   # human-readable notes from save_progress
    step_summaries: list[str] = []   # condensed summaries every SUMMARY_INTERVAL steps
    SUMMARY_INTERVAL = 10
    CHECKPOINT_INTERVAL = 5
    WATCHDOG_STALL_LIMIT = 5         # steps without new data → force intervention
    last_data_step = 0               # last step that produced new data
    pagination_bonus = 0             # extra steps granted for pagination

    # Smart termination state
    start_time = time.monotonic()
    network_errors = 0               # consecutive infra-level failures (timeout, DNS, etc.)
    items_collected = 0              # count of save_progress calls (proxy for items done)
    seen_screenshot_hashes: set[str] = set()  # detect duplicate screenshots

    # Navigate to starting URL if provided — fail fast if unreachable
    if sample.url:
        result = await browser.goto(page, sample.url)
        history.append({"step": 0, "action": "goto", "url": sample.url,
                        "result": result.description if result.success else result.error})
        if not result.success:
            log.error(f"Initial navigation failed: {result.error}")
            output_mgr.write_result(
                status="failed",
                errors=[f"Initial navigation failed: {result.error}"],
                steps=0,
            )
            return

    effective_max = task_spec.max_steps
    for step in range(1, task_spec.max_steps + 200):  # hard ceiling with pagination bonus
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
            if accumulated:
                output_mgr.write_checkpoint(step, accumulated, progress_notes, status=status)
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
        snap = await dom_extractor.snapshot(page, task_spec.keywords)
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

        # ---- 2. BUILD PROMPT ----
        # Dynamic history window: estimate fixed prompt costs, then fit history to budget
        fixed_tokens = _estimate_tokens(page_state + vision_text + task_spec.system_prompt + task_spec.goal)
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

        # ---- 3. DECIDE (LLM call with prompt caching) ----
        # System prompt + tools are static across all steps → cache them
        try:
            response = await client.messages.create(
                model=config.LLM_MODEL,
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": task_spec.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=messages,
                tools=tools,
                tool_choice={"type": "any"},  # forces structured output — never prose
            )
        except Exception as e:
            log.error(f"Step {step} | LLM error: {str(e)[:200]}")
            output_mgr.log_step(StepRecord(
                step=step, action="llm_error", result=str(e)[:200], url=page.url,
            ))
            output_mgr.write_result(
                status="failed", errors=[f"LLM error: {str(e)[:200]}"], steps=step,
            )
            return

        # Extract the tool call from response
        tool_block = next(
            (b for b in response.content if b.type == "tool_use"), None
        )
        if not tool_block:
            output_mgr.log_step(StepRecord(
                step=step, action="no_tool_call", result="LLM returned no tool call", url=page.url,
            ))
            consecutive_failures += 1
            continue

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

        # ---- 4. ACT ----
        action_result = await _dispatch(action, page, snap, output_mgr, seen_screenshot_hashes)

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

        # Track dead ends — repeated action on same URL with no data produced
        url_action_key = (page.url[:80], action.action)
        if loop_counter.get(url_action_key, 0) >= 3:
            dead = f"{action.action} on {page.url[:60]}"
            if dead not in progress["dead_ends"]:
                progress["dead_ends"].append(dead)
                log.info(f"Step {step} | Dead end detected: {dead}")

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
            log.info(f"Step {step} | save_progress #{items_collected} | new_data={data_changed} | {note} | keys={list(partial.keys())}")
            output_mgr.write_checkpoint(step, accumulated, progress_notes)

            # Check if we've collected the expected number of items
            if task_spec.expected_items > 0 and items_collected >= task_spec.expected_items:
                log.info(f"Step {step} | Expected items reached ({items_collected}/{task_spec.expected_items})")
                history.append({
                    "step": step,
                    "action": "system_notice",
                    "result": (
                        f"You have collected {items_collected} of {task_spec.expected_items} expected items. "
                        f"All items collected. Call done now with the complete data."
                    ),
                })
            elif not data_changed:
                history.append({
                    "step": step,
                    "action": "system_notice",
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
                    "action": "system_notice",
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
        if step > 0 and step % SUMMARY_INTERVAL == 0 and len(history) > MIN_HISTORY_ITEMS:
            summary = await _summarize_steps(client, history[:-MIN_HISTORY_ITEMS], task_spec.goal, log)
            step_summaries.append(summary)
            log.info(f"Step {step} | LLM summary generated | {summary[:100]}")

        # ---- AUTO-CHECKPOINT: write checkpoint.json every N steps ----
        if step > 0 and step % CHECKPOINT_INTERVAL == 0:
            output_mgr.write_checkpoint(step, accumulated, progress_notes)

        # Log the step
        output_mgr.log_step(StepRecord(
            step=step,
            thinking=thinking,
            action=action.action,
            params=action.model_dump(exclude_none=True, exclude={"action"}),
            result=action_result.description if action_result.success else (action_result.error or ""),
            url=page.url,
        ))

        # Update history for next prompt
        history.append({
            "step": step,
            "action": action.action,
            "params": {k: v for k, v in action.model_dump(exclude_none=True).items() if k != "action"},
            "result": action_result.description if action_result.success else action_result.error,
        })

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

                if step < task_spec.max_steps:
                    # Bounce back — force agent to try again
                    history.append({
                        "step": step,
                        "action": "system_notice",
                        "result": ". ".join(notice_parts) + ". Try again.",
                    })
                    consecutive_failures += 1
                    continue
                else:
                    # Last step — cannot retry. Write needs_review, not done.
                    output_mgr.write_result(
                        status="needs_review", extracted=extracted,
                        errors=notice_parts, steps=step,
                    )
                    return

            # Extract judgment if present
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

        # ---- 6. LOOP & FAILURE TRACKING ----
        loop_key = (page.url, action.action)
        loop_counter[loop_key] = loop_counter.get(loop_key, 0) + 1

        # Track consecutive same action type (catches screenshot/goto/scroll spam)
        # Excludes type/click — consecutive type calls are normal when filling forms
        SPAM_ACTIONS = {"screenshot", "goto", "extract", "scroll"}
        if len(history) >= 3:
            last_3_actions = [h.get("action") for h in history[-3:]]
            if (len(set(last_3_actions)) == 1
                    and last_3_actions[0] in SPAM_ACTIONS):
                history.append({
                    "step": step,
                    "action": "system_notice",
                    "result": (
                        f"You have called '{last_3_actions[0]}' 3 times consecutively. "
                        f"STOP repeating this action. You already have the page data in the "
                        f"page state text above. Extract the fields and call done now."
                    ),
                })

        if action_result.success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1

        # ---- AUTO-PAGINATION: detect "next page" clicks and grant bonus steps ----
        if (action.action == "click" and action_result.success
                and _is_pagination_click(action.selector or "", action_result.description)):
            pagination_bonus += 3
            effective_max = task_spec.max_steps + pagination_bonus
            log.info(f"Step {step} | Pagination detected → +3 bonus steps (effective_max={effective_max})")

        # ---- WATCHDOG: detect stalls and force intervention ----
        # Only reset when genuinely new data arrived (not duplicate save_progress)
        if (action.action == "save_progress" and data_changed) \
                or (action.action == "extract" and action_result.success):
            last_data_step = step

        steps_since_data = step - last_data_step
        if steps_since_data >= WATCHDOG_STALL_LIMIT and step < effective_max:
            log.warning(f"Step {step} | Watchdog: {steps_since_data} steps without new data")
            if accumulated:
                output_mgr.write_checkpoint(step, accumulated, progress_notes, status="watchdog_stall")
            history.append({
                "step": step,
                "action": "system_notice",
                "result": (
                    f"WARNING: You have not produced new data in {steps_since_data} steps. "
                    f"You have {effective_max - step} steps left. Either extract/save_progress with data, "
                    f"or call done with what you have, or call fail if the task cannot be completed."
                ),
            })
            last_data_step = step  # reset to avoid spamming

    # Exhausted max_steps without done/fail
    log.warning(f"Exhausted {effective_max} steps (base={task_spec.max_steps}, pagination_bonus={pagination_bonus})")
    if accumulated:
        output_mgr.write_checkpoint(step, accumulated, progress_notes, status="max_steps_exceeded")
    output_mgr.write_result(
        status="failed",
        extracted=accumulated or {},
        errors=[f"Exhausted {effective_max} steps without completing (base={task_spec.max_steps}, bonus={pagination_bonus})"],
        steps=step,
    )


async def _dispatch(
    action: AgentAction,
    page: Page,
    snap: dom_extractor.DOMSnapshot,
    output_mgr: OutputManager,
    seen_screenshot_hashes: set[str] | None = None,
) -> ActionResult:
    """Execute one action. Always returns ActionResult, never raises."""
    try:
        if action.action == "goto":
            return await browser.goto(page, action.url or "")

        elif action.action == "click":
            return await browser.click(page, action.selector or "", snap.element_map)

        elif action.action == "type":
            return await browser.type_text(
                page, action.selector or "", action.text or "", snap.element_map,
            )

        elif action.action == "scroll":
            return await browser.scroll(page, action.direction or "down")

        elif action.action == "screenshot":
            data = await browser.take_screenshot(page, full_page=True)
            artifact = output_mgr.save_screenshot(data, action.label or "page", page.url)
            if seen_screenshot_hashes is not None and artifact.sha256 in seen_screenshot_hashes:
                page_title = snap.title or "unknown"
                return ActionResult(
                    success=True,
                    description=(
                        f"Screenshot saved: {artifact.filename} — but this is IDENTICAL to a previous screenshot "
                        f"of \"{page_title}\" (same SHA256). You are still on the same page. "
                        f"Do NOT take another screenshot. Navigate to a new page with goto, or call done/fail."
                    ),
                )
            if seen_screenshot_hashes is not None:
                seen_screenshot_hashes.add(artifact.sha256)
            page_title = snap.title or "unknown"
            page_url = snap.url or page.url
            return ActionResult(
                success=True,
                description=(
                    f"Screenshot saved: {artifact.filename} "
                    f"(page: \"{page_title}\", url: {page_url})"
                ),
            )

        elif action.action == "extract":
            return await browser.extract_text(page, action.selector or "", snap.element_map)

        elif action.action == "wait":
            return await browser.wait_for(page, action.selector or "")

        elif action.action == "save_progress":
            return ActionResult(success=True, description="Progress checkpointed")

        elif action.action in ("done", "fail"):
            return ActionResult(success=True, description=f"Action: {action.action}")

        else:
            return ActionResult(success=False, error=f"Unknown action: {action.action}")

    except Exception as e:
        return ActionResult(success=False, error=f"Dispatch error: {str(e)[:200]}")


PAGINATION_KEYWORDS = frozenset({
    "next", "next page", "load more", "show more", "older", "newer",
    "page 2", "page 3", "page 4", "page 5", "»", "›", "→",
    "previous", "prev", "back", "forward",
})


def _is_pagination_click(selector: str, result_desc: str) -> bool:
    """Detect if a click was a pagination action (next page, load more, etc.)."""
    combined = f"{selector} {result_desc}".lower()
    return any(kw in combined for kw in PAGINATION_KEYWORDS)


INFRA_ERROR_PATTERNS = [
    "timeout", "net::err", "dns", "connection refused", "connection reset",
    "network error", "err_connection", "err_name_not_resolved",
    "err_internet_disconnected", "page crashed", "target closed",
    "browser has been closed", "navigation error", "ssl",
]


def _is_infra_error(error_text: str) -> bool:
    """Distinguish infrastructure errors (network, browser) from logic errors (element not found)."""
    lower = error_text.lower()
    return any(p in lower for p in INFRA_ERROR_PATTERNS)


def _check_termination(
    step: int,
    elapsed: float,
    task_spec,
    accumulated: dict,
    items_collected: int,
    network_errors: int,
    effective_max: int,
    log,
) -> tuple[str, str] | None:
    """Check if the agent should stop early for a smart reason.

    Returns (status, reason) if termination needed, None to continue.
    """
    # 1. Wall-clock timeout
    if task_spec.max_time_seconds > 0 and elapsed > task_spec.max_time_seconds:
        has_data = bool(accumulated)
        status = "partial_success" if has_data else "failed"
        return status, f"Wall-clock timeout ({elapsed:.0f}s > {task_spec.max_time_seconds}s limit)"

    # 2. Network circuit breaker — too many consecutive infra failures
    limit = task_spec.max_consecutive_network_errors
    if network_errors >= limit:
        has_data = bool(accumulated)
        status = "partial_success" if has_data else "failed"
        return status, f"Network circuit breaker: {network_errors} consecutive infrastructure errors"

    # 3. Expected items reached via accumulated data (belt+suspenders with save_progress check)
    if task_spec.expected_items > 0 and items_collected >= task_spec.expected_items:
        for array_field in accumulated.values():
            if isinstance(array_field, list) and len(array_field) >= task_spec.expected_items:
                return None  # let the agent call done naturally — it already got the nudge

    # 4. Near step budget with accumulated data — warn but don't terminate
    remaining = effective_max - step
    if remaining == 5 and accumulated:
        log.info(f"Step {step} | 5 steps remaining with accumulated data — agent should wrap up soon")

    return None


def _deep_merge(base: dict, update: dict) -> None:
    """Merge update into base, appending *unique* items to lists and recursing into dicts.

    Deduplication uses JSON serialisation so identical dicts aren't appended twice
    (e.g. the same contributor saved multiple times via save_progress).
    """
    for key, val in update.items():
        if key in base and isinstance(base[key], list) and isinstance(val, list):
            existing = {json.dumps(item, sort_keys=True, default=str) for item in base[key]}
            for item in val:
                serialised = json.dumps(item, sort_keys=True, default=str)
                if serialised not in existing:
                    base[key].append(item)
                    existing.add(serialised)
        elif key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


async def _summarize_steps(
    client: AsyncAnthropic,
    steps: list[dict],
    goal: str,
    log,
) -> str:
    """Produce a structured summary: findings, gaps, next actions.

    Uses the cheap/fast model. Returns a compact structured block the
    agent can act on immediately. Falls back to mechanical summary.
    """
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


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English/code mix."""
    return max(1, len(text) // 4)


# Token budget = 8% of model context window, capped at 24K, floor 8K.
# Why 8%: LLMs lose attention on mid-prompt content above ~20% fill ("lost in the middle" — Liu et al. 2023).
# Cap at 24K: even a 1M-context model doesn't need 80K of prompt for a browser agent step.
PROMPT_TOKEN_BUDGET = min(24_000, max(8_000, int(config.LLM_CONTEXT_WINDOW * 0.08)))
HISTORY_TOKEN_SHARE = 0.30  # 30% of budget goes to action history
MIN_HISTORY_ITEMS = 5
MAX_HISTORY_ITEMS = 25

# Importance weights — data-producing actions get priority in the window
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


def _fit_history(full_history: list[dict], fixed_tokens: int) -> list[dict]:
    """Dynamically select history items that fit within the token budget.

    Strategy (hybrid approach from industry best practices):
    1. Calculate remaining token budget after fixed costs (DOM, vision, goal, etc.)
    2. Always include the last MIN_HISTORY_ITEMS (recency matters most)
    3. For older items, score by importance and include highest-value ones first
    4. Stop when budget is exhausted or MAX_HISTORY_ITEMS reached
    """
    budget = max(500, int(PROMPT_TOKEN_BUDGET * HISTORY_TOKEN_SHARE))

    if not full_history:
        return []

    # Always include the most recent items (verbatim recency window)
    recency_window = full_history[-MIN_HISTORY_ITEMS:]
    recency_tokens = sum(_estimate_tokens(json.dumps(h, default=str)) for h in recency_window)

    remaining_budget = budget - recency_tokens
    if remaining_budget <= 0 or len(full_history) <= MIN_HISTORY_ITEMS:
        return recency_window

    # Score older items by importance and select the most valuable ones
    older = full_history[:-MIN_HISTORY_ITEMS]
    scored = []
    for i, item in enumerate(older):
        action = item.get("action", "")
        importance = _IMPORTANCE.get(action, 0)
        recency_bonus = i / max(len(older), 1)  # 0.0 (oldest) → 1.0 (most recent)
        score = importance + recency_bonus
        tokens = _estimate_tokens(json.dumps(item, default=str))
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


def _build_messages(
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
) -> list[dict]:
    """Build the message list for the LLM call.

    Structure:
      USER message with:
        - Current page state (DOM)
        - Vision analysis (if activated)
        - Action history (dynamically sized)
        - Recovery nudges (if stuck)
        - Goal + output schema
    """
    parts = []

    # Current page state
    parts.append(f"## Current page state\n{page_state}")

    # Vision supplement (if DOM confidence was low)
    if vision_text:
        parts.append(f"\n## Visual analysis (DOM was insufficient)\n{vision_text}")

    # Step budget awareness
    if current_step > 0:
        remaining = task_spec.max_steps - current_step
        parts.append(f"\n**Step {current_step} of {task_spec.max_steps}** ({remaining} remaining)")

    # Accumulated data from save_progress calls (long-term memory)
    if accumulated:
        acc_text = json.dumps(accumulated, indent=2, default=str)
        if len(acc_text) > 2000:
            acc_text = acc_text[:2000] + "\n... (truncated)"
        parts.append(f"\n## Data collected so far (via save_progress)\n```json\n{acc_text}\n```")

    # Step summaries (condensed history from earlier steps)
    if step_summaries:
        parts.append(f"\n## Earlier steps (condensed)\n" + "\n".join(step_summaries[-3:]))

    # Progress summary (persists beyond history window — structured run state)
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

    # Action history (last 5 steps — recent context)
    if history:
        history_text = "\n".join(
            f"Step {h['step']}: {h['action']} → {h.get('result', '')[:100]}"
            for h in history
        )
        parts.append(f"\n## Action history ({len(history)} items, budget-fitted)\n{history_text}")

    # Loop detection nudge
    for (url, act_name), count in loop_counter.items():
        if count >= 3 and url == snap.url:
            parts.append(
                f"\n[NOTICE] You have repeated '{act_name}' on this URL {count} times "
                f"without progress. Try a different approach or call fail()."
            )
            break

    # Consecutive failure recovery
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

    # Sample context
    sample_info = f"SAMPLE: ID={sample.sample_id}"
    if sample.url:
        sample_info += f", URL={sample.url}"
    if sample.extra:
        sample_info += f", Extra={json.dumps(sample.extra)}"
    parts.append(f"\n## Sample\n{sample_info}")

    # Long-term memory hints (learned navigation patterns for this domain)
    if memory_hints and current_step <= 3:
        parts.append(f"\n{memory_hints}")

    # Goal
    parts.append(f"\n## Goal\n{task_spec.goal}")

    # Output schema
    if task_spec.output_schema:
        schema_text = json.dumps(task_spec.output_schema, indent=2)
        parts.append(f"\n## Output schema (populate when calling done)\n{schema_text}")

    # Required fields reminder
    if task_spec.required_fields:
        parts.append(f"\nRequired fields (must be non-empty in done): {task_spec.required_fields}")

    # Judgment reminder
    if task_spec.judgment_required:
        parts.append(
            f"\n## Judgment required\nQuestion: {task_spec.judgment_question}\n"
            f"Include judgment fields in your done() extracted data: "
            f"{json.dumps(task_spec.judgment_output_schema)}"
        )

    parts.append("\nTake the single best next action.")

    return [{"role": "user", "content": "\n".join(parts)}]
