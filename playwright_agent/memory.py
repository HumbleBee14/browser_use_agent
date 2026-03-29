"""Long-term memory — learns navigation patterns from successful runs.

After each successful task, the agent distills its action sequence into a
compact reusable pattern keyed by domain. On future runs against the same
domain, these patterns are injected into the prompt so the agent can skip
the discovery phase and execute efficiently.

Storage: JSON file at memory/patterns.json (no external dependencies).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from anthropic import AsyncAnthropic

import config

_DISTILL_PROMPT = """Analyze this browser agent's full action log from a completed task. Steps marked FAIL show what didn't work. Use both successes and failures to extract a reusable navigation pattern.

Goal: {goal}
Domain: {domain}
Steps taken: {steps}

Full action log:
{log_text}

Return a JSON object with exactly these fields:
- task_type: short label like "profile_extraction", "data_collection", "audit"
- action_sequence: list of 4-7 ABSTRACT reusable steps (not URLs or selectors, just patterns like "goto profile page", "extract sidebar data", "screenshot evidence", "save_progress", "goto back to listing")
- tips: list of 2-4 site-specific navigation tips based on what WORKED (e.g. "follower count is a link element", "use goto(url) to return, not browser back", "sidebar has all profile data in one view")
- avoid: list of 1-3 things that FAILED or wasted steps (e.g. "selector X broke — use Y instead", "don't re-screenshot same page", "scrolling the contributions graph yields nothing useful")

Keep it SHORT — under 250 tokens total. Contrast what worked vs what failed.
Return ONLY valid JSON, no markdown."""


class MemoryStore:
    """Domain-keyed pattern store with LLM-powered distillation.

    Stores two kinds of memories:
    - Procedural patterns (from successes): reusable navigation sequences
    - Episodic warnings (from failures): dead ends, broken selectors, traps
    """

    def __init__(self, memory_dir: Path | None = None):
        self.memory_dir = memory_dir or config.MEMORY_DIR
        self.patterns_file = self.memory_dir / "patterns.json"
        self.failures_file = self.memory_dir / "failures.json"
        self._patterns: dict = self._load(self.patterns_file)
        self._failures: dict = self._load(self.failures_file)

    @staticmethod
    def _load(filepath: Path) -> dict:
        if filepath.exists():
            try:
                return json.loads(filepath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_file(self, data: dict, filepath: Path) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        tmp = filepath.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(filepath)

    def _save(self) -> None:
        self._save_file(self._patterns, self.patterns_file)

    def _save_failures(self) -> None:
        self._save_file(self._failures, self.failures_file)

    def get_hints(self, url: str, goal: str = "") -> str | None:
        """Retrieve navigation hints for a URL's domain, filtered by task relevance.

        When a goal is provided, patterns whose task_type appears in the goal
        text are ranked first. This prevents a stargazer pattern from bleeding
        into a commit-audit run on the same domain.

        Pure read — no side effects on stored data.
        """
        domain = urlparse(url).netloc
        patterns = self._patterns.get(domain, [])
        failures = self._failures.get(domain, [])

        if not patterns and not failures:
            return None

        parts: list[str] = []

        if patterns:
            ranked = self._rank_patterns(patterns, goal)
            parts.append(f"## Navigation memory for {domain}")
            parts.append(f"(from {len(ranked)} previous successful run{'s' if len(ranked) > 1 else ''})\n")
            for p in ranked:
                parts.append(f"**{p.get('task_type', 'task')}** ({p.get('avg_steps', '?')} steps avg)")
                seq = p.get("action_sequence", [])
                if seq:
                    parts.append("Efficient sequence: " + " → ".join(seq))
                for tip in p.get("tips", []):
                    parts.append(f"• {tip}")
                for avoid in p.get("avoid", []):
                    parts.append(f"⚠ Avoid: {avoid}")
                parts.append("")

        if failures:
            parts.append(f"## Known issues on {domain} (from past failures)")
            for f in failures[-3:]:
                if f.get("failed_urls"):
                    parts.append(f"Dead URLs (skip): {', '.join(f['failed_urls'][:5])}")
                if f.get("blocked_selectors"):
                    parts.append(f"Broken selectors: {', '.join(f['blocked_selectors'][:5])}")
                if f.get("dead_ends"):
                    parts.append(f"Dead ends: {', '.join(f['dead_ends'][:3])}")
                if f.get("failure_reason"):
                    parts.append(f"Previous failure: {f['failure_reason']}")
            parts.append("")

        return "\n".join(parts) if parts else None

    def record_usage(self, url: str, goal: str = "") -> None:
        """Increment usage counters for patterns served. Call after a run starts."""
        domain = urlparse(url).netloc
        patterns = self._patterns.get(domain, [])
        if not patterns:
            return
        for p in self._rank_patterns(patterns, goal):
            p["uses"] = p.get("uses", 0) + 1
        self._save()

    @staticmethod
    def _rank_patterns(patterns: list[dict], goal: str) -> list[dict]:
        """Rank patterns by relevance to the current goal.

        Patterns whose task_type keywords appear in the goal are placed first.
        Others are still included (they may have useful site-level tips) but
        ranked lower.
        """
        if not goal:
            return patterns

        goal_lower = goal.lower()

        def _relevance(p: dict) -> int:
            task_type = p.get("task_type", "").lower().replace("_", " ")
            if not task_type:
                return 0
            return sum(1 for word in task_type.split() if word in goal_lower)

        return sorted(patterns, key=_relevance, reverse=True)

    def learn_failures(
        self,
        url: str,
        progress: dict,
        status: str,
        reason: str = "",
    ) -> bool:
        """Store failure signals so future runs avoid the same dead ends.

        Learns from: failed, partial_success, needs_review runs.
        Stores: failed URLs, broken selectors, dead ends, failure reason.
        """
        if status == "done":
            return False

        domain = urlparse(url).netloc
        if not domain:
            return False

        failed_urls = progress.get("failed_urls", [])
        blocked = progress.get("blocked_selectors", [])
        dead_ends = progress.get("dead_ends", [])

        if not failed_urls and not blocked and not dead_ends and not reason:
            return False

        entry = {
            "status": status,
            "failure_reason": reason[:200] if reason else "",
            "failed_urls": failed_urls[:10],
            "blocked_selectors": blocked[:10],
            "dead_ends": dead_ends[:5],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if domain not in self._failures:
            self._failures[domain] = []

        self._failures[domain].append(entry)
        self._failures[domain] = self._failures[domain][-config.MAX_PATTERNS_PER_DOMAIN:]
        self._save_failures()
        return True

    async def learn_from_run(
        self,
        client: AsyncAnthropic,
        url: str,
        goal: str,
        history: list[dict],
        steps: int,
        status: str,
    ) -> bool:
        """Distill a successful run into a reusable pattern.

        Returns True if a pattern was stored, False if skipped.
        Only learns from 'done' runs to avoid teaching bad patterns.
        """
        if status != "done":
            return False

        domain = urlparse(url).netloc
        if not domain:
            return False

        agent_steps = [
            s for s in history
            if s.get("action") not in ("system_notice",)
        ]

        if len(agent_steps) < 3:
            return False

        pattern = await self._distill(client, domain, goal, agent_steps, steps)
        if not pattern:
            return False

        if domain not in self._patterns:
            self._patterns[domain] = []

        existing_types = {p.get("task_type") for p in self._patterns[domain]}
        if pattern.get("task_type") in existing_types:
            self._patterns[domain] = [
                p for p in self._patterns[domain]
                if p.get("task_type") != pattern["task_type"]
            ]

        pattern["avg_steps"] = steps
        pattern["created_at"] = datetime.now(timezone.utc).isoformat()
        pattern["uses"] = 0
        self._patterns[domain].append(pattern)

        self._patterns[domain] = self._patterns[domain][-config.MAX_PATTERNS_PER_DOMAIN:]
        self._save()
        return True

    async def _distill(
        self,
        client: AsyncAnthropic,
        domain: str,
        goal: str,
        steps_list: list[dict],
        total_steps: int,
    ) -> dict | None:
        """Use fast model to extract a compact pattern from the full action log."""
        def _format_step(s: dict) -> str:
            result_str = str(s.get("result", ""))
            failed = "failed" in result_str.lower()[:50]
            tag = "FAIL" if failed else "OK"
            return (
                f"[{tag}] Step {s.get('step', '?')}: {s.get('action', '?')}"
                f"({json.dumps(s.get('params', {}), default=str)[:80]}) "
                f"→ {result_str[:120]}"
            )

        log_text = "\n".join(_format_step(s) for s in steps_list[:30])

        try:
            response = await client.messages.create(
                model=config.LLM_FAST_MODEL,
                max_tokens=400,
                messages=[{
                    "role": "user",
                    "content": _DISTILL_PROMPT.format(
                        goal=goal,
                        domain=domain,
                        steps=total_steps,
                        log_text=log_text,
                    ),
                }],
            )
            text = response.content[0].text
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                parsed = json.loads(json_match.group())
                if "action_sequence" in parsed and "tips" in parsed:
                    return parsed
        except Exception:
            pass

        ok_actions = [s.get("action", "") for s in steps_list
                      if "failed" not in str(s.get("result", "")).lower()[:50]]
        failed_actions = [s.get("action", "") for s in steps_list
                         if "failed" in str(s.get("result", "")).lower()[:50]]
        return {
            "task_type": "general",
            "action_sequence": list(dict.fromkeys(ok_actions))[:7],
            "tips": [],
            "avoid": [f"{a} failed" for a in dict.fromkeys(failed_actions)][:3],
        }
