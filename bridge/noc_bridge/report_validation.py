"""Validation for the historical ``daily_report_docx`` assembler.

This is deliberately outside generic bridge validation. It is a compatibility
adapter for immutable legacy report rows; the active report profile validates
its narrative-only ReportPlan and produces ReportDocument blocks without a
Cross-Incident Findings field.
"""
from __future__ import annotations

from noc_bridge.validation import OutputValidationError


def _validate_find(entry: object, path: str) -> None:
    if not isinstance(entry, dict):
        raise OutputValidationError(f"{path} must be an object")
    for field in ("label_en", "label_zh", "detail_en", "detail_zh"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            raise OutputValidationError(f"{path}.{field} must be a non-empty string")
    if entry.get("count") is not None and not isinstance(entry["count"], int):
        raise OutputValidationError(f"{path}.count must be an integer or null")
    if entry.get("percentage") is not None and not isinstance(entry["percentage"], (int, float)):
        raise OutputValidationError(f"{path}.percentage must be a number or null")


def _validate_analysis(analysis: dict, path: str) -> None:
    required = ["summary_en", "summary_zh", "key_finds", "secondary_finds", "likely_cause_en", "likely_cause_zh", "recommended_action_en", "recommended_action_zh", "severity_signal", "confidence"]
    missing = [field for field in required if field not in analysis]
    if missing:
        raise OutputValidationError(f"{path} missing fields: {missing}")
    for field in ("summary_en", "summary_zh", "likely_cause_en", "likely_cause_zh", "recommended_action_en", "recommended_action_zh"):
        if not isinstance(analysis[field], str) or not analysis[field].strip():
            raise OutputValidationError(f"{path}.{field} must be a non-empty string")
    if analysis["severity_signal"] not in {"low", "medium", "high", "critical"}:
        raise OutputValidationError(f"{path}.severity_signal is invalid")
    if not isinstance(analysis["confidence"], (int, float)) or not 0 <= analysis["confidence"] <= 1:
        raise OutputValidationError(f"{path}.confidence must be in [0, 1]")
    if not isinstance(analysis["key_finds"], list) or not analysis["key_finds"]:
        raise OutputValidationError(f"{path}.key_finds must be a non-empty list")
    if not isinstance(analysis["secondary_finds"], list):
        raise OutputValidationError(f"{path}.secondary_finds must be a list")
    for name in ("key_finds", "secondary_finds"):
        for i, entry in enumerate(analysis[name]):
            _validate_find(entry, f"{path}.{name}[{i}]")


def validate_merged_daily_report(output: dict) -> None:
    required = ["title", "overview_en", "overview_zh", "sections"]
    missing = [field for field in required if field not in output]
    if missing:
        raise OutputValidationError(f"daily_report output missing fields: {missing}")
    for field in ("title", "overview_en", "overview_zh"):
        if not isinstance(output[field], str) or not output[field].strip():
            raise OutputValidationError(f"{field} must be a non-empty string")
    if not isinstance(output["sections"], list):
        raise OutputValidationError("sections must be a list")
    for i, section in enumerate(output["sections"]):
        if not isinstance(section, dict):
            raise OutputValidationError(f"sections[{i}] must be an object")
        for field in ("incident_display_id", "title", "status"):
            if not isinstance(section.get(field), str) or not section[field].strip():
                raise OutputValidationError(f"sections[{i}].{field} must be a non-empty string")
        if section.get("analysis") is not None:
            _validate_analysis(section["analysis"], f"sections[{i}].analysis")
