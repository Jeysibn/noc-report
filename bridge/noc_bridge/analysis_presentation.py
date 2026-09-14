"""Skill-aware full analysis presentation exports.

The compact ReportFragment is for model reasoning. This module adapts the
frozen, full AnalysisRun result into renderer-neutral blocks so technical
detail is not lost merely because it was omitted from the Daily Report
prompt.
"""
from __future__ import annotations

from noc_bridge.report_document import BilingualFindList, BilingualText, Block, Find, Heading

ANALYSIS_PRESENTATION_CONTRACT = "analysis-presentation-v1"


def _text(result: dict, zh_key: str, en_key: str, *, zh_heading: str | None = None, en_heading: str | None = None):
    zh = str(result.get(zh_key) or "")
    en = str(result.get(en_key) or "")
    if not zh and not en:
        return None
    return BilingualText(zh, en, heading_zh=zh_heading, heading_en=en_heading)


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


def _find_list(result: dict, key: str, heading_zh: str, heading_en: str):
    values = result.get(key) or []
    if not isinstance(values, list):
        return None
    values = [value for value in values if isinstance(value, dict)]
    if not values:
        return None
    return BilingualFindList(
        heading_zh=heading_zh,
        heading_en=heading_en,
        finds_zh=tuple(_find(value, zh=True) for value in values),
        finds_en=tuple(_find(value, zh=False) for value in values),
    )


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
    summary = _text(result, "summary_zh", "summary_en", zh_heading="Chinese", en_heading="English")
    if summary:
        blocks.append(summary)
    key_finds = _find_list(result, "key_finds", "重点发现 (Key Finds)", "Key Finds")
    if key_finds:
        blocks.append(key_finds)
    secondary_finds = _find_list(result, "secondary_finds", "次要发现 (Secondary Finds)", "Secondary Finds")
    if secondary_finds:
        blocks.append(secondary_finds)
    likely_cause = _text(result, "likely_cause_zh", "likely_cause_en")
    if likely_cause:
        blocks.extend((Heading("Likely Cause", level=3), likely_cause))
    recommended_action = _text(result, "recommended_action_zh", "recommended_action_en")
    if recommended_action:
        blocks.extend((Heading("Recommended Action", level=3), recommended_action))
    return tuple(blocks)
