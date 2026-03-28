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

_DISTILL_PROMPT = """Analyze this browser agent's action log from a successful task and extract a reusable navigation pattern.

Goal: {goal}
Domain: {domain}
Steps taken: {steps}

Action log (successful steps only):
{log_text}

Return a JSON object with exactly these fields:
- task_type: short label like "profile_extraction", "data_collection", "audit"
- action_sequence: list of 4-7 ABSTRACT reusable steps (not URLs or selectors, just patterns like "goto profile page", "extract sidebar data", "screenshot evidence", "save_progress", "goto back to listing")
- tips: list of 2-4 site-specific navigation tips the agent should know next time (e.g. "follower count is a link element", "DOM confidence is low due to SVG contribution graph - vision adds minimal value", "use goto(url) to return, not browser back")
- avoid: list of 1-2 things that wasted steps (e.g. "don't re-screenshot same page", "don't call save_progress with duplicate data")

Keep it SHORT — under 200 tokens total. Focus on what saves steps next time.
Return ONLY valid JSON, no markdown."""


class MemoryStore:
    """Domain-keyed pattern store with LLM-powered distillation."""

    def __init__(self, memory_dir: Path | None = None):
        self.memory_dir = memory_dir or config.MEMORY_DIR
        self.patterns_file = self.memory_dir / "patterns.json"
        self._patterns: dict = self._load()

    def _load(self) -> dict:
        if self.patterns_file.exists():
            try:
                return json.loads(self.patterns_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.patterns_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._patterns, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.patterns_file)

    def get_hints(self, url: str) -> str | None:
        """Retrieve navigation hints for a URL's domain.

        Returns a compact text block to inject into the agent prompt,
        or None if no patterns exist for this domain.
        """
        domain = urlparse(url).netloc
        patterns = self._patterns.get(domain, [])
        if not patterns:
            return None

        parts = [f"## Navigation memory for {domain}",
                 f"(from {len(patterns)} previous successful run{'s' if len(patterns) > 1 else ''})\n"]

        for p in patterns:
            parts.append(f"**{p.get('task_type', 'task')}** ({p.get('avg_steps', '?')} steps avg)")
            seq = p.get("action_sequence", [])
            if seq:
                parts.append("Efficient sequence: " + " → ".join(seq))
            for tip in p.get("tips", []):
                parts.append(f"• {tip}")
            for avoid in p.get("avoid", []):
                parts.append(f"⚠ Avoid: {avoid}")
            parts.append("")

        for p in patterns:
            p["uses"] = p.get("uses", 0) + 1
        self._save()

        return "\n".join(parts)

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

        successful_steps = [
            s for s in history
            if s.get("action") not in ("system_notice",)
            and "failed" not in str(s.get("result", "")).lower()[:50]
        ]

        if len(successful_steps) < 3:
            return False

        pattern = await self._distill(client, domain, goal, successful_steps, steps)
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
        """Use fast model to extract a compact pattern from the action log."""
        log_text = "\n".join(
            f"Step {s.get('step', '?')}: {s.get('action', '?')}"
            f"({json.dumps(s.get('params', {}), default=str)[:80]}) "
            f"→ {str(s.get('result', ''))[:120]}"
            for s in steps_list[:30]
        )

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

        actions = [s.get("action", "") for s in steps_list if s.get("action") != "system_notice"]
        unique = list(dict.fromkeys(actions))
        return {
            "task_type": "general",
            "action_sequence": unique[:7],
            "tips": [],
            "avoid": [],
        }
