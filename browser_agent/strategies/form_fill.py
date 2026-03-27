"""Form fill strategy — fill forms, submit, download artifacts.

Handles interactive workflows where the agent must:
  - Fill form fields with provided data
  - Submit forms and verify success
  - Download resulting artifacts (PDFs, reports)
  - Handle multi-step form wizards

Used for: Workday form filling, data entry tasks, report generation.
"""

from __future__ import annotations

from models.task import SampleInput, TaskConfig
from strategies.single_page import SinglePageStrategy


class FormFillStrategy(SinglePageStrategy):
    """Interactive: fill forms, submit, download artifacts.

    Extends SinglePageStrategy with form-specific instructions:
    careful field mapping, submission verification, download handling.
    """

    def build_prompt(self, task: TaskConfig, sample: SampleInput) -> str:
        base_prompt = super().build_prompt(task, sample)

        # Build field mapping from sample's extra_fields
        fill_instructions = ""
        if sample.extra_fields:
            field_map = "\n".join(
                f"  - {k}: {v}" for k, v in sample.extra_fields.items()
            )
            fill_instructions = (
                f"\n\nFORM DATA TO FILL:\n{field_map}"
                "\nMap each value to the correct form field on the page."
            )

        form_rules = (
            "\n\n--- FORM FILL RULES ---"
            "\n1. Before filling, take a screenshot of the EMPTY form."
            "\n2. Fill fields carefully — match field labels to the data provided."
            "\n3. For dropdowns/selects, pick the closest matching option."
            "\n4. After filling ALL fields, take a screenshot of the FILLED form."
            "\n5. Submit the form only after verifying all fields are filled."
            "\n6. After submission, take a screenshot of the confirmation/result page."
            "\n7. If a file download is triggered, save it using the download action."
            "\n8. If the form has multiple steps/pages, repeat this process for each."
            "\n9. Do NOT submit if required fields are empty — report as needs_review."
        )

        return f"{base_prompt}{fill_instructions}{form_rules}"
