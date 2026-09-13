"""Renders a validated `daily_report` result (see noc_bridge.validation)
into a DOCX file (master plan §29 Daily Report Job Contract: "... ->
daily-alert-report -> DOCX -> MinIO -> UI download"). This runs in the
bridge process itself, not inside the sandbox — the sandbox's only job
is to produce the structured JSON via the Claude CLI; keeping DOCX
rendering out of the sandbox image means that image doesn't need
python-docx or any of its dependencies baked in, and a rendering bug can
be fixed/redeployed without touching the sandbox image at all.

Structure mirrors a real-world reference report the NOC team uses
(bilingual Alerts / General Summary / Log Analysis with numbered Key
Finds and Secondary Finds, plus embedded screenshots when evidence
exists): per incident, an "Alerts" entry (Grafana link, log file name),
a shift-level bilingual General Summary, and a per-incident bilingual
Log Analysis section with numbered, counted/percentaged findings.
Screenshot images are embedded via `screenshot_fetcher`, an optional
callback the caller (noc_bridge.service) supplies to pull evidence bytes
out of MinIO — kept out of this module so it stays storage-agnostic and
easy to unit test with plain dicts.
"""
from __future__ import annotations

import io
import pathlib
from typing import Callable

from docx import Document
from docx.shared import Inches


ScreenshotFetcher = Callable[[str, str], bytes | None]


def _add_finds(doc: Document, heading: str, finds: list[dict]) -> None:
    if not finds:
        return
    doc.add_paragraph(heading, style="Intense Quote")
    for i, find in enumerate(finds, start=1):
        count = find.get("count")
        pct = find.get("percentage")
        stats = ""
        if count is not None and pct is not None:
            stats = f" ({count:,} / {pct}%)"
        elif count is not None:
            stats = f" ({count:,})"
        p = doc.add_paragraph()
        p.add_run(f"{i}. {find.get('label_en') or find.get('label_zh')}{stats}").bold = True
        detail_en = find.get("detail_en")
        if detail_en:
            doc.add_paragraph(detail_en)


def _add_finds_zh(doc: Document, heading: str, finds: list[dict]) -> None:
    if not finds:
        return
    doc.add_paragraph(heading, style="Intense Quote")
    for i, find in enumerate(finds, start=1):
        count = find.get("count")
        pct = find.get("percentage")
        stats = ""
        if count is not None and pct is not None:
            stats = f"（{count:,} / {pct}%）"
        elif count is not None:
            stats = f"（{count:,}）"
        p = doc.add_paragraph()
        p.add_run(f"{i}. {find.get('label_zh') or find.get('label_en')}{stats}").bold = True
        detail_zh = find.get("detail_zh")
        if detail_zh:
            doc.add_paragraph(detail_zh)


def render_daily_report_docx(
    result: dict,
    dest_path: pathlib.Path,
    screenshot_fetcher: ScreenshotFetcher | None = None,
) -> None:
    doc = Document()
    doc.add_heading(result["title"], level=0)

    sections = result.get("sections", [])

    # --- Alerts -----------------------------------------------------
    doc.add_heading("Alerts", level=1)
    if not sections:
        doc.add_paragraph("No incidents in this shift.")
    for i, section in enumerate(sections, start=1):
        doc.add_heading(f"Alert #{i} - {section['title']}", level=2)
        doc.add_paragraph(f"Incident: {section['incident_display_id']}  ·  Status: {section['status']}")
        if section.get("grafana_url"):
            doc.add_paragraph("Grafana Link").add_run()
            doc.add_paragraph(section["grafana_url"])
        if section.get("log_filename"):
            doc.add_paragraph(f"Log File — File Name: {section['log_filename']}")

        for shot in section.get("screenshots") or []:
            if screenshot_fetcher is None:
                continue
            data = screenshot_fetcher(shot["bucket"], shot["object_key"])
            if not data:
                continue
            try:
                doc.add_picture(io.BytesIO(data), width=Inches(5.5))
            except Exception:
                # A non-image or corrupt screenshot shouldn't fail the
                # whole report — note it and move on.
                doc.add_paragraph(f"[could not embed screenshot: {shot.get('filename')}]")

    # --- General Summary (shift-level, bilingual) -------------------
    doc.add_heading("General Summary", level=1)
    doc.add_heading("Chinese Summary", level=3)
    doc.add_paragraph(result.get("overview_zh") or "")
    doc.add_heading("English Summary", level=3)
    doc.add_paragraph(result.get("overview_en") or "")

    # --- Cross-Incident Findings (AI cost-optimization mission Phase 2,
    # Issue 6: the one piece of this report that is genuinely reasoning-
    # dependent across incidents, so it's still Claude's own text, merged
    # in verbatim by service.py's deterministic assembly step) ---------
    if result.get("cross_incident_findings_en") or result.get("cross_incident_findings_zh"):
        doc.add_heading("Cross-Incident Findings", level=1)
        doc.add_heading("Chinese", level=3)
        doc.add_paragraph(result.get("cross_incident_findings_zh") or "")
        doc.add_heading("English", level=3)
        doc.add_paragraph(result.get("cross_incident_findings_en") or "")

    # --- Log Analysis (per incident, bilingual) ---------------------
    doc.add_heading("Log Analysis", level=1)
    for section in sections:
        analysis = section.get("analysis")
        doc.add_heading(f"{section['incident_display_id']} — {section['title']}", level=2)
        if analysis is None:
            doc.add_paragraph("No log analysis available yet for this incident.")
            continue

        doc.add_heading("Chinese", level=3)
        doc.add_paragraph(analysis.get("summary_zh") or "")
        _add_finds_zh(doc, "重点发现 (Key Finds)", analysis.get("key_finds") or [])
        _add_finds_zh(doc, "次要发现 (Secondary Finds)", analysis.get("secondary_finds") or [])
        if analysis.get("likely_cause_zh"):
            doc.add_paragraph(f"最可能原因: {analysis['likely_cause_zh']}")
        if analysis.get("recommended_action_zh"):
            doc.add_paragraph(f"建议措施: {analysis['recommended_action_zh']}")

        doc.add_heading("English", level=3)
        doc.add_paragraph(analysis.get("summary_en") or "")
        _add_finds(doc, "Key Finds", analysis.get("key_finds") or [])
        _add_finds(doc, "Secondary Finds", analysis.get("secondary_finds") or [])
        if analysis.get("likely_cause_en"):
            doc.add_paragraph(f"Likely cause: {analysis['likely_cause_en']}")
        if analysis.get("recommended_action_en"):
            doc.add_paragraph(f"Recommended action: {analysis['recommended_action_en']}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest_path))
