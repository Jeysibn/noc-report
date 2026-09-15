import io
from datetime import datetime, timezone

import httpx
from PIL import Image, ImageDraw

from app.core.ocr import OcrLine, OcrResult
from app.models.models import AuditLog
from tests.conftest import auth_headers, make_user


def _make_alert_screenshot_bytes() -> bytes:
    img = Image.new("RGB", (500, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 15), "AppService Error Rate High", fill="black")
    draw.text((10, 55), "Triggered 09:31:22", fill="black")
    draw.text((10, 90), "Trigger value 193K", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_incident_with_uploaded_evidence(client, headers) -> tuple[str, str]:
    incident = client.post(
        "/api/v1/incidents",
        json={
            "title": "AppService error spike",
            "service": "app-service",
            "environment": "production",
            "triggered_at": datetime.now(timezone.utc).isoformat(),
        },
        headers=headers,
    ).json()

    upload_req = client.post(
        f"/api/v1/incidents/{incident['id']}/evidence/upload-url",
        json={"evidence_type": "ALERT_SCREENSHOT", "filename": "alert.png", "content_type": "image/png"},
        headers=headers,
    ).json()

    httpx.put(
        upload_req["upload_url"],
        content=_make_alert_screenshot_bytes(),
        headers={"Content-Type": "image/png"},
    )

    evidence = client.post(
        f"/api/v1/incidents/{incident['id']}/evidence/complete",
        json={
            "evidence_type": "ALERT_SCREENSHOT",
            "bucket": upload_req["bucket"],
            "object_key": upload_req["object_key"],
            "original_filename": "alert.png",
            "mime_type": "image/png",
        },
        headers=headers,
    ).json()

    return incident["id"], evidence["id"]


def test_ocr_extracts_real_text_from_screenshot(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    _, evidence_id = _create_incident_with_uploaded_evidence(client, headers)

    resp = client.post(f"/api/v1/evidence/{evidence_id}/ocr", headers=headers)
    assert resp.status_code == 201
    run = resp.json()
    assert run["status"] == "REVIEW_REQUIRED"
    assert run["engine"] == "paddleocr"
    assert "AppService Error Rate High" in run["raw_text"]

    fields = run["extracted_json"]["fields"]
    assert "alert_title" in fields
    assert fields["alert_title"]["confidence"] > 0.5
    assert "triggered_at" in fields
    assert fields["triggered_at"]["value"] == "09:31:22"
    assert "trigger_value" in fields
    assert fields["trigger_value"]["value"].upper().replace(" ", "") == "193K"


def test_get_ocr_run(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    _, evidence_id = _create_incident_with_uploaded_evidence(client, headers)

    created = client.post(f"/api/v1/evidence/{evidence_id}/ocr", headers=headers).json()

    fetched = client.get(f"/api/v1/ocr-runs/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]


def test_ocr_on_missing_evidence_404(client, db_session):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")

    resp = client.post(
        "/api/v1/evidence/00000000-0000-0000-0000-000000000000/ocr", headers=headers
    )
    assert resp.status_code == 404


def test_incident_prefill_is_review_only_and_attaches_after_confirmation(client, db_session, monkeypatch):
    make_user(db_session, "operator1", "NOC")
    headers = auth_headers(client, "operator1")
    monkeypatch.setattr(
        "app.api.v1.routers.ocr.run_ocr",
        lambda _content: OcrResult(
            raw_text="Alert: PaymentGatewayTimeout\nService: payments-api\nEnvironment: production\nTriggered: 2026-09-15 01:00:00+08:00",
            lines=[
                OcrLine("Alert: PaymentGatewayTimeout", 0.99, []),
                OcrLine("Service: payments-api", 0.98, []),
                OcrLine("Environment: production", 0.98, []),
                OcrLine("Triggered: 2026-09-15 01:00:00+08:00", 0.96, []),
            ],
        ),
    )

    response = client.post(
        "/api/v1/ocr/prefill",
        files={"file": ("alert.png", b"not-a-real-image-but-the-engine-is-mocked", "image/png")},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    prefill = response.json()
    assert prefill["status"] == "REVIEW_REQUIRED"
    assert prefill["incident_id"] is None
    assert prefill["prefill_json"]["service"]["source_text"] == "Service: payments-api"

    incident = client.post(
        "/api/v1/incidents",
        json={
            "title": "Operator corrected title",
            "service": "payments-api",
            "environment": "production",
            "triggered_at": "2026-09-15T01:00:00+08:00",
        },
        headers=headers,
    ).json()
    attached = client.post(
        f"/api/v1/ocr/prefills/{prefill['id']}/attach/{incident['id']}",
        headers=headers,
    )
    assert attached.status_code == 201, attached.text
    assert attached.json()["evidence_type"] == "ALERT_SCREENSHOT"
    correction_audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "incident.prefill.attached")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert correction_audit is not None
    assert correction_audit.metadata_json["corrections"]["title"]["final"] == "Operator corrected title"
