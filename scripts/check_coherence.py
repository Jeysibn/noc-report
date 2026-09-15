"""Static release gate for the repository's cross-layer contracts.

This is intentionally a small, dependency-free check rather than a second
schema framework. Runtime tests remain the source of behavioral proof; this
gate catches the particular branch-drift failures that can otherwise leave
the API, bridge, frontend, skills, migrations, and documentation describing
different generations of the system.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    auth_router = read("apps/api/app/api/v1/routers/auth.py")
    token_schema = read("apps/api/app/schemas/schemas.py")
    http_client = read("apps/web/src/lib/http.ts")
    web_src = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "apps/web/src").rglob("*.ts*"))
    incidents_router = read("apps/api/app/api/v1/routers/incidents.py")
    reports_router = read("apps/api/app/api/v1/routers/reports.py")
    shift_report = read("apps/web/src/pages/ShiftReport.tsx")
    bridge_db = read("bridge/noc_bridge/db.py")
    bridge_service = read("bridge/noc_bridge/service.py")
    report_schema = json.loads(read("skills/daily-alert-report/output.schema.json"))
    report_manifest = read("skills/daily-alert-report/skill.yaml")
    model = read("apps/api/app/models/models.py")
    current_migration = read("apps/api/alembic/versions/f9a0b1c2d3e4_analysis_current_unique.py")
    job_schema = json.loads(read("packages/contracts/job_message.schema.json"))
    context = read("CONTEXT.md")

    # Authentication is cookie/session based end-to-end.
    require('"refresh_token"' not in token_schema, "TokenPair must not expose a refresh token")
    require("httponly=True" in auth_router, "refresh cookie must be HttpOnly")
    require("with_for_update()" in auth_router and "session.revoked_at = now" in auth_router, "refresh must lock and rotate sessions")
    require("credentials: \"include\"" in http_client, "frontend requests must send the refresh cookie")
    require("localStorage" not in web_src, "frontend must not persist refresh credentials in localStorage")

    # Readiness and report freezing share the canonical shift scope.
    require("shift_incident_statement" in incidents_router, "incident readiness must use the shared shift scope")
    require("shift_incident_statement" in reports_router, "ReportSnapshot must use the shared shift scope")
    require("shiftId, limit: 0" in shift_report, "Shift Report readiness must request the complete shift scope")
    require('"shift_timezone": shift_timezone' in reports_router, "ReportSnapshot must freeze shift_timezone")

    # AI usage is cumulative and policy resolution is centralized in the bridge.
    require("paid_ai_calls_used +" in bridge_db, "paid AI usage must add to the durable cumulative total")
    require("def record_paid_ai_calls" in bridge_db, "paid AI consumption must have one accounting seam")
    require("ai_governance.effective_model" in bridge_service and "ai_governance.effective_effort" in bridge_service, "bridge must resolve Auto through AI governance")

    # Daily Report is narrative-only; deterministic coverage stays in composition.
    require("general_summary" in report_schema.get("required", []), "Daily Report must require general_summary")
    require(report_schema.get("additionalProperties") is False, "Daily Report schema must reject undeclared fields")
    require("blocks" not in report_schema.get("properties", {}), "Daily Report schema must not ask Claude for deterministic blocks")
    require("incidents: all" in report_manifest and "analyses: all_available" in report_manifest, "Daily Report manifest must own complete coverage")

    # Current AnalysisRun and shared job contract must be durable/explicit.
    require("uq_analysis_runs_current_incident" in model and "postgresql_where=text(\"current IS TRUE\")" in model, "ORM must declare the partial current-run index")
    require("uq_analysis_runs_current_incident" in current_migration and "current IS TRUE" in current_migration, "Alembic must create the current-run partial index")
    required_job_fields = set(job_schema.get("required", []))
    require({"job_id", "job_type", "object_refs", "correlation_id", "attempt"} <= required_job_fields, "job message schema is missing core protocol fields")

    for term in (
        "Shift", "Incident", "SkillSnapshot", "ReportFragment", "AnalysisPresentation",
        "ReportPlan", "ReportSnapshot", "ReportDocument", "Report Composition", "AI Usage Budget",
    ):
        require(f"**{term}**" in context, f"CONTEXT.md is missing domain term: {term}")

    print("coherence gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
