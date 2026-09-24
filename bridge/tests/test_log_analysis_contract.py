from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from docx import Document

from noc_bridge.analysis_presentation import build_analysis_presentation
from noc_bridge.docx_render import render_document
from noc_bridge.report_composition import compose_report
from noc_bridge.report_document import AnalysisReference, FindList, Heading
from noc_bridge.report_document_json import document_to_preview_json
from noc_bridge.validation import OutputValidationError, validate_log_triage_result, validate_output


FIXTURE = Path(__file__).parent / "fixtures" / "sanitized_golden_daily_report_snapshot.json"
SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def _analysis() -> dict:
    return _fixture()["incidents"][0]["analysis"]


def _valid_result() -> dict:
    return {
        "total_entries": 581,
        "summary_zh": "该日志包含581条记录，覆盖约1小时。主要异常集中在查询超时；其余错误模式相互独立。",
        "summary_en": "The log contains 581 entries across roughly one hour. Query timeouts dominate; the remaining error patterns are separate.",
        "key_finds": [{
            "id": "query-timeout",
            "label_zh": "查询超时",
            "label_en": "Query timeout",
            "count": 215,
            "percentage": 37.01,
            "pattern_ids": ["p001"],
            "detail_zh": "查询达到超时阈值。",
            "detail_en": "The query reached its timeout threshold.",
        }],
        "secondary_finds": [],
        "severity_signal": "high",
        "confidence": 0.9,
    }


def test_sanitized_golden_fixture_has_canonical_shape_and_exact_counts():
    result = _analysis()
    validate_log_triage_result(result)
    assert result["total_entries"] == 581
    assert len(result["key_finds"]) == 4
    assert len(result["secondary_finds"]) >= 5
    assert sum(
        finding["count"]
        for group in ("key_finds", "secondary_finds")
        for finding in result[group]
    ) == result["total_entries"]
    assert "sanitized-ranking-cache-2026-09-20.json" == _fixture()["incidents"][0]["log_filename"]


def test_semantic_validation_accepts_581_215_37_01_and_zero_entry_result():
    validate_log_triage_result(_valid_result())
    zero = _valid_result()
    zero["total_entries"] = 0
    zero["key_finds"][0]["count"] = 0
    zero["key_finds"][0]["percentage"] = 0.0
    validate_log_triage_result(zero)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda result: result["key_finds"][0].update({"percentage": 18.0}), "percentage is inconsistent"),
        (lambda result: result["key_finds"][0].update({"count": -1}), "non-negative integer"),
        (lambda result: result["secondary_finds"].append({**result["key_finds"][0], "id": "second", "pattern_ids": ["p001"]}), "duplicate finding identity"),
        (lambda result: result["secondary_finds"].append({**result["key_finds"][0], "pattern_ids": ["p002"]}), "duplicate finding id"),
        (lambda result: result["key_finds"][0].update({"detail_zh": ""}), "too short"),
    ],
)
def test_invalid_numeric_or_bilingual_identity_is_rejected(mutate, message):
    result = _valid_result()
    mutate(result)
    if message == "too short":
        with pytest.raises(OutputValidationError, match="too short"):
            validate_output("log_triage", result, skill_name="log-triage-summary", skills_dir=SKILLS_DIR)
    else:
        with pytest.raises(OutputValidationError, match=message):
            validate_log_triage_result(result)


def test_new_result_rejects_cause_action_fields_and_unsupported_shared_causation():
    with pytest.raises(OutputValidationError, match="Additional properties"):
        validate_output(
            "log_triage",
            {**_valid_result(), "likely_cause_en": "shared outage", "likely_cause_zh": "共同故障"},
            skill_name="log-triage-summary",
            skills_dir=SKILLS_DIR,
        )

    result = _valid_result()
    result["summary_en"] = (
        "The log contains 581 entries. Elasticsearch and partner errors share one root cause "
        "because they occurred in the same hour."
    )
    with pytest.raises(OutputValidationError, match="unsupported causal assertion"):
        validate_log_triage_result(result)


def test_new_result_rejects_unsupported_speculative_causation():
    result = _valid_result()
    result["summary_en"] = (
        "The log contains 581 entries. The timeout and partner error patterns are likely due to one shared outage."
    )
    with pytest.raises(OutputValidationError, match="unsupported causal assertion"):
        validate_log_triage_result(result)


def test_new_result_rejects_full_finding_detail_repeated_in_summary():
    result = _valid_result()
    result["summary_en"] = (
        "The log contains 581 entries. The query reached its timeout threshold repeatedly."
    )
    result["key_finds"][0]["detail_en"] = "The query reached its timeout threshold repeatedly."
    with pytest.raises(OutputValidationError, match="repeats its full explanation"):
        validate_log_triage_result(result)


def test_key_find_detail_is_substantive_and_secondary_detail_is_brief():
    result = _valid_result()
    result["key_finds"][0]["detail_en"] = (
        "The timeout occurred in ThirdOrderEsDao.queryForGameRank during cache reloads. "
        "The log records repeated threshold breaches without a linked downstream exception."
    )
    result["key_finds"][0]["detail_zh"] = (
        "超时发生在缓存重载期间的排行榜查询路径。日志记录了多次达到阈值的事件，"
        "但未提供与其他故障直接关联的下游异常链。"
    )
    secondary = {**result["key_finds"][0], "id": "secondary", "pattern_ids": ["p002"], "count": 17, "percentage": 2.93}
    secondary["detail_en"] = "A lower-volume payment-status query failure was recorded."
    secondary["detail_zh"] = "记录到少量支付状态查询失败。"
    result["secondary_finds"] = [secondary]
    validate_log_triage_result(result)

    secondary["detail_en"] = "First sentence. Second sentence."
    with pytest.raises(OutputValidationError, match="secondary_finds.*no more than 1 sentence"):
        validate_log_triage_result(result)


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("summary_en", "The log contains two dominant failures. The parser rejected the VALUES clause because", "incomplete phrase"),
        ("summary_zh", "日志显示两个主要故障。解析器在VALUES子句处中断", "complete-sentence punctuation"),
        ("detail_en", "The parser rejected the VALUES clause and", "incomplete phrase"),
        ("detail_zh", "解析器拒绝了VALUES子句……", "appears truncated"),
    ],
)
def test_incomplete_narratives_are_rejected(field, value, message):
    result = _valid_result()
    if field.startswith("summary"):
        result[field] = value
    else:
        result["key_finds"][0][field] = value
    with pytest.raises(OutputValidationError, match=message):
        validate_log_triage_result(result)


def test_web_and_docx_semantics_use_the_same_canonical_presentation(tmp_path):
    snapshot = _fixture()
    document = compose_report(
        {"general_summary": {"zh": "班次摘要。", "en": "Shift summary."}},
        snapshot,
    )
    analysis_block = next(block for block in document.blocks if isinstance(block, AnalysisReference))
    children = list(analysis_block.children)
    headings = [
        block.text if isinstance(block, Heading) else block.heading
        for block in children
        if isinstance(block, (Heading, FindList))
    ]
    assert headings == [
        "Log Analysis", "Chinese", "Short Summary", "Key Finds", "Secondary Finds",
        "English", "Short Summary", "Key Finds", "Secondary Finds",
    ]

    chinese = [block for block in children if isinstance(block, FindList) and block.language == "Chinese"]
    english = [block for block in children if isinstance(block, FindList) and block.language == "English"]
    assert [[find.stat for find in block.finds] for block in chinese] == [[find.stat for find in block.finds] for block in english]
    assert [[find.label for find in block.finds] for block in chinese] != [[find.label for find in block.finds] for block in english]

    preview, _screenshots = document_to_preview_json(document)
    preview_analysis = next(block for block in preview["blocks"] if block.get("type") == "analysis_reference")
    preview_headings = [
        child.get("text") if child.get("type") == "heading" else child.get("heading")
        for child in preview_analysis["children"]
        if child.get("type") in {"heading", "find_list"}
    ]
    assert preview_headings == headings
    assert "Most Likely Cause" not in str(preview)
    assert "Recommended Action" not in str(preview)
    assert "Cross-Incident Findings" not in str(preview)

    destination = tmp_path / "sanitized-golden-report.docx"
    render_document(document, destination, lambda *_args: _PNG)
    text = "\n".join(paragraph.text for paragraph in Document(str(destination)).paragraphs)
    assert "Chinese" in text and text.index("Chinese") < text.index("English")
    assert "Log File: sanitized-ranking-cache-2026-09-20.json" in text
    assert "Ranking query timeout" in text
    assert "Most Likely Cause" not in text
    assert "Recommended Action" not in text
    assert "Cross-Incident Findings" not in text


def test_canonical_adapter_is_deterministic_and_contains_no_legacy_sections():
    first = build_analysis_presentation(_analysis())
    second = build_analysis_presentation(_analysis())
    assert first == second
    assert "Likely Cause" not in str(first)
    assert "Recommended Action" not in str(first)
