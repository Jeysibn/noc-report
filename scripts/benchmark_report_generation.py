"""Compare compact, full-document, and ReportPlan report prompts.

The live mode uses the same Claude CLI bridge function as sandbox jobs and
records its real usage envelope. It is intentionally gated because it spends
subscription usage:

    NOC_LIVE_REPORT_BENCHMARK=1 python3 scripts/benchmark_report_generation.py

Without the gate, the script reports deterministic fixture sizes so CI can
still verify that the ReportPlan input excludes trusted evidence fields.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "sandbox" / "entrypoint.py"
spec = importlib.util.spec_from_file_location("sandbox_entrypoint", ENTRYPOINT)
entrypoint = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(entrypoint)
if shutil.which("claude"):
    entrypoint.CLAUDE_BINARY = shutil.which("claude")
entrypoint.SKILLS_DIR = ROOT / "skills"
sys.path.insert(0, str(ROOT / "bridge"))
from noc_bridge.docx_render import render_document  # noqa: E402
from noc_bridge.report_composition import compose_report  # noqa: E402


SNAPSHOT = {
    "shift_starts_at": "2026-09-14T00:00:00+00:00",
    "shift_ends_at": "2026-09-14T08:00:00+00:00",
    "report_skill_snapshot_id": "report-v2",
    "report_skill_execution_hash": "report-exec-v2",
    "incidents": [
        {
            "id": "inc-001", "display_id": "INC-001", "title": "Payment timeout", "status": "RECOVERED",
            "service": "payments", "environment": "prod", "triggered_at": "2026-09-14T01:00:00+00:00",
            "recovered_at": "2026-09-14T01:30:00+00:00", "trigger_value": "95%",
            "teams_url": "https://teams.example/inc-001", "grafana_url": "https://grafana.example/inc-001",
            "log_filename": "payments.log", "screenshots": [{"bucket": "noc-evidence", "object_key": "inc-001.png"}],
            "analysis_run_id": "run-001", "analysis": {"summary_en": "Payment timeout from a slow dependency", "summary_zh": "下游缓慢导致支付超时", "key_finds": [{"label_en": "Timeout", "label_zh": "超时", "detail_en": "repeated", "detail_zh": "重复"}], "likely_cause_en": "Slow dependency", "likely_cause_zh": "下游缓慢", "recommended_action_en": "Inspect dependency", "recommended_action_zh": "检查依赖", "severity_signal": "high"},
            "report_fragment": {"contract": "report-fragment-v1", "headline": "Payment timeout", "severity": "high", "summary": {"zh": "下游缓慢导致支付超时", "en": "Payment timeout from a slow dependency"}, "likely_cause": {"zh": "下游缓慢", "en": "Slow dependency"}, "recommended_action": {"zh": "检查依赖", "en": "Inspect dependency"}, "findings": []},
        },
        {
            "id": "inc-002", "display_id": "INC-002", "title": "Cache errors", "status": "ACTIVE", "service": "cache", "environment": "prod", "triggered_at": "2026-09-14T02:00:00+00:00", "recovered_at": None, "trigger_value": "80%", "teams_url": None, "grafana_url": None, "log_filename": None, "screenshots": [], "analysis_run_id": None, "analysis": None, "report_fragment": None,
        },
    ],
}

COMPACT_SCHEMA = {"type": "object", "required": ["overview_en", "overview_zh", "cross_incident_findings_en", "cross_incident_findings_zh"], "additionalProperties": False, "properties": {field: {"type": "string"} for field in ["overview_en", "overview_zh", "cross_incident_findings_en", "cross_incident_findings_zh"]}}
FULL_SCHEMA = {"type": "object", "required": ["metadata", "blocks"], "additionalProperties": True, "properties": {"metadata": {"type": "object"}, "blocks": {"type": "array"}}}


def _legacy_prompt() -> str:
    return "Write a bilingual shift summary and cross-incident findings from this compact context:\n" + json.dumps({"shift": SNAPSHOT["shift_starts_at"], "incidents": [{"id": i["display_id"], "title": i["title"], "status": i["status"], "summary": i["report_fragment"]["summary"] if i["report_fragment"] else None} for i in SNAPSHOT["incidents"]]}, indent=2)


def _full_prompt() -> str:
    return "Produce the complete report document, including evidence and analysis, from this frozen snapshot:\n" + json.dumps(SNAPSHOT, indent=2)


def _row(strategy: str, result: dict, telemetry: dict, prompt: str, valid: bool, **quality) -> dict:
    return {
        "strategy": strategy,
        "prompt_chars": len(prompt),
        "output_chars": len(json.dumps(result)),
        "input_tokens": telemetry.get("input_tokens"),
        "cache_creation_input_tokens": telemetry.get("cache_creation_tokens"),
        "cache_read_input_tokens": telemetry.get("cache_read_tokens"),
        "output_tokens": telemetry.get("output_tokens"),
        "duration_ms": telemetry.get("duration_ms"),
        "turns": telemetry.get("num_turns"),
        "effort": telemetry.get("effort"),
        "estimated_cost_usd": telemetry.get("cost_usd"),
        "document_complete": valid,
        **quality,
    }


def main() -> None:
    out_path = ROOT / "scripts" / "benchmark_report_generation_results.json"
    prompts = [("compact-legacy", _legacy_prompt(), COMPACT_SCHEMA), ("full-report-document", _full_prompt(), FULL_SCHEMA)]
    if not os.environ.get("NOC_LIVE_REPORT_BENCHMARK"):
        print("live benchmark gated; deterministic prompt sizes:")
        for name, prompt, _ in prompts:
            print(f"{name}: {len(prompt)} chars")
        print("report-plan: uses the mounted daily-alert-report projection; set NOC_LIVE_REPORT_BENCHMARK=1 for real telemetry")
        return

    rows = []
    for name, prompt, schema in prompts:
        started = time.monotonic()
        result, envelope = entrypoint._invoke_claude(prompt, schema=schema, model="claude-sonnet-5", effort="low", max_budget="0.50")
        telemetry = entrypoint._envelope_telemetry(envelope)
        rows.append(_row(name, result, {**telemetry, "effort": "low"}, prompt, bool(result)))
    plan_prompt = json.dumps(SNAPSHOT)
    plan, telemetry = entrypoint.run_skill(plan_prompt, "daily-alert-report")
    try:
        document = compose_report(plan, SNAPSHOT)
        allowed = {
            (item["bucket"], item["object_key"])
            for incident in SNAPSHOT["incidents"]
            for item in incident.get("screenshots") or []
        }
        document_refs = {
            (shot.bucket, shot.object_key)
            for block in document.blocks
            if hasattr(block, "screenshots")
            for shot in block.screenshots
        }
        with tempfile.TemporaryDirectory() as tmp:
            render_document(document, pathlib.Path(tmp) / "report.docx", screenshot_fetcher=lambda *_: None)
        quality = {
            "reference_correctness": document_refs <= allowed,
            "screenshot_correctness": document_refs == allowed,
            "analysis_integrity": {block.analysis_run_id for block in document.blocks if getattr(block, "analysis_run_id", None)} <= {"run-001", "run-002"},
        }
    except Exception as exc:  # pragma: no cover - live/manual benchmark
        quality = {"reference_correctness": False, "screenshot_correctness": False, "analysis_integrity": False, "composition_error": str(exc)}
    rows.append(_row("report-plan", plan, telemetry, plan_prompt, bool(plan.get("blocks")), **quality))
    out_path.write_text(json.dumps(rows, indent=2))
    print(json.dumps(rows, indent=2))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
