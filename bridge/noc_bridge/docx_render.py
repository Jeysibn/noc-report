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
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Inches, Pt, RGBColor

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
from noc_bridge.failures import EvidenceIntegrityError, EvidenceRetrievalError

ScreenshotFetcher = Callable[[str, str], bytes | None]


def _set_east_asia_font(style, name: str = "Microsoft YaHei") -> None:
    style.font.name = name
    style._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), name)


def _configure_document(doc: Document, title: str, metadata: tuple[Metadata, ...]) -> None:
    """Apply reusable document typography, margins, headers, and footers."""
    for section in doc.sections:
        section.top_margin = Inches(0.72)
        section.bottom_margin = Inches(0.68)
        section.left_margin = Inches(0.78)
        section.right_margin = Inches(0.78)
        header = section.header.paragraphs[0]
        header.text = title
        header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        for run in header.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(100, 116, 139)
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        date = next((m.value for m in metadata if m.label == "Date"), "")
        shift = next((m.value for m in metadata if m.label == "Shift"), "")
        footer.add_run(" · ".join(value for value in (date, shift) if value))
        footer.add_run("  |  Page ")
        page_field = OxmlElement("w:fldSimple")
        page_field.set(qn("w:instr"), "PAGE")
        footer._p.append(page_field)
        for run in footer.runs:
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(100, 116, 139)

    for style_name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Intense Quote"):
        try:
            style = doc.styles[style_name]
        except KeyError:
            continue
        _set_east_asia_font(style)
        if style_name == "Normal":
            style.font.size = Pt(10)
        elif style_name == "Heading 1":
            style.font.color.rgb = RGBColor(30, 64, 175)
        elif style_name == "Heading 2":
            style.font.color.rgb = RGBColor(37, 99, 235)


def _add_hyperlink(paragraph, url: str, text: str) -> None:
    relationship_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "2563EB")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(color)
    properties.append(underline)
    run.append(properties)
    text_node = OxmlElement("w:t")
    text_node.text = text
    run.append(text_node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _add_link_paragraph(doc: Document, link: Link) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.keep_with_next = True
    paragraph.add_run(f"{link.prefix or link.label}: ").bold = True
    _add_hyperlink(paragraph, link.url, link.text or link.url)


def _add_screenshot(doc: Document, shot: Screenshot, screenshot_fetcher: ScreenshotFetcher | None) -> None:
    if screenshot_fetcher is None:
        raise EvidenceIntegrityError(f"no screenshot fetcher for frozen evidence: {shot.filename or shot.object_key}")
    data = screenshot_fetcher(shot.bucket, shot.object_key)
    if not data:
        raise EvidenceRetrievalError(f"frozen screenshot could not be retrieved: {shot.bucket}/{shot.object_key}")
    try:
        doc.add_picture(io.BytesIO(data), width=Inches(5.9))
        paragraph = doc.paragraphs[-1]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.keep_with_next = True
    except Exception as exc:
        raise EvidenceIntegrityError(f"frozen screenshot is not a valid image: {shot.filename or shot.object_key}") from exc


def _add_log_file(doc: Document, log_file: LogFileReference) -> None:
    paragraph = doc.add_paragraph()
    paragraph.add_run("Log File  ").bold = True
    paragraph.add_run("File Name: ")
    if log_file.url:
        _add_hyperlink(paragraph, log_file.url, log_file.filename)
    else:
        paragraph.add_run(log_file.filename)


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
    # Keep the alert heading glued to whatever paragraph follows it so Word's
    # automatic pagination does not strand "Alert #n - <title>" alone at the
    # bottom of a page with its evidence pushed to the next one.
    heading = doc.add_heading(block.heading, level=2)
    heading.paragraph_format.keep_with_next = True
    if heading.runs:
        heading.runs[0].font.color.rgb = RGBColor(37, 99, 235)
    for shot in block.screenshots:
        _add_screenshot(doc, shot, screenshot_fetcher)
    links = list(block.links)
    if block.link:
        links.insert(0, block.link)
    for link in links:
        _add_link_paragraph(doc, link)
    if block.log_file:
        _add_log_file(doc, block.log_file)
    if block.incident_id:
        doc.add_paragraph(f"Incident ID: {block.incident_id}")
    if block.metadata:
        doc.add_paragraph("  ·  ".join(f"{m.label}: {m.value}" for m in block.metadata))


def _add_analysis_reference(
    doc: Document,
    block: AnalysisReference,
    screenshot_fetcher: ScreenshotFetcher | None,
) -> None:
    heading = doc.add_heading(block.heading, level=2)
    heading.paragraph_format.keep_with_next = True
    if heading.runs:
        heading.runs[0].font.color.rgb = RGBColor(37, 99, 235)
    if block.analysis_run_id:
        doc.add_paragraph(f"Analysis reference: {block.analysis_run_id}")
    for shot in block.screenshots:
        _add_screenshot(doc, shot, screenshot_fetcher)
    if block.log_file:
        _add_log_file(doc, block.log_file)
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
        _add_link_paragraph(doc, block)
    elif isinstance(block, LogFileReference):
        _add_log_file(doc, block)
    elif isinstance(block, Screenshot):
        _add_screenshot(doc, block, screenshot_fetcher)
    elif isinstance(block, Divider):
        doc.add_paragraph("―" * 20)
    elif isinstance(block, PageBreak):
        doc.add_page_break()
    elif isinstance(block, BilingualText):
        _add_bilingual_text(doc, block)
    elif isinstance(block, BilingualFindList):
        _add_bilingual_find_list(doc, block)
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
    _configure_document(doc, document.title, document.metadata)
    title = doc.add_heading(document.title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if title.runs:
        title.runs[0].font.size = Pt(22)
        title.runs[0].font.bold = True
        title.runs[0].font.color.rgb = RGBColor(30, 64, 175)
    for metadata in document.metadata:
        paragraph = doc.add_paragraph()
        paragraph.add_run(f"{metadata.label}: ").bold = True
        paragraph.add_run(metadata.value)

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
