"""Single-page strategy — visit one URL, extract fields, take screenshots.

The simplest strategy. No link following, no multi-page navigation.
Good for: Linear ticket extraction, direct URL data extraction.

This is the default strategy. If a task YAML doesn't specify a strategy,
it gets this one.
"""

from __future__ import annotations

from models.task import SampleInput, TaskConfig
from strategies.base import BaseTaskStrategy


class SinglePageStrategy(BaseTaskStrategy):
    """Visit one URL, extract, screenshot. No link following."""

    def build_prompt(self, task: TaskConfig, sample: SampleInput) -> str:
        fields_desc = ""
        if task.output_fields:
            fields_list = ", ".join(
                f"{f.name} ({f.type}{'*' if f.required else ''})"
                for f in task.output_fields
            )
            fields_desc = f"\n\nFIELDS TO EXTRACT: {fields_list}"
            fields_desc += "\n(Fields marked * are required — you must extract them.)"

        checkpoints_desc = ""
        if task.checkpoints:
            cp_list = "\n".join(
                f"  - {c.name}: {c.description} ({'REQUIRED' if c.required else 'optional'})"
                for c in task.checkpoints
            )
            checkpoints_desc = f"\n\nEVIDENCE CHECKPOINTS:\n{cp_list}"
            checkpoints_desc += (
                "\nYou must satisfy all REQUIRED checkpoints before finishing."
            )

        judgment_desc = ""
        if task.judgment_question:
            judgment_desc = (
                f"\n\nJUDGMENT REQUIRED: After collecting evidence, answer this question: "
                f'"{task.judgment_question}"\n'
                f"Use the make_judgment action with your answer (yes/no/inconclusive), "
                f"confidence (0.0-1.0), and reasoning."
            )

        sample_context = f"\n\nSAMPLE: ID={sample.sample_id}"
        if sample.url:
            sample_context += f", URL={sample.url}"
        if sample.extra_fields:
            sample_context += f", Extra={sample.extra_fields}"

        return (
            f"{task.instructions}"
            f"{fields_desc}"
            f"{checkpoints_desc}"
            f"{judgment_desc}"
            f"{sample_context}"
        )
