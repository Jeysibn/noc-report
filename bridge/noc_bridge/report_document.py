"""Skill Runtime mission Phase 8: `ReportDocument` — a semantic,
renderer-agnostic intermediate representation for a generated report.

Before this, `docx_render.render_daily_report_docx` read `daily-alert-
report`'s exact result shape directly (`section["incident_display_id"]`,
`analysis["summary_en"]`, ...) while building the DOCX paragraph by
paragraph — the renderer and the skill's output schema were the same
piece of code. That meant a different report skill, or a materially
different report structure, could only be supported by editing the
renderer itself.

`ReportDocument` breaks that coupling in two pieces:
  - `build_daily_report_document` (this module) is the "Report Assembly"
    step (Phase 10): it knows daily-alert-report's exact result shape,
    and translates it into a flat list of typed, generic blocks (heading,
    paragraph, bilingual_text, incident_evidence, screenshot, ...).
  - `noc_bridge.docx_render.render_document` (Phase 9) knows only the
    block types, never a skill's field names — it can render any
    ReportDocument, regardless of which skill or assembly function built
    it.

A future second report skill/shape needs only its own `build_*_document`
function; the renderer is untouched. This module intentionally has no
python-docx import — it's pure data, easy to unit test and, later, to
reuse for a non-DOCX renderer (HTML, PDF, ...) without touching assembly
logic at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Heading:
    text: str
    level: int = 1


@dataclass(frozen=True)
class Paragraph:
    text: str
    style: str | None = None


@dataclass(frozen=True)
class Divider:
    pass


@dataclass(frozen=True)
class PageBreak:
    pass


@dataclass(frozen=True)
class Metadata:
    """A single label/value fact line (e.g. "Incident" / "INC-001")."""

    label: str
    value: str


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class LogFileReference:
    filename: str


@dataclass(frozen=True)
class Screenshot:
    bucket: str
    object_key: str
    filename: str | None = None


@dataclass(frozen=True)
class BilingualText:
    """The same content in two languages, rendered as paired language
    subsections. Keyed zh/en since that's this system's bilingual
    contract throughout, not a generic n-language construct."""

    text_zh: str
    text_en: str
    heading_zh: str | None = None
    heading_en: str | None = None


@dataclass(frozen=True)
class Find:
    label: str
    detail: str | None
    stat: str | None  # pre-formatted "(count / pct%)"-style text, or None


@dataclass(frozen=True)
class BilingualFindList:
    heading_zh: str
    heading_en: str
    finds_zh: tuple[Find, ...]
    finds_en: tuple[Find, ...]


@dataclass(frozen=True)
class IncidentEvidence:
    """One incident's Alerts-style evidence block: its identifying
    metadata, an optional external link (e.g. Grafana), an optional
    reference to its attached log file, and any screenshots."""

    heading: str
    incident_id: str | None = None
    metadata: tuple[Metadata, ...] = ()
    link: Link | None = None
    log_file: LogFileReference | None = None
    screenshots: tuple[Screenshot, ...] = ()


@dataclass(frozen=True)
class AnalysisReference:
    """One incident's log-analysis writeup, or a placeholder when no
    analysis exists yet for it."""

    heading: str
    available: bool
    analysis_run_id: str | None = None
    unavailable_text: str | None = None
    children: tuple["Block", ...] = ()
    summary: BilingualText | None = None
    key_finds: BilingualFindList | None = None
    secondary_finds: BilingualFindList | None = None
    likely_cause: BilingualText | None = None
    recommended_action: BilingualText | None = None


Block = (
    Heading | Paragraph | Divider | PageBreak | Metadata | Link | LogFileReference |
    Screenshot | BilingualText | IncidentEvidence | AnalysisReference
)


@dataclass(frozen=True)
class ReportDocument:
    title: str
    blocks: tuple[Block, ...] = field(default_factory=tuple)
    metadata: tuple[Metadata, ...] = field(default_factory=tuple)


def build_report_document(result: dict) -> ReportDocument:
    """Build the generic renderer IR from a skill-owned declarative result.
    This intentionally maps only semantic primitives; it does not know any
    report section names. Skill-specific assemblers may still produce the
    typed IR directly, as the daily-report assembler does."""
    blocks = []
    raw_metadata = result.get("metadata") or {}
    metadata = tuple(
        Metadata(str(key), str(value))
        for key, value in raw_metadata.items()
        if key != "title" and value is not None
    )
    blocks.extend(_parse_blocks(result.get("blocks", [])))
    return ReportDocument(
        title=raw_metadata.get("title", "Report"),
        blocks=tuple(blocks),
        metadata=metadata,
    )


def _parse_blocks(raw_blocks: list[dict]) -> list[Block]:
    parsed: list[Block] = []
    for raw in raw_blocks:
        kind = raw.get("type")
        if kind == "heading":
            parsed.append(Heading(raw["text"], int(raw.get("level", 1))))
        elif kind == "paragraph":
            parsed.append(Paragraph(raw["text"], raw.get("style")))
        elif kind == "bilingual_text":
            parsed.append(BilingualText(raw.get("zh", ""), raw.get("en", ""), raw.get("heading_zh"), raw.get("heading_en")))
        elif kind == "metadata":
            parsed.append(Metadata(raw["label"], str(raw["value"])))
        elif kind == "link":
            parsed.append(Link(raw["label"], raw["url"]))
        elif kind == "log_file_reference":
            parsed.append(LogFileReference(raw["filename"]))
        elif kind == "screenshot":
            parsed.append(Screenshot(raw["bucket"], raw["object_key"], raw.get("filename")))
        elif kind == "incident_evidence":
            parsed.append(IncidentEvidence(
                heading=raw["heading"],
                incident_id=raw.get("incident_id"),
                metadata=tuple(Metadata(str(item["label"]), str(item["value"])) for item in raw.get("metadata", [])),
                link=Link(raw["link"]["label"], raw["link"]["url"]) if raw.get("link") else None,
                log_file=LogFileReference(raw["log_file"]) if raw.get("log_file") else None,
                screenshots=tuple(Screenshot(s["bucket"], s["object_key"], s.get("filename")) for s in raw.get("screenshots", [])),
            ))
        elif kind == "divider":
            parsed.append(Divider())
        elif kind == "page_break":
            parsed.append(PageBreak())
        elif kind == "analysis_reference":
            analysis_run_id = raw.get("analysis_run_id")
            parsed.append(
                AnalysisReference(
                    heading=str(raw.get("label") or analysis_run_id or "Analysis"),
                    available=bool(raw.get("available", True)),
                    analysis_run_id=str(analysis_run_id) if analysis_run_id is not None else None,
                    unavailable_text=raw.get("unavailable_text"),
                    children=tuple(_parse_blocks(raw.get("blocks", []))),
                )
            )
        else:
            raise ValueError(f"unsupported ReportDocument block type: {kind!r}")
    return parsed


def _find(entry: dict) -> Find:
    count = entry.get("count")
    pct = entry.get("percentage")
    stat = None
    if count is not None and pct is not None:
        stat = f"{count:,} / {pct}%"
    elif count is not None:
        stat = f"{count:,}"
    return Find(
        label=entry.get("label_en") or entry.get("label_zh") or "",
        detail=entry.get("detail_en"),
        stat=stat,
    )


def _find_zh(entry: dict) -> Find:
    count = entry.get("count")
    pct = entry.get("percentage")
    stat = None
    if count is not None and pct is not None:
        stat = f"{count:,} / {pct}%"
    elif count is not None:
        stat = f"{count:,}"
    return Find(
        label=entry.get("label_zh") or entry.get("label_en") or "",
        detail=entry.get("detail_zh"),
        stat=stat,
    )


def _bilingual_find_list(heading_zh: str, heading_en: str, finds: list[dict]) -> BilingualFindList | None:
    if not finds:
        return None
    return BilingualFindList(
        heading_zh=heading_zh,
        heading_en=heading_en,
        finds_zh=tuple(_find_zh(f) for f in finds),
        finds_en=tuple(_find(f) for f in finds),
    )


def _analysis_reference(section: dict) -> AnalysisReference:
    heading = f"{section['incident_display_id']} — {section['title']}"
    analysis = section.get("analysis")
    if analysis is None:
        return AnalysisReference(
            heading=heading,
            available=False,
            unavailable_text="No log analysis available yet for this incident.",
        )
    return AnalysisReference(
        heading=heading,
        available=True,
        summary=BilingualText(
            text_zh=analysis.get("summary_zh") or "",
            text_en=analysis.get("summary_en") or "",
            heading_zh="Chinese",
            heading_en="English",
        ),
        key_finds=_bilingual_find_list("重点发现 (Key Finds)", "Key Finds", analysis.get("key_finds") or []),
        secondary_finds=_bilingual_find_list(
            "次要发现 (Secondary Finds)", "Secondary Finds", analysis.get("secondary_finds") or []
        ),
        likely_cause=BilingualText(
            text_zh=f"最可能原因: {analysis['likely_cause_zh']}" if analysis.get("likely_cause_zh") else "",
            text_en=f"Likely cause: {analysis['likely_cause_en']}" if analysis.get("likely_cause_en") else "",
        )
        if analysis.get("likely_cause_zh") or analysis.get("likely_cause_en")
        else None,
        recommended_action=BilingualText(
            text_zh=f"建议措施: {analysis['recommended_action_zh']}" if analysis.get("recommended_action_zh") else "",
            text_en=f"Recommended action: {analysis['recommended_action_en']}"
            if analysis.get("recommended_action_en")
            else "",
        )
        if analysis.get("recommended_action_zh") or analysis.get("recommended_action_en")
        else None,
    )


def build_daily_report_document(result: dict) -> ReportDocument:
    """Report Assembly (Phase 10) for daily-alert-report: the one place
    that knows this skill's exact result shape (title/overview_en+zh/
    cross_incident_findings_en+zh/sections[...]). Everything downstream
    of this function — noc_bridge.docx_render.render_document — only
    ever sees generic blocks."""
    sections = result.get("sections", [])
    blocks: list[Block] = [Heading("Alerts", level=1)]

    if not sections:
        blocks.append(Paragraph("No incidents in this shift."))
    for i, section in enumerate(sections, start=1):
        metadata = (Metadata("Incident", section["incident_display_id"]), Metadata("Status", section["status"]))
        link = Link("Grafana Link", section["grafana_url"]) if section.get("grafana_url") else None
        log_file = LogFileReference(section["log_filename"]) if section.get("log_filename") else None
        screenshots = tuple(
            Screenshot(bucket=s["bucket"], object_key=s["object_key"], filename=s.get("filename"))
            for s in (section.get("screenshots") or [])
        )
        blocks.append(
            IncidentEvidence(
                heading=f"Alert #{i} - {section['title']}",
                metadata=metadata,
                link=link,
                log_file=log_file,
                screenshots=screenshots,
            )
        )

    blocks.append(Heading("General Summary", level=1))
    blocks.append(
        BilingualText(
            text_zh=result.get("overview_zh") or "",
            text_en=result.get("overview_en") or "",
            heading_zh="Chinese Summary",
            heading_en="English Summary",
        )
    )

    if result.get("cross_incident_findings_en") or result.get("cross_incident_findings_zh"):
        blocks.append(Heading("Cross-Incident Findings", level=1))
        blocks.append(
            BilingualText(
                text_zh=result.get("cross_incident_findings_zh") or "",
                text_en=result.get("cross_incident_findings_en") or "",
                heading_zh="Chinese",
                heading_en="English",
            )
        )

    blocks.append(Heading("Log Analysis", level=1))
    for section in sections:
        blocks.append(_analysis_reference(section))

    return ReportDocument(title=result["title"], blocks=tuple(blocks))
