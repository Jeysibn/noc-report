from concurrent.futures import ThreadPoolExecutor
import threading
import time

from app.incident_prefill import build_incident_prefill, build_mapper_response_schema, deterministic_prefill, normalize_ocr_text
from app.local_inference import FakeLocalInference, LocalInferenceError, OllamaLocalInference


def _extraction(text: str) -> dict:
    return {
        "raw_text": text,
        "lines": [{"text": line, "confidence": 0.96, "bbox": []} for line in text.splitlines()],
    }


def test_deterministic_prefill_maps_supported_fields_without_local_ai():
    result = deterministic_prefill(
        _extraction(
            "ALERT: PaymentGatewayTimeout\n"
            "Service: betbingo-app-servce\n"
            "Environment: production\n"
            "Triggered: 2026-09-15 01:00:00+08:00\n"
            "Trigger value: 193K"
        ),
        known_services=("betbingo-app-service",),
    )
    assert result.title.value == "PaymentGatewayTimeout"
    assert result.service.value == "betbingo-app-service"
    assert result.service.method == "deterministic_fuzzy"
    assert result.environment.value == "production"
    assert result.triggered_at.value == "2026-09-15T01:00:00+08:00"
    assert result.trigger_value.value == "193K"
    assert result.service.source_text == "Service: betbingo-app-servce"


def test_missing_environment_stays_unresolved():
    result = deterministic_prefill(_extraction("ALERT: API error\nService: payments-api"))
    assert result.environment.value is None
    assert result.environment.source_text is None


def test_local_mapper_resolves_noisy_known_service_with_source_trace():
    result = build_incident_prefill(
        _extraction("Alert: Error rate high\nService: betbingo-app-servce\nTriggered: 09:31:22"),
        inference=FakeLocalInference(
            {
                "service": {"value": "betbingo-app-service", "source_index": 1},
                "title": {"value": "Alert: Error rate high", "source_index": 0},
                "status": {"value": "open", "source_index": 2},
            }
        ),
        known_services=("betbingo-app-service",),
    )
    assert result.service.value == "betbingo-app-service"
    assert result.service.source_text == "Service: betbingo-app-servce"
    assert result.service.method == "local_ai"
    assert result.title.value == "Error rate high"
    assert result.status.value == "open"


def test_mapper_receives_only_unresolved_fields_in_strict_schema():
    class CapturingInference:
        model = "test"

        def __init__(self):
            self.schema = None

        def complete_json(self, _prompt, response_schema=None):
            self.schema = response_schema
            return {
                "title": {"value": None, "source_text": None},
                "service": {"value": None, "source_text": None},
            }

    adapter = CapturingInference()
    result = build_incident_prefill(
        _extraction("Alert name: API error\nApplication name: paymnts-api"),
        inference=adapter,
        known_services=("payments-api",),
    )
    assert result.service.value is None
    assert adapter.schema == build_mapper_response_schema(
        ["title", "service"]
    )
    assert adapter.schema["additionalProperties"] is False
    assert adapter.schema["properties"]["title"]["required"] == ["value", "source_index"]


def test_local_mapper_rejects_unsupported_service_value():
    result = build_incident_prefill(
        _extraction("Alert: API error\nService: payments-api"),
        inference=FakeLocalInference(
            {"service": {"value": "unrelated-service", "source_text": "Service: payments-api"}}
        ),
        known_services=("payments-api",),
    )
    assert result.service.value == "payments-api"
    assert result.service.method == "deterministic"


def test_local_model_cannot_invent_environment_without_source_evidence():
    result = build_incident_prefill(
        _extraction("ALERT: API error\nService: payments-api"),
        inference=FakeLocalInference(
            {
                "environment": {"value": "production", "source_text": "ALERT: API error"},
            }
        ),
    )
    assert result.environment.value is None
    assert result.mapper_status == "DETERMINISTIC"


def test_local_mapper_offline_keeps_deterministic_fields_and_falls_back():
    class Offline:
        model = "offline"

        def complete_json(self, _prompt, response_schema=None):
            del response_schema
            raise LocalInferenceError("connection refused")

    result = build_incident_prefill(
        _extraction("ALERT: API error\nService: payments-api\nEnvironment: staging"),
        inference=Offline(),
    )
    assert result.title.value == "API error"
    assert result.service.value == "payments-api"
    assert result.environment.value == "staging"
    assert result.mapper_status == "DETERMINISTIC"


def test_normalized_text_preserves_line_boundaries():
    assert normalize_ocr_text("  Service:   payments-api  \n\n Environment: production ") == "Service: payments-api\nEnvironment: production"


def test_ollama_adapter_serializes_requests_and_never_exceeds_one_inference(monkeypatch):
    active = 0
    peak = 0
    formats = []
    lock = threading.Lock()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": '{"environment":{"value":null,"source_text":null}}'}}

    def fake_post(*_args, **kwargs):
        nonlocal active, peak
        assert kwargs["json"]["options"]["num_ctx"] == 2048
        assert kwargs["json"]["options"]["num_predict"] == 64
        assert kwargs["json"]["keep_alive"] == "5m"
        formats.append(kwargs["json"]["format"])
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return Response()

    monkeypatch.setattr("app.local_inference.httpx.post", fake_post)
    adapter = OllamaLocalInference(
        base_url="http://ollama:11434",
        model="qwen2.5:3b-instruct-q4_K_M",
        context_size=2048,
        keep_alive="5m",
        timeout_seconds=1,
        max_concurrency=1,
        max_queue=8,
    )
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(adapter.complete_json, ["x"] * 5))
    assert peak == 1
    schema = {"type": "object", "additionalProperties": False}
    adapter.complete_json("x", response_schema=schema)
    assert formats[-1] == schema


def test_ollama_invalid_json_is_bounded_and_returns_local_error(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "not-json"}}

    calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return Response()

    monkeypatch.setattr("app.local_inference.httpx.post", fake_post)
    adapter = OllamaLocalInference(
        base_url="http://ollama:11434",
        model="qwen2.5:3b-instruct-q4_K_M",
        context_size=2048,
        keep_alive="5m",
        timeout_seconds=1,
    )
    try:
        adapter.complete_json("OCR")
    except LocalInferenceError:
        pass
    else:
        raise AssertionError("invalid Ollama JSON must fail closed")
    assert calls == 2
