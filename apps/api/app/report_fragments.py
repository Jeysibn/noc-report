"""Stable analysis-to-report export.

Analysis skills own their complete result schemas.  Reports consume only this
small semantic projection, which is frozen into ReportSnapshot and therefore
does not make a report skill depend on private Log Triage field names.
"""
from __future__ import annotations


REPORT_FRAGMENT_CONTRACT = "report-fragment-v1"


def _pair(value_zh: object, value_en: object) -> dict[str, str]:
    return {"zh": str(value_zh or ""), "en": str(value_en or "")}


def build_report_fragment(result: dict | None) -> dict | None:
    """Export a deliberately small stable context from an analysis result.

    New analysis skills may provide ``report_context`` directly.  The
    fallback is the one compatibility adapter for the current Log Triage
    result; the Daily Report code never reads those private fields.
    """
    if not isinstance(result, dict):
        return None
    context = result.get("report_context")
    if isinstance(context, dict):
        fragment = {
            "contract": REPORT_FRAGMENT_CONTRACT,
            "headline": str(context.get("headline") or ""),
            "severity": context.get("severity"),
            "summary": _pair(
                (context.get("summary") or {}).get("zh") if isinstance(context.get("summary"), dict) else context.get("summary_zh"),
                (context.get("summary") or {}).get("en") if isinstance(context.get("summary"), dict) else context.get("summary_en"),
            ),
            "findings": list(context.get("findings") or [])[:5],
        }
        # Cause/action are compatibility-only report-fragment fields. The
        # active canonical analysis has no such fields and therefore does not
        # carry empty slots through the Daily Report prompt.
        if any(key in context for key in ("likely_cause", "likely_cause_zh", "likely_cause_en")):
            fragment["likely_cause"] = _pair(
                (context.get("likely_cause") or {}).get("zh") if isinstance(context.get("likely_cause"), dict) else context.get("likely_cause_zh"),
                (context.get("likely_cause") or {}).get("en") if isinstance(context.get("likely_cause"), dict) else context.get("likely_cause_en"),
            )
        if any(key in context for key in ("recommended_action", "recommended_action_zh", "recommended_action_en")):
            fragment["recommended_action"] = _pair(
                (context.get("recommended_action") or {}).get("zh") if isinstance(context.get("recommended_action"), dict) else context.get("recommended_action_zh"),
                (context.get("recommended_action") or {}).get("en") if isinstance(context.get("recommended_action"), dict) else context.get("recommended_action_en"),
            )
        return fragment

    findings = []
    for finding in (result.get("key_finds") or [])[:5]:
        if isinstance(finding, dict):
            findings.append(
                {
                    "zh": str(finding.get("label_zh") or finding.get("label_en") or ""),
                    "en": str(finding.get("label_en") or finding.get("label_zh") or ""),
                    "detail_zh": str(finding.get("detail_zh") or ""),
                    "detail_en": str(finding.get("detail_en") or ""),
                }
            )
    fragment = {
        "contract": REPORT_FRAGMENT_CONTRACT,
        "headline": str(result.get("summary_en") or result.get("summary_zh") or ""),
        "severity": result.get("severity_signal"),
        "summary": _pair(result.get("summary_zh"), result.get("summary_en")),
        "findings": findings,
    }
    # Preserve old stored output for compatibility, but never add these
    # fields to a new canonical result that does not contain them.
    if any(key in result for key in ("likely_cause_zh", "likely_cause_en")):
        fragment["likely_cause"] = _pair(result.get("likely_cause_zh"), result.get("likely_cause_en"))
    if any(key in result for key in ("recommended_action_zh", "recommended_action_en")):
        fragment["recommended_action"] = _pair(
            result.get("recommended_action_zh"), result.get("recommended_action_en")
        )
    return fragment
