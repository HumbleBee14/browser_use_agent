"""Graph traversal strategy — follow links across pages, collect at each node.

Handles multi-page workflows where the agent navigates from page to page,
collecting evidence at each stop:
  - GitHub: commit → PR → review status → CI checks
  - LinkedIn: search → candidate list → profile → enrichment
  - Blame: file → blame view → author → materiality judgment

ADL-3: Why one strategy handles all three — the navigation PATTERN is identical
(follow links, collect at nodes). What differs is WHICH links and WHAT to collect,
and that's controlled by YAML checkpoints and instructions.

State management: all per-sample state lives in the agent's context window
(conversation history) and the SampleResult. The strategy itself is stateless.
"""

from __future__ import annotations

from models.task import SampleInput, TaskConfig
from strategies.single_page import SinglePageStrategy


class GraphTraversalStrategy(SinglePageStrategy):
    """Multi-page: follow links across pages, collect evidence at each node.

    Extends SinglePageStrategy's prompt building with graph-specific
    navigation rules: track visited URLs, satisfy checkpoints at each
    node, handle cross-domain links safely.
    """

    def build_prompt(self, task: TaskConfig, sample: SampleInput) -> str:
        # Get the base prompt from SinglePageStrategy
        base_prompt = super().build_prompt(task, sample)

        # Add graph-specific navigation instructions
        required_cps = [c.name for c in task.checkpoints if c.required]
        optional_cps = [c.name for c in task.checkpoints if not c.required]

        graph_rules = (
            "\n\n--- NAVIGATION RULES (GRAPH TRAVERSAL) ---"
            "\n1. Track every URL you visit. Do NOT revisit the same page."
            "\n2. At each page, check if any evidence checkpoints can be satisfied."
            "\n3. Follow links to related pages (PRs, reviews, CI, profiles) as instructed."
            "\n4. Take an evidence screenshot at each significant page you visit."
            "\n5. Extract relevant fields at each page — record the source URL for each."
        )

        if task.allowed_domains:
            domains = ", ".join(task.allowed_domains)
            graph_rules += (
                f"\n6. ALLOWED DOMAINS: {domains}. "
                f"If a link leads outside these domains, note it but do NOT follow."
            )
        else:
            graph_rules += (
                "\n6. If a link leads to an unexpected/unrelated domain, "
                "note it in your observations but do NOT follow it."
            )

        if required_cps:
            graph_rules += (
                f"\n7. REQUIRED CHECKPOINTS you must satisfy: {required_cps}"
            )
        if optional_cps:
            graph_rules += (
                f"\n8. OPTIONAL CHECKPOINTS (collect if possible): {optional_cps}"
            )

        graph_rules += (
            "\n9. When all required checkpoints are met and fields extracted, "
            "you may finish. Do not navigate further than necessary."
        )

        return f"{base_prompt}{graph_rules}"
