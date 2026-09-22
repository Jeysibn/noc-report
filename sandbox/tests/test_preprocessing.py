"""Regression tests for sandbox/entrypoint.py's deterministic log
preprocessing (NOC cost-optimization mission, Phase 5/13).

entrypoint.py is a script, not a package (it's baked into the sandbox image
and run standalone), so it's loaded here via importlib from its file path
rather than a normal import.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

_ENTRYPOINT_PATH = pathlib.Path(__file__).resolve().parents[1] / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("sandbox_entrypoint", _ENTRYPOINT_PATH)
entrypoint = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(entrypoint)


def _make_noisy_log_with_one_rare_severe_error() -> str:
    """80,000 WARN retries + 1 OutOfMemoryError — the exact scenario called
    out in the mission brief as something that must never be dropped."""
    lines = [f"WARN retry attempt for request req-{i:06d} timed out" for i in range(80_000)]
    lines.insert(40_000, "FATAL java.lang.OutOfMemoryError: Java heap space at com.acme.Worker.run(Worker.java:42)")
    return "\n".join(lines)


def test_rare_severe_event_survives_compaction():
    """Regression test: one critical error among thousands of low-severity
    lines must be preserved in the compacted evidence sent to Claude."""
    log_text = _make_noisy_log_with_one_rare_severe_error()
    assert len(log_text) > entrypoint.MAX_LOG_CHARS  # sanity: this should trigger compaction

    compacted = entrypoint._compact_log_if_oversized(log_text)

    assert "OutOfMemoryError" in compacted
    assert "[SEVERE]" in compacted


def test_frequent_pattern_still_wins_a_slot_on_its_own_merits():
    log_text = _make_noisy_log_with_one_rare_severe_error()
    compacted = entrypoint._compact_log_if_oversized(log_text)
    assert "retry attempt" in compacted


def test_small_log_is_not_compacted():
    log_text = "a single short log line\n" * 10
    assert entrypoint._compact_log_if_oversized(log_text) == log_text


def test_is_severe_matches_expected_markers():
    assert entrypoint._is_severe("FATAL: node crashed")
    assert entrypoint._is_severe("java.lang.OutOfMemoryError: heap space")
    assert entrypoint._is_severe("possible data loss detected during flush")
    assert not entrypoint._is_severe("INFO: request completed in 12ms")


def test_severity_is_independent_of_frequency_ranking():
    """A severe pattern that occurs only once, deep inside a log with more
    than MAX_PATTERN_GROUPS distinct frequent patterns, must still appear —
    this is the core "never use frequency alone" guarantee."""
    lines = []
    for i in range(entrypoint.MAX_PATTERN_GROUPS + 20):
        # each pattern repeated enough times to dominate frequency ranking,
        # and each pattern distinct (varying non-normalized text) so they
        # don't collapse into one signature.
        lines.extend([f"WARN distinct-pattern-{i} occurred"] * 50)
    lines.append("FATAL rare single-occurrence StackOverflowError in module X")
    log_text = "\n".join(lines)

    compacted = entrypoint._compact_log_if_oversized(log_text)

    assert "StackOverflowError" in compacted
    assert "[SEVERE]" in compacted


# --- AI cost-optimization mission Phase 2, Issue 7: field-aware
# normalization (the old blanket `\d+` catch-all incorrectly collapsed
# semantically different, meaningful numbers into the same signature). ---


def test_http_403_and_500_are_not_collapsed_into_same_signature():
    sig_403 = entrypoint._signature("ERROR request to /api/payments failed with HTTP 403 Forbidden")
    sig_500 = entrypoint._signature("ERROR request to /api/payments failed with HTTP 500 Internal Server Error")
    assert sig_403 != sig_500


def test_different_business_error_codes_are_not_collapsed():
    sig_a = entrypoint._signature("BusinessException: errorCode=1001 insufficient balance")
    sig_b = entrypoint._signature("BusinessException: errorCode=2007 account locked")
    assert sig_a != sig_b


def test_dynamic_trace_id_is_normalized_so_same_error_collapses():
    line_a = "ERROR request failed trace_id=abc123def request_id=xyz789"
    line_b = "ERROR request failed trace_id=qqq999www request_id=zzz111"
    assert entrypoint._signature(line_a) == entrypoint._signature(line_b)


def test_dynamic_user_and_record_id_are_normalized():
    line_a = "WARN duplicate key for userId=48213 recordId=90021"
    line_b = "WARN duplicate key for userId=77 recordId=4"
    assert entrypoint._signature(line_a) == entrypoint._signature(line_b)


def test_operator_templates_aggregate_variable_values_and_endpoints():
    """Presentation grouping is template-level, not one finding per event."""
    parse_a = "ERROR -abc123-/api/front/paddleOcr/runpodHook Failed to parse error response as JSON"
    parse_b = "ERROR -different-request-/api/admin/userKyc/pass Failed to parse error response as JSON"
    maya_a = "WARN orderNo=R01549062598845145088 GET /pay/queryOrderStatus"
    maya_b = "WARN orderNo=R01549677011197431808 GET /pay/queryOrderStatus"

    assert entrypoint._family_signature(parse_a) == "NDRP response parse failure"
    assert entrypoint._family_signature(parse_b) == "NDRP response parse failure"
    assert entrypoint._family_signature(maya_a) == "Maya payment status query failed"
    assert entrypoint._family_signature(maya_b) == "Maya payment status query failed"


def test_template_accounting_does_not_create_one_pattern_per_event():
    lines = []
    for index in range(100):
        lines.append(
            f"WARN trace_id={index:032x} -request-{index}/api/front/paddleOcr/runpodHook "
            "Failed to parse error response as JSON"
        )
        lines.append(
            f"WARN orderNo=R{index:020d} GET https://payment.example/pay/queryOrderStatus"
        )
        lines.append(
            f"ERROR userId={index} [VIP] Transaction synchronization is not active"
        )

    order, _groups, counts, _severe = entrypoint._pattern_stats(lines, entrypoint._family_signature)

    assert len(order) == 3
    assert sum(counts.values()) == 300
    assert sorted(counts.values()) == [100, 100, 100]
    assert all(str(index) not in signature for index in range(100) for signature in order)


def test_thread_suffix_is_normalized():
    line_a = "INFO handled by Thread-42"
    line_b = "INFO handled by Thread-9001"
    assert entrypoint._signature(line_a) == entrypoint._signature(line_b)


def test_root_cause_stack_frame_is_preserved_through_compaction():
    """The exact app class/method/line naming a severe error's root cause
    must survive compaction, not just a generic exception name — this is
    a single physical log line (as many real appenders emit: exception
    header + "Caused by: <class>.<method>(<file>:<line>)" on one line),
    so it is matched by _is_severe and kept regardless of frequency."""
    lines = [f"WARN retry attempt for request req-{i:06d} timed out" for i in range(80_000)]
    lines.insert(
        40_000,
        "FATAL java.lang.OutOfMemoryError: Java heap space, "
        "Caused by: com.acme.payments.LedgerWorker.flush(LedgerWorker.java:88)",
    )
    log_text = "\n".join(lines)
    compacted = entrypoint._compact_log_if_oversized(log_text)
    assert "LedgerWorker.flush" in compacted
    assert "LedgerWorker.java:88" in compacted


# --- Skill Runtime mission Phase 15: deterministic grounding for logs that
# never get compacted (previously: no deterministic extraction ran at all
# below MAX_LOG_CHARS, so count/percentage fields for a small log were pure
# model estimates while the same fields for a large log were exact). ---


def test_deterministic_stats_appendix_reports_exact_counts_for_a_small_log():
    log_text = "\n".join(
        ["INFO request completed"] * 7 + ["WARN retry attempt for request"] * 3
    )
    appendix = entrypoint._deterministic_stats_appendix(log_text)
    assert appendix is not None
    assert "occurs 7 time(s)" in appendix
    assert "occurs 3 time(s)" in appendix
    assert "exact, not estimates" in appendix


def test_deterministic_stats_appendix_flags_a_rare_severe_pattern():
    log_text = "\n".join(
        ["INFO request completed"] * 50 + ["FATAL OutOfMemoryError: heap space"]
    )
    appendix = entrypoint._deterministic_stats_appendix(log_text)
    assert "[SEVERE]" in appendix
    assert "OutOfMemoryError" in appendix


def test_deterministic_stats_appendix_is_none_for_an_empty_log():
    assert entrypoint._deterministic_stats_appendix("") is None


def test_log_triage_counts_are_recomputed_from_pattern_ids():
    log_text = "\n".join(["ERROR timeout"] * 7 + ["WARN retry"] * 3)
    result = {
        "summary_en": "summary",
        "summary_zh": "摘要",
        "key_finds": [{
            "label_en": "timeouts", "label_zh": "超时", "count": 999,
            "percentage": 99.9, "pattern_ids": ["p001"],
            "detail_en": "d", "detail_zh": "细节",
        }],
        "secondary_finds": [{
            "label_en": "retries", "label_zh": "重试", "count": 1,
            "percentage": 1.0, "pattern_ids": ["p002"],
            "detail_en": "d", "detail_zh": "细节",
        }],
    }

    normalized = entrypoint._reconcile_log_triage_counts(result, log_text)

    assert normalized["total_entries"] == 10
    assert normalized["key_finds"][0]["count"] == 7
    assert normalized["key_finds"][0]["percentage"] == 70.0
    assert normalized["secondary_finds"][0]["count"] == 3
    assert normalized["secondary_finds"][0]["percentage"] == 30.0
    assert sum(
        finding["count"] or 0
        for group in ("key_finds", "secondary_finds")
        for finding in normalized[group]
    ) == 10
    assert all(
        "other" not in (finding.get("pattern_ids") or [])
        for group in ("key_finds", "secondary_finds")
        for finding in normalized[group]
    )


def test_omitted_families_are_identified_individually_not_hidden_in_other():
    log_text = "\n".join(
        ["WARN api request failed request_id=req-1"] * 3
        + ["ERROR database timeout userId=101"] * 2
        + ["WARN cache miss recordId=abc"]
    )
    normalized = entrypoint._reconcile_log_triage_counts(
        {
            "key_finds": [{
                "label_en": "api failures", "label_zh": "API失败",
                "count": 999, "percentage": 99.0,
                "pattern_ids": ["p001"],
                "detail_en": "d", "detail_zh": "细节",
            }],
            "secondary_finds": [],
        },
        log_text,
    )
    findings = normalized["key_finds"] + normalized["secondary_finds"]
    assert normalized["total_entries"] == 6
    assert sum(finding["count"] or 0 for finding in findings) == 6
    assert len(findings) == 3
    assert all("Other log entries" not in finding["label_en"] for finding in findings)
    assert all("other" not in (finding.get("pattern_ids") or []) for finding in findings)


def test_log_triage_adds_exact_remainder_for_unselected_patterns():
    log_text = "\n".join(["ERROR timeout"] * 7 + ["WARN retry"] * 3 + ["INFO done"] * 2)
    result = {
        "key_finds": [{
            "label_en": "timeouts", "label_zh": "超时", "count": 7,
            "percentage": 70.0, "pattern_ids": ["p001"],
            "detail_en": "d", "detail_zh": "细节",
        }],
        "secondary_finds": [],
    }

    normalized = entrypoint._reconcile_log_triage_counts(result, log_text)
    remainder = normalized["secondary_finds"][-1]

    assert normalized["total_entries"] == 12
    assert remainder["pattern_ids"] == ["p003"]
    assert remainder["count"] == 2
    assert remainder["percentage"] == round(2 / 12 * 100, 2)
    assert sum(finding["count"] or 0 for finding in normalized["key_finds"] + normalized["secondary_finds"]) == 12


def test_unmatched_narrative_finding_is_not_mislabeled_as_exact_other():
    normalized = entrypoint._reconcile_log_triage_counts(
        {
            "key_finds": [{
                "label_en": "ambiguous", "label_zh": "不明确", "count": 999,
                "percentage": 99.0, "detail_en": "d", "detail_zh": "细节",
            }],
            "secondary_finds": [],
        },
        "ERROR timeout\nWARN retry",
    )

    assert normalized["key_finds"][0]["pattern_ids"] == ["unquantified"]
    assert normalized["key_finds"][0]["count"] is None
    assert normalized["secondary_finds"][-1]["pattern_ids"] == ["p002"]
    assert normalized["secondary_finds"][-1]["count"] == 1


def test_low_volume_related_template_is_grouped_under_dominant_finding():
    log_text = "\n".join(
        ["ERROR file does not exist"] * 100
        + ["ERROR input file List is null or empty"] * 2
        + ["ERROR payment authorization failed"]
    )
    normalized = entrypoint._reconcile_log_triage_counts(
        {
            "key_finds": [{
                "label_en": "Export task output file missing", "label_zh": "导出任务输出文件缺失",
                "count": 100, "percentage": 97.0, "pattern_ids": ["p001"],
                "detail_en": "The export output file is missing.", "detail_zh": "导出输出文件缺失。",
            }],
            "secondary_finds": [],
        },
        log_text,
    )

    findings = normalized["key_finds"] + normalized["secondary_finds"]
    assert len(findings) == 2
    assert normalized["key_finds"][0]["pattern_ids"] == ["p001", "p002"]
    assert normalized["key_finds"][0]["count"] == 102
    assert normalized["key_finds"][0]["percentage"] == round(102 / 103 * 100, 2)
    assert sum(finding["count"] or 0 for finding in findings) == 103
    assert "Related lower-volume templates" in normalized["key_finds"][0]["detail_en"]


def test_related_grouping_keeps_distinct_http_statuses_separate():
    log_text = "\n".join(
        ["ERROR NDRP API returned HTTP 400 invalid search string"] * 100
        + ["ERROR NDRP API error HTTP 503 upstream unavailable"] * 2
    )
    normalized = entrypoint._reconcile_log_triage_counts(
        {
            "key_finds": [{
                "label_en": "HTTP 400", "label_zh": "HTTP 400",
                "count": 100, "percentage": 98.0, "pattern_ids": ["p001"],
                "detail_en": "d", "detail_zh": "细节",
            }],
            "secondary_finds": [],
        },
        log_text,
    )

    assert len(normalized["key_finds"] + normalized["secondary_finds"]) == 2
    assert normalized["key_finds"][0]["pattern_ids"] == ["p001"]
    assert normalized["secondary_finds"][0]["pattern_ids"] == ["p002"]


def test_run_skill_appends_deterministic_grounding_for_a_small_log(monkeypatch, tmp_path):
    """run_skill's log-triage-summary branch must append the grounding
    appendix (not just compact when oversized) so a small log's prompt
    still carries exact counts, never only the raw log."""
    seen = {}

    def _fake_invoke_claude(prompt, *, schema, model, effort, max_budget):
        seen["prompt"] = prompt
        return {"summary": "ok"}, {}

    monkeypatch.setattr(entrypoint, "_invoke_claude", _fake_invoke_claude)
    monkeypatch.setattr(entrypoint, "_load_output_schema", lambda skill_name: {})
    monkeypatch.setattr(entrypoint, "_load_input_contract", lambda skill_name: ("log.txt", "intro"))
    monkeypatch.setattr(entrypoint, "_escalation_reason", lambda result: None)
    skill_dir = tmp_path / "log-triage-summary"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("skill instructions")
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)

    log_text = "\n".join(["INFO request completed"] * 5 + ["WARN retry attempt"] * 2)
    entrypoint.run_skill(log_text, "log-triage-summary")

    assert "exact, not estimates" in seen["prompt"]
    assert "occurs 5 time(s)" in seen["prompt"]


def test_deterministic_profile_uses_json_log_metadata_and_causes():
    log_text = json.dumps(
        [
            {
                "date": "2026-09-22T10:00:00Z",
                "fields": {
                    "detected_level": "ERROR",
                    "app": "cache-service",
                    "timestamp": "2026-09-22T10:00:00Z",
                },
                "line": (
                    "ERROR CacheService -- refresh failed\\n"
                    "Caused by: java.util.concurrent.TimeoutException: waiting"
                ),
            },
            {
                "date": "2026-09-22T11:00:00Z",
                "fields": {
                    "detected_level": "WARN",
                    "service_name": "ndrp-client",
                    "timestamp": "2026-09-22T11:00:00Z",
                },
                "line": "WARN NdrpClient -- upstream unavailable",
            },
        ]
    )

    profile = entrypoint._deterministic_log_profile(log_text)

    assert profile is not None
    assert "total_entries=2" in profile
    assert "cache-service" in profile
    assert "ndrp-client" in profile
    assert "time_range: 2026-09-22T10:00:00Z -> 2026-09-22T11:00:00Z" in profile
    assert "TimeoutException" in profile


# --- Skill Runtime mission Phase 20: prove a brand-new skill with a wholly
# different output shape runs end-to-end through run_skill with zero
# entrypoint.py changes — schema, prompt intro, and input contract all come
# from files under the skill's own directory, never a hard-coded Python
# shape (Phase 3/4). ---


def test_run_skill_handles_a_synthetic_skill_with_an_unrelated_output_shape(monkeypatch, tmp_path):
    seen = {}

    def _fake_invoke_claude(prompt, *, schema, model, effort, max_budget):
        seen["schema"] = schema
        # A shape that shares nothing with log-triage-summary or
        # daily-alert-report's schemas (no "summary", no "sections", no
        # bilingual _en/_zh pairs at all) — proves the sandbox imposes no
        # assumed structure of its own.
        return {"widgets": [{"widget_id": "w1", "count": 3}], "total_widgets": 3}, {}

    monkeypatch.setattr(entrypoint, "_invoke_claude", _fake_invoke_claude)
    monkeypatch.setattr(entrypoint, "_load_input_contract", lambda skill_name: ("input.txt", "intro"))
    monkeypatch.setattr(entrypoint, "_escalation_reason", lambda result: None)

    skill_dir = tmp_path / "widget-counter"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("count the widgets")
    synthetic_schema = {
        "type": "object",
        "required": ["widgets", "total_widgets"],
        "properties": {
            "widgets": {"type": "array"},
            "total_widgets": {"type": "integer"},
        },
    }
    (skill_dir / "output.schema.json").write_text(json.dumps(synthetic_schema))
    monkeypatch.setattr(entrypoint, "SKILLS_DIR", tmp_path)

    result, _telemetry = entrypoint.run_skill("some widgets: a, b, c", "widget-counter")

    assert seen["schema"] == synthetic_schema
    assert result == {"widgets": [{"widget_id": "w1", "count": 3}], "total_widgets": 3}
