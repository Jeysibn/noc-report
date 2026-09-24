import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import noc_bridge.worker as worker_module
from noc_bridge.daily_report import build_daily_report_input
from noc_bridge.hermes import HermesResult
from noc_bridge.validation import validate_daily_report_result
from noc_bridge.worker import WorkerMetrics, process_message


ROOT = Path(__file__).resolve().parents[2]


def test_daily_report_projection_excludes_application_storage_details():
    payload = build_daily_report_input(
        {
            "shift_starts_at": "2026-09-24T00:00:00Z",
            "shift_ends_at": None,
            "shift_display_name": "Night",
            "shift_timezone": "Asia/Manila",
            "incidents": [
                {
                    "display_id": "INC-1001",
                    "title": "Database timeout",
                    "status": "recovered",
                    "service": "payments",
                    "environment": "production",
                    "triggered_at": "2026-09-24T01:00:00Z",
                    "recovered_at": "2026-09-24T01:10:00Z",
                    "grafana_url": "https://grafana.example.invalid/private",
                    "log_evidence": {"bucket": "secret", "object_key": "private/log"},
                    "report_fragment": {
                        "summary": {"zh": "数据库超时。", "en": "Database timeout."},
                        "findings": [],
                    },
                }
            ],
        }
    )
    encoded = json.dumps(payload, ensure_ascii=False)
    assert payload["schema_version"] == "daily-report-context-v1"
    assert "grafana.example.invalid" not in encoded
    assert "object_key" not in encoded
    assert "INC-1001" not in encoded
    assert "2026-09-24T01:00:00Z" not in encoded
    assert payload["incidents"][0]["report_fragment"]["summary"]["zh"] == "数据库超时。"


def test_daily_report_worker_calls_dedicated_profile_and_returns_renderer_artifacts(monkeypatch, tmp_path):
    uploaded = []

    class Connection:
        def close(self):
            pass

    class Storage:
        def get_object(self, **_kwargs):
            return {"Body": SimpleNamespace(read=lambda: b"{}")}

    snapshot = {
        "shift_starts_at": "2026-09-24T00:00:00Z",
        "shift_ends_at": None,
        "shift_display_name": "Night",
        "shift_timezone": "Asia/Manila",
        "composition_profile": "noc-daily-report-v1",
        "coverage": {"incidents": "all", "analyses": "all_available"},
        "incidents": [],
    }

    monkeypatch.setattr(worker_module, "get_db_connection", lambda _url: Connection())
    monkeypatch.setattr(worker_module, "get_client", lambda _settings: Storage())

    def materialize(_conn, _skill_name, _skill_hash, *, snapshot_id, execution_hash, dest_root):
        destination = Path(dest_root) / "daily-alert-report"
        destination.mkdir(parents=True)
        source = ROOT / "skills" / "daily-alert-report"
        for filename in ("SKILL.md", "output.schema.json", "skill.yaml"):
            shutil.copy2(source / filename, destination / filename)

    monkeypatch.setattr(worker_module, "materialize_snapshot", materialize)
    monkeypatch.setattr(
        worker_module,
        "download_object",
        lambda _client, *, bucket, object_key, dest_path, expected_sha256, version_id: Path(dest_path).write_text(
            json.dumps(snapshot), encoding="utf-8"
        ),
    )
    monkeypatch.setattr(worker_module, "load_saved_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_module, "existing_report_artifacts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker_module,
        "compose_and_render",
        lambda plan, snapshot, *, docx_path, screenshot_fetcher: ({"title": "Daily"}, []),
    )
    monkeypatch.setattr(
        worker_module,
        "upload_artifact",
        lambda _client, *, bucket, object_key, src_path: uploaded.append((bucket, object_key)),
    )
    monkeypatch.setattr(
        worker_module,
        "upload_artifact_metadata",
        lambda _client, *, bucket, object_key, src_path, content_type: {
            "bucket": bucket,
            "object_key": object_key,
            "version_id": "v1",
            "sha256": "a" * 64,
            "byte_size": 1,
            "content_type": content_type,
        },
    )

    class FakeHermes:
        profile = None

        def __init__(self, _settings, *, profile=None):
            self.profile = profile

        def analyze(self, *, payload, skill_md, output_schema, task):
            assert task == "daily_report"
            self.__class__.profile = self.profile
            result = {"general_summary": {"zh": "本班次运行稳定。", "en": "The shift was stable."}}
            validate_daily_report_result(result)
            return HermesResult(
                result=result,
                telemetry={"runtime_model": "daily-model", "provider": "fake", "input_tokens": 10, "output_tokens": 5},
            )

    settings = SimpleNamespace(
        database_url="postgresql://test",
        minio_bucket_reports="noc-reports",
        minio_bucket_job_artifacts="noc-job-artifacts",
        hermes_profile="noc-daily-report",
        hermes_max_output_attempts=1,
        hermes_version="test-hermes",
    )
    result = process_message(
        {
            "job_id": "job-report-1",
            "job_type": "daily_report",
            "incident_id": None,
            "object_refs": [{"bucket": "noc-reports", "key": "snapshots/report.json", "sha256": "b" * 64}],
            "skill_name": "daily-alert-report",
            "skill_hash": None,
            "skill_snapshot_id": "snapshot-1",
            "skill_execution_hash": "execution-1",
        },
        settings=settings,
        metrics=WorkerMetrics(),
        hermes_client_factory=FakeHermes,
    )

    assert FakeHermes.profile == "noc-daily-report"
    assert result["output"]["general_summary"]["zh"] == "本班次运行稳定。"
    assert result["artifact_metadata"]["report"]["object_key"].endswith("/report.docx")
    assert result["artifact_metadata"]["document"]["object_key"].endswith("/document.json")
    assert [key for _, key in uploaded] == [
        "jobs/job-report-1/report-plan.json",
        "jobs/job-report-1/report-telemetry.json",
    ]
