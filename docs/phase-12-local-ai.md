# Phase 12: transactional hardening and local OCR prefill

## Runtime contract

The API persists an `IncidentPrefillRun` before an Incident exists. It retains
the original screenshot in the evidence bucket, raw OCR text, normalized OCR,
engine/version, line extraction, durations, mapper result, and failure state.
The browser receives suggestions and source snippets. Applying them only
updates the editable form. The screenshot is attached as `ALERT_SCREENSHOT`
only after the operator submits the Incident.

The exact backend contract is `IncidentPrefill`:

```json
{
  "title": {"value": "...", "source_text": "Alert: ...", "quality": "HIGH", "method": "deterministic", "ocr_confidence": 0.98},
  "service": {"value": "...", "source_text": "Service: ...", "quality": "REVIEW", "method": "local_ai", "ocr_confidence": 0.94},
  "notes": {"value": null, "source_text": null, "quality": "UNRESOLVED", "method": "unresolved", "ocr_confidence": null},
  "status": {"value": "open", "source_text": "Triggered", "quality": "HIGH", "method": "deterministic", "ocr_confidence": 0.98},
  "triggered_at": {"value": "2026-09-15T01:00:00+08:00", "source_text": "Triggered: ...", "quality": "HIGH", "method": "deterministic", "ocr_confidence": 0.96},
  "recovered_at": {"value": null, "source_text": null, "quality": "UNRESOLVED", "method": "unresolved", "ocr_confidence": null},
  "trigger_value": {"value": "193K", "source_text": "Trigger value: 193K", "quality": "HIGH", "method": "deterministic", "ocr_confidence": 0.97},
  "environment": {"value": "production", "source_text": "Environment: prod", "quality": "REVIEW", "method": "local_ai", "ocr_confidence": 0.95},
  "mapper_status": "LOCAL_AI",
  "mapper_model": "qwen2.5:3b-instruct-q4_K_M",
  "mapper_error": null
}
```

The current Incident schema has no severity field, so this phase does not
invent one. Status values are the existing Incident values (`open`,
`investigating`, `recovered`).

## Ollama deployment profile

The Compose service is behind the `local-ai` profile and is bounded to 4 GiB
RAM, 2 CPUs, one model, one parallel request, and a five-minute keep-alive.
The API defaults to disabled, model `qwen2.5:3b-instruct-q4_K_M`, context 2048,
45-second CPU timeout, and queue size 8:

```bash
docker compose -f infrastructure/docker-compose.dev.yml --profile local-ai up -d ollama
docker exec noc-report-ollama ollama pull qwen2.5:3b-instruct-q4_K_M
LOCAL_PREFILL_AI_ENABLED=true LOCAL_PREFILL_BASE_URL=http://localhost:11434
```

The exact memory footprint is model/runtime dependent. Validate with
`docker stats noc-report-ollama`, `free -m`, and swap counters while the full
PostgreSQL/RabbitMQ/MinIO/API/bridge stack is active. Do not treat swap as
normal model memory.

## Transactional migration

Apply `0a1b2c3d4e5f_phase12_transactional_prefill.py` with `alembic upgrade
head`. It adds the active-Shift partial index, report identity constraint,
OCR metadata columns, and the pre-incident prefill table. The migration must
not be skipped in production.

## Benchmark

Run the sanitized 50-case comparison:

```bash
PYTHONPATH=apps/api python3 scripts/benchmark_incident_prefill.py \
  --output phase12-prefill-benchmark.json
PYTHONPATH=apps/api python3 scripts/benchmark_incident_prefill.py --ollama
```

The checked-in benchmark harness never downloads a model implicitly. The
full 50 cases establish OCR-only and OCR-plus-rules baselines; real-model
mode uses a documented 10-case sample (six ambiguous label variants plus four
controls) because each CPU call is measured in tens of seconds. CI uses the
fake adapter and does not depend on model download or external AI connectivity.

Latest rules-only baseline on this workspace: OCR-only complete-form exact
match `0/50`; OCR plus rules `40/50` (80%). A live Q4 3B run on the Docker
profile measured 3.66–3.77 GiB resident model memory, about 26–41 seconds per
successful CPU request at two allocated CPUs, and roughly 198–217% CPU. The
host had no swap activity, but it has 32 GiB rather than the target 16 GiB.
The initial local sample produced 40% exact match and did not beat the rules
baseline; unsupported source choices were rejected. This is a successful
resource/safety measurement, not a semantic-quality acceptance claim. Keep
`LOCAL_PREFILL_AI_ENABLED=false` for rollout until a sanitized operational
corpus demonstrates improvement with the selected model/prompt.
