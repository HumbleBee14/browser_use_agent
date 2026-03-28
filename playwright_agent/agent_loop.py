"""The Agent Loop — the entire brain of the system.

Custom ReAct cycle: OBSERVE → DECIDE → ACT → CHECK → repeat.

Everything else is scaffolding. This file is the agent.

Key invariants:
- Claude always returns a typed tool call (tool_choice=any), never prose
- History capped at last 5 actions — token cost stays flat
- Actions always return ActionResult, never raise
- done/fail terminate the loop — max_steps is the hard ceiling
- Loop detection: same (url, action) 3+ times → inject recovery nudge
- Consecutive failures: 3+ → inject visible element list
"""

from __future__ import annotations

import json
from datetime import datetime

from anthropic import AsyncAnthropic
from playwright.async_api import Page

import config

# Module-level client — reused across all samples for connection pooling
_client: AsyncAnthropic | None = None


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
    tools = action_tool_schema()
    history: list[dict] = []
    loop_counter: dict[tuple, int] = {}  # (url, action_name) → count
    consecutive_failures = 0
    step = 0

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

    for step in range(1, task_spec.max_steps + 1):
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
        messages = _build_messages(
            page_state=page_state,
            vision_text=vision_text,
            history=history[-5:],  # rolling 5-action cap
            task_spec=task_spec,
            sample=sample,
            snap=snap,
            consecutive_failures=consecutive_failures,
            loop_counter=loop_counter,
        )

        # Log everything going into the LLM call — full context for debugging
        log.info(
            f"Step {step} LLM input | "
            f"dom_nodes={len(snap.nodes)} | confidence={snap.confidence:.2f} | "
            f"history_items={len(history[-5:])} | vision={'yes' if vision_text else 'no'}"
        )
        log.debug(f"Step {step} system_prompt | {task_spec.system_prompt[:300]}")
        log.debug(f"Step {step} page_state | {page_state[:500]}")
        log.debug(f"Step {step} history | {json.dumps(history[-5:], default=str)[:500]}")
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
        action_result = await _dispatch(action, page, snap, output_mgr)

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

            log.info(f"Completed | status=done | steps={step} | fields={len(extracted)}")
            output_mgr.write_result(
                status="done", extracted=extracted, judgment=judgment or None,
                steps=step,
            )
            return

        if action.action == "fail":
            log.warning(f"Failed | reason={action.note} | steps={step}")
            output_mgr.write_result(
                status="failed",
                errors=[action.note or "Agent called fail"],
                steps=step,
            )
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

    # Exhausted max_steps without done/fail
    log.warning(f"Exhausted {task_spec.max_steps} steps without completing")
    output_mgr.write_result(
        status="failed",
        errors=[f"Exhausted {task_spec.max_steps} steps without completing"],
        steps=step,
    )


async def _dispatch(
    action: AgentAction,
    page: Page,
    snap: dom_extractor.DOMSnapshot,
    output_mgr: OutputManager,
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
            return ActionResult(
                success=True,
                description=f"Screenshot saved: {artifact.filename} (sha256: {artifact.sha256[:12]}...)",
            )

        elif action.action == "extract":
            return await browser.extract_text(page, action.selector or "", snap.element_map)

        elif action.action == "wait":
            return await browser.wait_for(page, action.selector or "")

        elif action.action in ("done", "fail"):
            # Handled in the main loop
            return ActionResult(success=True, description=f"Action: {action.action}")

        else:
            return ActionResult(success=False, error=f"Unknown action: {action.action}")

    except Exception as e:
        return ActionResult(success=False, error=f"Dispatch error: {str(e)[:200]}")


def _build_messages(
    page_state: str,
    vision_text: str,
    history: list[dict],
    task_spec: TaskSpec,
    sample: SampleInput,
    snap: dom_extractor.DOMSnapshot,
    consecutive_failures: int,
    loop_counter: dict,
) -> list[dict]:
    """Build the message list for the LLM call.

    Structure:
      USER message with:
        - Current page state (DOM)
        - Vision analysis (if activated)
        - Action history (last 5)
        - Recovery nudges (if stuck)
        - Goal + output schema
    """
    parts = []

    # Current page state
    parts.append(f"## Current page state\n{page_state}")

    # Vision supplement (if DOM confidence was low)
    if vision_text:
        parts.append(f"\n## Visual analysis (DOM was insufficient)\n{vision_text}")

    # Action history
    if history:
        history_text = "\n".join(
            f"Step {h['step']}: {h['action']} → {h.get('result', '')[:100]}"
            for h in history
        )
        parts.append(f"\n## Actions taken so far (last {len(history)})\n{history_text}")

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
