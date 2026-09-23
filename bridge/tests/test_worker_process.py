import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import noc_bridge.worker as worker_module
from noc_bridge.hermes import HermesResult
from noc_bridge.worker import WorkerMetrics, process_message


ROOT = Path(__file__).resolve().parents[2]


class _Connection:
    def close(self):
        return None


def test_process_message_verifies_preprocesses_calls_fake_hermes_and_uploads(monkeypatch, tmp_path):
    uploaded = []
    evidence_text = "2026-09-23T01:00:00Z ERROR GET /payments ElasticsearchTimeoutException\n"

    monkeypatch.setattr(worker_module, "get_db_connection", lambda _url: _Connection())
    monkeypatch.setattr(
        worker_module,
        "_incident_and_evidence",
        lambda _conn, _message: (
            {"id": "incident-1", "title": "Payment timeout", "triggered_at": None, "recovered_at": None},
            {"bucket": "noc-evidence", "key": "incident-1/log", "sha256": "a" * 64, "content_version": "v1", "filename": "payments.log"},
        ),
    )
    monkeypatch.setattr(worker_module, "get_client", lambda _settings: object())

    def fake_materialize(_conn, _skill_name, _skill_hash, *, snapshot_id, execution_hash, dest_root):
        destination = Path(dest_root) / "log-triage-summary"
        destination.mkdir(parents=True)
        source = ROOT / "skills" / "log-triage-summary"
        shutil.copy2(source / "SKILL.md", destination / "SKILL.md")
        shutil.copy2(source / "output.schema.json", destination / "output.schema.json")

    monkeypatch.setattr(worker_module, "materialize_snapshot", fake_materialize)

    def fake_download(_client, *, bucket, object_key, dest_path, expected_sha256, version_id):
        Path(dest_path).write_text(evidence_text, encoding="utf-8")

    monkeypatch.setattr(worker_module, "download_object", fake_download)

    def fake_upload(_client, *, bucket, object_key, src_path):
        uploaded.append((bucket, object_key, json.loads(Path(src_path).read_text(encoding="utf-8"))))

    monkeypatch.setattr(worker_module, "upload_artifact", fake_upload)

    class FakeHermes:
        def __init__(self, _settings):
            pass

        def analyze(self, *, payload, skill_md, output_schema, repair_hint=None):
            pattern_id = payload["statistics"]["pattern_manifest"][0]["id"]
            return HermesResult(
                result={
                    "total_entries": 999,
                    "summary_zh": "日志显示重复错误。证据仅限于该日志。",
                    "summary_en": "The log shows a repeated error. The evidence is limited to this file.",
                    "key_finds": [{
                        "id": "timeout",
                        "label_en": "Timeout",
                        "label_zh": "超时",
                        "count": 999,
                        "percentage": 99.9,
                        "pattern_ids": [pattern_id],
                        "detail_en": "The timeout pattern is repeated in the payment path.",
                        "detail_zh": "支付路径中重复出现超时模式。",
                    }],
                    "secondary_finds": [],
                    "severity_signal": "high",
                    "confidence": 0.8,
                },
                telemetry={"provider": "fake", "model": "fake-model"},
            )

    settings = SimpleNamespace(
        database_url="postgresql://test",
        minio_bucket_job_artifacts="noc-job-artifacts",
        hermes_max_output_attempts=1,
        hermes_version="test-hermes",
    )
    result = process_message(
        {
            "job_id": "job-1",
            "job_type": "log_triage",
            "skill_name": "log-triage-summary",
            "skill_hash": None,
            "skill_snapshot_id": "snapshot-1",
            "skill_execution_hash": "execution-1",
        },
        settings=settings,
        metrics=WorkerMetrics(),
        hermes_client_factory=FakeHermes,
    )

    assert result["output"]["total_entries"] == 1
    assert result["output"]["key_finds"][0]["count"] == 1
    assert result["telemetry"]["evidence_sha256"] == "a" * 64
    assert result["telemetry"]["runtime_version"] == "test-hermes"
    assert [item[1] for item in uploaded] == ["jobs/job-1/result.json", "jobs/job-1/telemetry.json"]


def test_process_message_retries_semantically_invalid_model_output(monkeypatch, tmp_path):
    uploaded = []
    evidence_text = "2026-09-23T01:00:00Z ERROR payment database failure\n"

    monkeypatch.setattr(worker_module, "get_db_connection", lambda _url: _Connection())
    monkeypatch.setattr(
        worker_module,
        "_incident_and_evidence",
        lambda _conn, _message: (
            {"id": "incident-1", "title": "Payment failure", "triggered_at": None, "recovered_at": None},
            {"bucket": "noc-evidence", "key": "incident-1/log", "sha256": "b" * 64, "content_version": "v1", "filename": "payments.log"},
        ),
    )
    monkeypatch.setattr(worker_module, "get_client", lambda _settings: object())

    def fake_materialize(_conn, _skill_name, _skill_hash, *, snapshot_id, execution_hash, dest_root):
        destination = Path(dest_root) / "log-triage-summary"
        destination.mkdir(parents=True)
        source = ROOT / "skills" / "log-triage-summary"
        shutil.copy2(source / "SKILL.md", destination / "SKILL.md")
        shutil.copy2(source / "output.schema.json", destination / "output.schema.json")

    monkeypatch.setattr(worker_module, "materialize_snapshot", fake_materialize)
    monkeypatch.setattr(
        worker_module,
        "download_object",
        lambda _client, *, bucket, object_key, dest_path, expected_sha256, version_id: Path(dest_path).write_text(evidence_text),
    )
    monkeypatch.setattr(
        worker_module,
        "upload_artifact",
        lambda _client, *, bucket, object_key, src_path: uploaded.append(object_key),
    )

    class FakeHermes:
        calls = 0

        def __init__(self, _settings):
            pass

        def analyze(self, *, payload, skill_md, output_schema, repair_hint=None):
            self.calls += 1
            if self.calls == 2:
                assert repair_hint is not None
                assert "exact pattern IDs" in repair_hint
            pattern_id = payload["statistics"]["pattern_manifest"][0]["id"]
            result = {
                "total_entries": 1,
                "summary_zh": (
                    "日志显示一次支付错误。证据仅限于该日志。需要进一步确认。"
                    "仍需检查下游依赖。当前无法确认更广泛影响。"
                ),
                "summary_en": (
                    "The log shows one payment error. Evidence is limited to this file. "
                    "Further confirmation is needed. Downstream dependencies need review. "
                    "Broader impact is not confirmed."
                ),
                "key_finds": [{
                    "id": "payment-error",
                    "label_en": "Payment error",
                    "label_zh": "支付错误",
                    "count": 1 if self.calls > 1 else 0,
                    "percentage": 100.0 if self.calls == 1 else None,
                    "pattern_ids": ["unquantified"] if self.calls > 1 else ["unknown-pattern"],
                    "detail_en": "The payment path logged one database failure.",
                    "detail_zh": "支付路径记录了一次数据库故障。",
                }],
                "secondary_finds": [],
                "severity_signal": "high",
                "confidence": 0.8,
            }
            return HermesResult(result=result, telemetry={"provider": "fake", "model": "fake-model"})

    settings = SimpleNamespace(
        database_url="postgresql://test",
        minio_bucket_job_artifacts="noc-job-artifacts",
        hermes_max_output_attempts=2,
        hermes_version="test-hermes",
    )
    result = process_message(
        {
            "job_id": "job-retry-1",
            "job_type": "log_triage",
            "skill_name": "log-triage-summary",
            "skill_hash": None,
            "skill_snapshot_id": "snapshot-1",
            "skill_execution_hash": "execution-1",
        },
        settings=settings,
        metrics=WorkerMetrics(),
        hermes_client_factory=FakeHermes,
    )

    assert result["output"]["key_finds"][0]["pattern_ids"] != ["unknown-pattern"]
    assert result["telemetry"]["output_attempts"] == 2
    assert uploaded == ["jobs/job-retry-1/result.json", "jobs/job-retry-1/telemetry.json"]
