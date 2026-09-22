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


def test_preprocess_log_normalizes_a_json_array_and_extracts_http_identity_facts():
    result = preprocess_log(
        '[{"timestamp":"2026-09-23T01:00:00Z","level":"ERROR","status_code":504,"path":"/search","trace_id":"t-1","user_id":"u-1","record_id":"r-1","message":"Elasticsearch timeout"},'
        '{"timestamp":"2026-09-23T01:01:30Z","level":"INFO","status_code":200,"path":"/search","trace_id":"t-1"}]'
    )

    assert result["total_entries"] == 2
    assert result["http_status_counts"] == {"200": 1, "504": 1}
    assert result["user_id_counts"] == {"u-1": 1}
    assert result["record_id_counts"] == {"r-1": 1}
    assert result["duration_seconds"] == 90.0
    assert result["normalization"].startswith("json-array")
