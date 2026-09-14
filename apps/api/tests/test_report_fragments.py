from app.report_fragments import REPORT_FRAGMENT_CONTRACT, build_report_fragment


def test_legacy_log_triage_result_exports_small_stable_fragment():
    fragment = build_report_fragment({
        "summary_en": "Payment worker failed",
        "summary_zh": "支付工作进程失败",
        "severity_signal": "high",
        "likely_cause_en": "Downstream timeout",
        "likely_cause_zh": "下游超时",
        "recommended_action_en": "Inspect dependency",
        "recommended_action_zh": "检查依赖",
        "key_finds": [{"label_en": "Timeout", "label_zh": "超时", "detail_en": "x", "detail_zh": "x"}],
        "secondary_finds": [{"label_en": "private field", "label_zh": "private field"}],
    })
    assert fragment["contract"] == REPORT_FRAGMENT_CONTRACT
    assert fragment["summary"]["en"] == "Payment worker failed"
    assert "secondary_finds" not in fragment
    assert len(fragment["findings"]) == 1


def test_materially_different_analysis_schema_can_export_same_contract():
    fragment = build_report_fragment({
        "executive_summary": "Payment worker failed",
        "report_context": {
            "headline": "Payment worker failed",
            "severity": "high",
            "summary": {"zh": "支付工作进程失败", "en": "Payment worker failed"},
            "likely_cause": {"zh": "下游超时", "en": "Downstream timeout"},
            "recommended_action": {"zh": "检查依赖", "en": "Inspect dependency"},
            "findings": [],
        },
    })
    assert fragment["contract"] == "report-fragment-v1"
    assert fragment["summary"]["en"] == "Payment worker failed"
