"""Skill-aware full analysis presentation exports.

The compact ReportFragment is for model reasoning. This module adapts the
frozen, full AnalysisRun result into renderer-neutral blocks so technical
detail is not lost merely because it was omitted from the Daily Report
prompt.
"""
from __future__ import annotations

from noc_bridge.report_document import Block, Find, FindList, Heading, Paragraph

ANALYSIS_PRESENTATION_CONTRACT = "analysis-presentation-v1"


def _find(value: dict, *, zh: bool) -> Find:
    count = value.get("count")
    percentage = value.get("percentage")
    stat = None
    if count is not None and percentage is not None:
        stat = f"{count:,} / {percentage}%"
    elif count is not None:
        stat = f"{count:,}"
    primary = "label_zh" if zh else "label_en"
    fallback = "label_en" if zh else "label_zh"
    detail = "detail_zh" if zh else "detail_en"
    return Find(
        label=str(value.get(primary) or value.get(fallback) or ""),
        detail=str(value.get(detail) or "") or None,
        stat=stat,
    )


def _find_list(result: dict, key: str, *, zh: bool):
    values = result.get(key) or []
    if not isinstance(values, list):
        return None
    values = [value for value in values if isinstance(value, dict)]
    if not values:
        return None
    return FindList(
        heading="Key Finds" if key == "key_finds" else "Secondary Finds",
        finds=tuple(_find(value, zh=zh) for value in values),
        language="Chinese" if zh else "English",
    )


def _language_section(result: dict, *, zh: bool) -> list[Block]:
    suffix = "_zh" if zh else "_en"
    language = "Chinese" if zh else "English"
    blocks: list[Block] = [Heading(language, level=3)]
    summary = str(result.get(f"summary{suffix}") or "")
    if summary:
        blocks.extend((Heading("Short Summary", level=4), Paragraph(summary)))
    for key in ("key_finds", "secondary_finds"):
        finding_list = _find_list(result, key, zh=zh)
        if finding_list:
            blocks.append(finding_list)
    for key, heading in (("likely_cause", "Likely Cause"), ("recommended_action", "Recommended Action")):
        value = str(result.get(f"{key}{suffix}") or "")
        if value:
            blocks.extend((Heading(heading, level=4), Paragraph(value)))
    return blocks


def build_analysis_presentation(result: dict | None, contract: str | None = None) -> tuple[Block, ...]:
    """Build the complete human-facing presentation without an AI call.

    A materially different analysis schema can provide ``report_presentation``
    as generic ReportDocument blocks. The fallback is the compatibility
    adapter for the current Log Triage result and retains all findings.
    """
    if not isinstance(result, dict):
        return ()
    if contract not in (None, ANALYSIS_PRESENTATION_CONTRACT):
        raise ValueError(f"unsupported analysis presentation contract: {contract}")
    explicit = result.get("report_presentation")
    if isinstance(explicit, dict) and isinstance(explicit.get("blocks"), list):
        from noc_bridge.report_document import build_report_document
        return tuple(build_report_document({"metadata": {}, "blocks": explicit["blocks"]}).blocks)
    blocks: list[Block] = []
    blocks.extend(_language_section(result, zh=True))
    blocks.extend(_language_section(result, zh=False))
    return tuple(blocks)
