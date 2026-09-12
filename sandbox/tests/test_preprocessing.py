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
