"""Static cross-layer coherence checks for the provider-neutral architecture."""
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
    reports_router = read("apps/api/app/api/v1/routers/reports.py")
    analysis_router = read("apps/api/app/api/v1/routers/analysis.py")
    shift_report = read("apps/web/src/pages/ShiftReport.tsx")
    model = read("apps/api/app/models/models.py")
    health_module = read("apps/api/app/operational_health.py")
    runtime = read("apps/api/app/ai_runtime.py")
    report_schema = json.loads(read("skills/daily-alert-report/output.schema.json"))
    report_manifest = read("skills/daily-alert-report/skill.yaml")
    job_schema = json.loads(read("packages/contracts/job_message.schema.json"))
    job_protocol = json.loads(read("packages/contracts/job_protocol.json"))
    evidence_router = read("apps/api/app/api/v1/routers/evidence.py")
    health_router = read("apps/api/app/main.py")
    context = read("CONTEXT.md")

    require('"refresh_token"' not in token_schema, "TokenPair must not expose a refresh token")
    require("httponly=True" in auth_router, "refresh cookie must be HttpOnly")
    require("credentials: \"include\"" in http_client, "frontend requests must send the refresh cookie")
    require("localStorage" not in web_src, "frontend must not persist refresh credentials in localStorage")

    require("shift_incident_statement" in reports_router, "ReportSnapshot must use the shared shift scope")
    require("shiftId, limit: 0" in shift_report, "Shift Report readiness must request the complete shift scope")
    require('"shift_timezone": shift_timezone' in reports_router, "ReportSnapshot must freeze shift_timezone")
    require("require_ai_runtime()" in analysis_router and "require_ai_runtime()" in reports_router, "AI actions must stop at the runtime boundary")
    require("AI_RUNTIME_UNAVAILABLE_CODE" in runtime, "runtime boundary must expose an intentional unavailable code")

    require('"uq_shifts_one_active"' in model, "Shift must have a durable active-row invariant")
    require('"uq_reports_shift_version"' in model, "Report must have a durable shift/version identity")
    require("SkillSnapshot" in context and "ReportSnapshot" in context, "immutable provenance terms are missing")
    require("general_summary" in report_schema.get("required", []), "Daily Report must require general_summary")
    require(report_schema.get("additionalProperties") is False, "Daily Report schema must be strict")
    require("blocks" not in report_schema.get("properties", {}), "report layout must remain application-owned")
    require("incidents: all" in report_manifest and "analyses: all_available" in report_manifest, "report coverage must remain skill-owned")

    required_job_fields = set(job_schema.get("required", []))
    require({"job_id", "job_type", "object_refs", "correlation_id", "attempt"} <= required_job_fields, "job contract is missing core fields")
    require("protocol_version" in required_job_fields and job_protocol.get("protocol_version") == 1, "job protocol version must be explicit")
    require("EvidenceUploadIntent" in evidence_router and "body.bucket" not in evidence_router and "body.object_key" not in evidence_router, "evidence storage identity must remain server-owned")
    require('"/health/dependencies"' in health_router and '"/health/readiness"' in health_router, "operational health endpoints are missing")
    require("_check_database" in health_module and "_check_rabbitmq" in health_module and "_check_minio" in health_module, "core health checks are missing")

    print("coherence gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
