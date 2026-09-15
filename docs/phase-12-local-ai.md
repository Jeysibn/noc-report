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
mode accepts a bounded sample size because each CPU call is measured in tens
of seconds. Explicit-label controls remain deterministic and are not sent to
the model. CI uses the fake adapter and does not depend on model download or
external AI connectivity.

Latest rules-only baseline on this workspace: OCR-only complete-form exact
match `0/50`; OCR plus rules `40/50` (80%). The selected ambiguous slice is
`0/3` with rules and `3/3` with the Q4 3B mapper after strict JSON-schema and
OCR-line-index validation. This is a bounded semantic result, not a claim
that all six ambiguous cases have been measured; run the harness with
`--ollama-sample-size 6` before expanding rollout confidence.

A live Q4 3B run on the Docker profile measured approximately 2.0–2.3 GiB
resident in the validation run, about 47 seconds per successful request in
the three-case sample at two allocated CPUs, and roughly 196–202% CPU. One
isolated cold/contended request previously reached the 75-second bound, so
the feature remains a suggestion-only path with deterministic/manual fallback.
The host had no swap activity, but it has 32 GiB rather than the target 16
GiB. The full representative stack (PostgreSQL, RabbitMQ, MinIO, API, Vite,
bridge, and Ollama) was healthy with roughly 27.5 GiB available at start/end;
this is a headroom projection, not a substitute for a real 16 GiB VM test.

`LOCAL_PREFILL_AI_ENABLED=false` remains the safe default for rollout. Enable
it only for developer/admin testing until the larger sanitized operational
corpus and an actual 16 GiB host validation are complete.
