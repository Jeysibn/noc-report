from preprocessing import compact_log, preprocess_log


def test_preprocess_log_extracts_reproducible_operational_facts():
    result = preprocess_log(
        "2026-09-23T01:00:00Z ERROR GET /payments trace_id=t-1 ValueError\n"
        "2026-09-23T01:01:00Z INFO GET /payments trace_id=t-1\n"
    )

    assert result["total_entries"] == 2
    assert result["error_count"] == 1
    assert result["exception_count"] == 1
    assert result["endpoint_counts"]["/payments"] == 2
    assert result["trace_counts"]["t-1"] == 2
    assert result["timestamp_start"] < result["timestamp_end"]


def test_compact_log_is_bounded_and_marks_omitted_bytes():
    result = compact_log("a" * 100, max_chars=40)
    assert len(result) <= 100
    assert "deterministic preprocessing omitted" in result
