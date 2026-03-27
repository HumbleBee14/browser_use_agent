"""Custom browser-use actions for evidence collection.

These actions extend browser-use's built-in capabilities with
evidence-specific operations: named screenshots, field extraction
recording, audit judgments, and file downloads with hashing.

Actions are registered on a Controller (Tools) instance via the
@controller.action() decorator. browser-use injects special params
(browser_session, page_url, page_extraction_llm) by NAME — do NOT
add type annotations on these params or it will conflict.
"""

import json

from browser_use import Controller
from browser_use.agent.views import ActionResult

from output.file_manager import FileManager


def create_evidence_controller(file_manager: FileManager) -> Controller:
    """Create a Controller with all evidence collection actions registered.

    We use closure over file_manager so actions can save artifacts
    without global state. Fresh controller per sample.
    """
    controller = Controller()

    @controller.action(
        description=(
            "Take a named evidence screenshot of the current page. "
            "Use this to capture visual proof at key moments. "
            "Provide a descriptive label like 'commit_detail_page' or 'pr_review_status'. "
            "Set full_page=true for the entire scrollable page, or full_page=false for just the visible viewport."
        )
    )
    async def screenshot_evidence(
        label: str,
        full_page: bool = True,
        browser_session=None,  # Injected by browser-use
        page_url=None,  # Injected by browser-use
    ) -> ActionResult:
        """Capture screenshot and save to evidence folder."""
        # take_screenshot() returns raw bytes (already decoded from base64 internally)
        screenshot_bytes = await browser_session.take_screenshot(full_page=full_page)
        mode = "full_page" if full_page else "viewport"

        artifact = file_manager.save_screenshot(
            data=screenshot_bytes,
            label=label,
            source_url=page_url or "",
        )
        return ActionResult(
            extracted_content=(
                f"Screenshot saved: {artifact.filename} ({mode}) "
                f"(sha256: {artifact.sha256}, source: {page_url})"
            ),
            include_in_memory=True,
        )

    @controller.action(
        description=(
            "Record extracted field values from the current page. "
            "Call this after you have identified and read field values from the page. "
            "Provide fields as a JSON object like: "
            '{"assignee": "Jane Doe", "status": "Open", "due_date": "2026-03-20"}. '
            "Optionally provide source_selector (CSS selector or description of where each field was found) "
            "and artifact_ref (filename of a screenshot that shows the field)."
        )
    )
    async def record_fields(
        fields_json: str,
        source_selector: str = "",
        artifact_ref: str = "",
        page_url=None,  # Injected by browser-use
    ) -> ActionResult:
        """Record extracted fields with source URL provenance."""
        try:
            fields = json.loads(fields_json)
        except json.JSONDecodeError as e:
            return ActionResult(
                extracted_content=f"ERROR: Invalid JSON for fields: {e}",
            )

        from models.evidence import FieldExtraction

        # Resolve artifact_ref: validate against real saved filenames,
        # fall back to latest artifact if the agent-supplied name doesn't match.
        valid_filenames = {a.filename for a in file_manager.artifacts}
        resolved_ref = ""
        if artifact_ref and artifact_ref in valid_filenames:
            resolved_ref = artifact_ref
        elif artifact_ref:
            # Agent gave a name that doesn't match — use latest real artifact
            resolved_ref = file_manager.artifacts[-1].filename if file_manager.artifacts else ""
        else:
            resolved_ref = file_manager.artifacts[-1].filename if file_manager.artifacts else ""

        extractions = []
        for name, value in fields.items():
            extraction = FieldExtraction(
                field_name=name,
                value=value,
                source_url=page_url or "",
                source_selector=source_selector or None,
                artifact_ref=resolved_ref or None,
            )
            extractions.append(extraction)
            file_manager._extractions.append(extraction)

        field_summary = ", ".join(f"{k}={v}" for k, v in fields.items())
        return ActionResult(
            extracted_content=f"Recorded {len(fields)} fields: {field_summary}",
            include_in_memory=True,
        )

    @controller.action(
        description=(
            "Mark an evidence checkpoint as satisfied. "
            "Call this after you have collected the required evidence for a checkpoint. "
            "Provide the exact checkpoint name from the task definition."
        )
    )
    async def mark_checkpoint(
        checkpoint_name: str,
    ) -> ActionResult:
        """Mark a checkpoint as satisfied."""
        file_manager._checkpoints_met.append(checkpoint_name)

        return ActionResult(
            extracted_content=f"Checkpoint '{checkpoint_name}' marked as satisfied.",
            include_in_memory=True,
        )

    @controller.action(
        description=(
            "Make an audit judgment based on the evidence you have collected. "
            "answer must be 'yes', 'no', or 'inconclusive'. "
            "confidence is a float 0.0-1.0. "
            "reasoning explains why you reached this conclusion. "
            "evidence_refs is a comma-separated list of artifact filenames that support the judgment. "
            "source_urls is a comma-separated list of URLs you reviewed."
        )
    )
    async def make_judgment(
        question: str,
        answer: str,
        confidence: float,
        reasoning: str,
        evidence_refs: str = "",
        source_urls: str = "",
        page_url=None,  # Injected by browser-use
    ) -> ActionResult:
        """Record a structured audit judgment."""
        from models.judgment import JudgmentResult

        if answer not in ("yes", "no", "inconclusive"):
            return ActionResult(
                extracted_content=f"ERROR: answer must be 'yes', 'no', or 'inconclusive', got '{answer}'",
            )

        # Parse comma-separated refs, validate against real saved artifacts.
        # Agent may hallucinate filenames (e.g. "pr_review.png" vs "03_pr_review.png").
        valid_filenames = {a.filename for a in file_manager.artifacts}
        raw_refs = [r.strip() for r in evidence_refs.split(",") if r.strip()]
        if raw_refs:
            refs = [r if r in valid_filenames else None for r in raw_refs]
            refs = [r for r in refs if r]  # drop invalid refs
            if not refs:
                # All agent-supplied refs were invalid — fall back to all artifacts
                refs = [a.filename for a in file_manager.artifacts]
        else:
            refs = [a.filename for a in file_manager.artifacts]

        urls = [u.strip() for u in source_urls.split(",") if u.strip()]
        if not urls and page_url:
            urls = [page_url]

        judgment = JudgmentResult(
            question=question,
            answer=answer,
            confidence=max(0.0, min(1.0, confidence)),
            reasoning=reasoning,
            evidence_refs=refs,
            source_urls=urls,
        )
        file_manager._judgment = judgment

        return ActionResult(
            extracted_content=(
                f"Judgment recorded: {answer} (confidence: {confidence:.1%}). "
                f"Reasoning: {reasoning}. Evidence: {refs}"
            ),
            include_in_memory=True,
        )

    @controller.action(
        description=(
            "Download a file from the current page or a given URL. "
            "The file will be saved to the evidence folder with its SHA-256 hash. "
            "Provide the filename to save as."
        )
    )
    async def download_file(
        filename: str,
        url: str = "",
        browser_session=None,  # Injected by browser-use
        page_url=None,  # Injected by browser-use
    ) -> ActionResult:
        """Download a file and save to evidence folder with hash."""
        try:
            download_url = url or ""
            if not download_url:
                return ActionResult(
                    extracted_content=(
                        "ERROR: You must provide an explicit file URL to download. "
                        "Do not call download_file without a URL."
                    ),
                )

            # Use the browser session to download
            page = await browser_session.get_current_page()
            response = await page.context.request.get(download_url)

            # Validate response before saving
            status = response.status
            content_type = response.headers.get("content-type", "")
            if status < 200 or status >= 300:
                return ActionResult(
                    extracted_content=(
                        f"ERROR: Download failed with HTTP {status} for {download_url}"
                    ),
                )

            # Reject HTML responses — likely a redirect, login page, or error page
            if "text/html" in content_type and not filename.endswith((".html", ".htm")):
                return ActionResult(
                    extracted_content=(
                        f"ERROR: Server returned HTML (content-type: {content_type}) "
                        f"instead of a file. The URL may require authentication or "
                        f"may be a redirect. URL: {download_url}"
                    ),
                )

            data = await response.body()

            artifact = file_manager.save_download(
                data=data,
                filename=filename,
                source_url=download_url,
            )
            return ActionResult(
                extracted_content=(
                    f"Downloaded: {artifact.filename} "
                    f"(sha256: {artifact.sha256}, size: {len(data)} bytes, "
                    f"content-type: {content_type}, source: {download_url})"
                ),
                include_in_memory=True,
            )
        except Exception as e:
            return ActionResult(
                extracted_content=f"ERROR downloading file: {e}",
            )

    return controller
