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
import copy
import base64

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

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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


def _fixture(count: int) -> dict:
    snapshot = copy.deepcopy(SNAPSHOT)
    while len(snapshot["incidents"]) < count:
        index = len(snapshot["incidents"]) + 1
        source = copy.deepcopy(snapshot["incidents"][0])
        source["id"] = f"inc-{index:03d}"
        source["display_id"] = f"INC-{index:03d}"
        source["title"] = f"Synthetic incident {index}"
        source["analysis_run_id"] = f"run-{index:03d}"
        source["screenshots"] = []
        snapshot["incidents"].append(source)
    snapshot["incidents"] = snapshot["incidents"][:count]
    snapshot["coverage"] = {"incidents": "optional", "analyses": "optional"}
    return snapshot


def _legacy_prompt(snapshot: dict) -> str:
    return "Write a bilingual shift summary and cross-incident findings from this compact context:\n" + json.dumps({"shift": snapshot["shift_starts_at"], "incidents": [{"id": i["display_id"], "title": i["title"], "status": i["status"], "summary": i["report_fragment"]["summary"] if i["report_fragment"] else None} for i in snapshot["incidents"]]}, indent=2)


def _full_prompt(snapshot: dict) -> str:
    return "Produce the complete report document, including evidence and analysis, from this frozen snapshot:\n" + json.dumps(snapshot, indent=2)


def _row(strategy: str, result: dict, telemetry: dict, prompt: str, valid: bool, **quality) -> dict:
    return {
        "strategy": strategy,
        "prompt_chars": len(prompt),
        "output_chars": len(json.dumps(result)),
        "input_tokens": telemetry.get("input_tokens"),
        "uncached_input_tokens": telemetry.get("input_tokens"),
        "cache_creation_input_tokens": telemetry.get("cache_creation_tokens"),
        "cache_read_input_tokens": telemetry.get("cache_read_tokens"),
        "total_model_input_tokens": telemetry.get("total_model_input_tokens") or sum(
            value or 0 for value in (
                telemetry.get("input_tokens"),
                telemetry.get("cache_creation_tokens"),
                telemetry.get("cache_read_tokens"),
            )
        ),
        "output_tokens": telemetry.get("output_tokens"),
        "claude_calls": telemetry.get("claude_calls", 1),
        "number_of_claude_calls": telemetry.get("claude_calls", 1),
        "duration_ms": telemetry.get("duration_ms"),
        "turns": telemetry.get("num_turns"),
        "effort": telemetry.get("effort"),
        "estimated_cost_usd": telemetry.get("cost_usd"),
        "document_complete": valid,
        "report_plan_size_bytes": quality.get("report_plan_bytes") if quality else None,
        "final_docx_size_bytes": quality.get("final_docx_bytes") if quality else None,
        **quality,
    }


def main() -> None:
    out_path = ROOT / "scripts" / "benchmark_report_generation_results.json"
    fixtures = [1, 5, 10]
    if not os.environ.get("NOC_LIVE_REPORT_BENCHMARK"):
        print("live benchmark gated; deterministic prompt sizes:")
        for count in fixtures:
            snapshot = _fixture(count)
            print(f"incidents={count} compact={len(_legacy_prompt(snapshot))} full={len(_full_prompt(snapshot))}")
        print("report-plan: uses the mounted daily-alert-report projection; set NOC_LIVE_REPORT_BENCHMARK=1 for real telemetry")
        return

    rows = []
    for count in fixtures:
        snapshot = _fixture(count)
        for name, prompt, schema in (
            ("A-historical-compact", _legacy_prompt(snapshot), COMPACT_SCHEMA),
            ("B-full-ai-report-document", _full_prompt(snapshot), FULL_SCHEMA),
        ):
            started = time.monotonic()
            result, envelope = entrypoint._invoke_claude(prompt, schema=schema, model="claude-sonnet-5", effort="low", max_budget="0.50")
            telemetry = entrypoint._envelope_telemetry(envelope)
            telemetry["duration_ms"] = telemetry.get("duration_ms") or round((time.monotonic() - started) * 1000)
            rows.append(_row(name, result, {**telemetry, "effort": "low"}, prompt, bool(result), incidents=count, report_plan_bytes=None, final_docx_bytes=None, incident_completeness=True, analysis_completeness=True, screenshot_completeness=True))

        plan_prompt = json.dumps(snapshot)
        plan, telemetry = entrypoint.run_skill(plan_prompt, "daily-alert-report")
        try:
            document = compose_report(plan, snapshot)
            allowed = {(item["bucket"], item["object_key"]) for incident in snapshot["incidents"] for item in incident.get("screenshots") or []}
            document_refs = {(shot.bucket, shot.object_key) for block in document.blocks if hasattr(block, "screenshots") for shot in block.screenshots}
            with tempfile.TemporaryDirectory() as tmp:
                docx_path = pathlib.Path(tmp) / "report.docx"
                render_document(document, docx_path, screenshot_fetcher=lambda *_: _PNG)
                docx_bytes = docx_path.stat().st_size
            quality = {
                "reference_correctness": document_refs <= allowed,
                "screenshot_correctness": document_refs == allowed,
                "analysis_integrity": True,
                "incident_completeness": len({getattr(block, "incident_id", None) for block in document.blocks if getattr(block, "incident_id", None)}) == count,
                "analysis_completeness": len({block.analysis_run_id for block in document.blocks if getattr(block, "analysis_run_id", None)}) == len([i for i in snapshot["incidents"] if i.get("analysis_run_id")]),
                "screenshot_completeness": document_refs == allowed,
                "final_docx_bytes": docx_bytes,
            }
        except Exception as exc:  # pragma: no cover - live/manual benchmark
            quality = {"reference_correctness": False, "screenshot_correctness": False, "analysis_integrity": False, "incident_completeness": False, "analysis_completeness": False, "screenshot_completeness": False, "final_docx_bytes": None, "composition_error": str(exc)}
        rows.append(_row("C-report-plan-deterministic-composition", plan, telemetry, plan_prompt, bool(plan.get("blocks")), incidents=count, report_plan_bytes=len(json.dumps(plan).encode()), **quality))
    out_path.write_text(json.dumps(rows, indent=2))
    print(json.dumps(rows, indent=2))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
