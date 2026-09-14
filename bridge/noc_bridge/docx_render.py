"""Renders a `ReportDocument` (see noc_bridge.report_document) into a DOCX
file (master plan §29 Daily Report Job Contract: "... -> daily-alert-
report -> DOCX -> MinIO -> UI download"). This runs in the bridge process
itself, not inside the sandbox — the sandbox's only job is to produce the
structured JSON via the Claude CLI; keeping DOCX rendering out of the
sandbox image means that image doesn't need python-docx or any of its
dependencies baked in, and a rendering bug can be fixed/redeployed
without touching the sandbox image at all.

Skill Runtime mission Phase 9: `render_document` knows only
`report_document`'s block types. Translation from a skill-owned result to
those blocks lives in a report assembler, so a different report skill/shape
only needs its own assembler; this DOCX adapter is unchanged. The legacy
daily-report wrapper remains as a compatibility entry point for that one
assembler.
"""
from __future__ import annotations

import io
import pathlib
from typing import Callable

from docx import Document
from docx.shared import Inches

from noc_bridge.report_document import (
    AnalysisReference,
    BilingualFindList,
    BilingualText,
    Divider,
    Heading,
    IncidentEvidence,
    Link,
    LogFileReference,
    Metadata,
    Paragraph,
    PageBreak,
    ReportDocument,
    Screenshot,
    build_daily_report_document,
)

ScreenshotFetcher = Callable[[str, str], bytes | None]


def _add_finds(doc: Document, heading: str, finds, *, zh: bool) -> None:
    if not finds:
        return
    doc.add_paragraph(heading, style="Intense Quote")
    for i, find in enumerate(finds, start=1):
        stats = f"（{find.stat}）" if (zh and find.stat) else (f" ({find.stat})" if find.stat else "")
        p = doc.add_paragraph()
        p.add_run(f"{i}. {find.label}{stats}").bold = True
        if find.detail:
            doc.add_paragraph(find.detail)


def _add_bilingual_find_list(doc: Document, find_list: BilingualFindList) -> None:
    _add_finds(doc, find_list.heading_zh, find_list.finds_zh, zh=True)
    _add_finds(doc, find_list.heading_en, find_list.finds_en, zh=False)


def _add_bilingual_text(doc: Document, block: BilingualText) -> None:
    if block.heading_zh:
        doc.add_heading(block.heading_zh, level=3)
    doc.add_paragraph(block.text_zh)
    if block.heading_en:
        doc.add_heading(block.heading_en, level=3)
    doc.add_paragraph(block.text_en)


def _add_incident_evidence(doc: Document, block: IncidentEvidence, screenshot_fetcher: ScreenshotFetcher | None) -> None:
    doc.add_heading(block.heading, level=2)
    if block.incident_id:
        doc.add_paragraph(f"Incident ID: {block.incident_id}")
    if block.metadata:
        doc.add_paragraph("  ·  ".join(f"{m.label}: {m.value}" for m in block.metadata))
    links = list(block.links)
    if block.link:
        links.insert(0, block.link)
    for link in links:
        doc.add_paragraph(link.label)
        doc.add_paragraph(link.url)
    if block.log_file:
        doc.add_paragraph(f"Log File — File Name: {block.log_file.filename}")
    for shot in block.screenshots:
        if screenshot_fetcher is None:
            continue
        data = screenshot_fetcher(shot.bucket, shot.object_key)
        if not data:
            continue
        try:
            doc.add_picture(io.BytesIO(data), width=Inches(5.5))
        except Exception:
            # A non-image or corrupt screenshot shouldn't fail the whole
            # report — note it and move on.
            doc.add_paragraph(f"[could not embed screenshot: {shot.filename}]")


def _add_analysis_reference(
    doc: Document,
    block: AnalysisReference,
    screenshot_fetcher: ScreenshotFetcher | None,
) -> None:
    doc.add_heading(block.heading, level=2)
    if block.analysis_run_id:
        doc.add_paragraph(f"Analysis reference: {block.analysis_run_id}")
    for metadata in block.metadata:
        doc.add_paragraph(f"{metadata.label}: {metadata.value}")
    if not block.available:
        doc.add_paragraph(block.unavailable_text or "No analysis available.")
        return
    for child in block.children:
        _render_block(doc, child, screenshot_fetcher)
    # Compatibility fields remain readable for historical daily-report
    # snapshots created before analysis_reference.children existed.
    if block.summary:
        _add_bilingual_text(doc, block.summary)
    if block.key_finds:
        _add_bilingual_find_list(doc, block.key_finds)
    if block.secondary_finds:
        _add_bilingual_find_list(doc, block.secondary_finds)
    if block.likely_cause:
        if block.likely_cause.text_zh:
            doc.add_paragraph(block.likely_cause.text_zh)
        if block.likely_cause.text_en:
            doc.add_paragraph(block.likely_cause.text_en)
    if block.recommended_action:
        if block.recommended_action.text_zh:
            doc.add_paragraph(block.recommended_action.text_zh)
        if block.recommended_action.text_en:
            doc.add_paragraph(block.recommended_action.text_en)


def _render_block(doc: Document, block, screenshot_fetcher: ScreenshotFetcher | None) -> None:
    if isinstance(block, Heading):
        doc.add_heading(block.text, level=block.level)
    elif isinstance(block, Paragraph):
        doc.add_paragraph(block.text, style=block.style) if block.style else doc.add_paragraph(block.text)
    elif isinstance(block, Metadata):
        doc.add_paragraph(f"{block.label}: {block.value}")
    elif isinstance(block, Link):
        doc.add_paragraph(f"{block.label}: {block.url}")
    elif isinstance(block, LogFileReference):
        doc.add_paragraph(f"Log File — File Name: {block.filename}")
    elif isinstance(block, Screenshot):
        if screenshot_fetcher is not None:
            data = screenshot_fetcher(block.bucket, block.object_key)
            if data:
                doc.add_picture(io.BytesIO(data), width=Inches(5.5))
    elif isinstance(block, Divider):
        doc.add_paragraph("―" * 20)
    elif isinstance(block, PageBreak):
        doc.add_page_break()
    elif isinstance(block, BilingualText):
        _add_bilingual_text(doc, block)
    elif isinstance(block, IncidentEvidence):
        _add_incident_evidence(doc, block, screenshot_fetcher)
    elif isinstance(block, AnalysisReference):
        _add_analysis_reference(doc, block, screenshot_fetcher)
    else:
        raise ValueError(f"render_document: unknown block type {type(block)!r}")


def render_document(
    document: ReportDocument,
    dest_path: pathlib.Path,
    screenshot_fetcher: ScreenshotFetcher | None = None,
) -> None:
    """Generic renderer: consumes only report_document's block types,
    never a skill's own field names. Any ReportDocument — regardless of
    which skill or assembly function built it — renders through here
    unchanged."""
    doc = Document()
    doc.add_heading(document.title, level=0)
    for metadata in document.metadata:
        doc.add_paragraph(f"{metadata.label}: {metadata.value}")

    for block in document.blocks:
        _render_block(doc, block, screenshot_fetcher)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest_path))


def render_daily_report_docx(
    result: dict,
    dest_path: pathlib.Path,
    screenshot_fetcher: ScreenshotFetcher | None = None,
) -> None:
    """The one skill-aware entry point: builds daily-alert-report's
    ReportDocument from its validated/merged result, then renders it
    through the generic renderer. Kept as the stable call site
    service.py uses, so this Phase 9 refactor needed no service.py
    changes."""
    render_document(build_daily_report_document(result), dest_path, screenshot_fetcher)
