"""Regression tests for sandbox/entrypoint.py's deterministic log
preprocessing (NOC cost-optimization mission, Phase 5/13).

entrypoint.py is a script, not a package (it's baked into the sandbox image
and run standalone), so it's loaded here via importlib from its file path
rather than a normal import.
"""
from __future__ import annotations

import importlib.util
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
