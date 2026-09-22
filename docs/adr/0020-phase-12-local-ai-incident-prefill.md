# ADR-0020: Local AI incident prefill after OCR

- Status: Accepted
- Date: 2026-09-16
- Supersedes: none

## Decision

Incident screenshot handling remains a text OCR pipeline:

`Screenshot -> PaddleOCR -> normalized OCR -> deterministic extraction -> optional Ollama mapper -> evidence resolver -> operator review -> Incident`

Ollama is an optional, local-only adapter behind `IncidentPrefill` and the
`LocalInference` interface. The production profile targets a Q4 3B instruct
model, CPU inference, context 2048, five-minute keep-alive, one concurrent
request, and a bounded queue. It is isolated from the retired provider runtime bridge and
therefore performs zero paid retired provider calls.

Every suggested field carries `value`, exact `source_text`, quality, method,
and OCR confidence. The resolver accepts values only when the source line is
present and the value is directly supported or is a documented enum/service
normalization. Missing or unsupported facts remain unresolved. The operator,
not the mapper, creates the Incident.

`LOCAL_PREFILL_AI_ENABLED` defaults to false. Ollama failure, timeout, invalid
JSON, or queue saturation preserves OCR and deterministic suggestions and
leaves ambiguous fields for manual entry. Raw OCR is never overwritten by
model output. Corrections are captured at the UI/API integration seam for
future rule and prompt improvements, without automatic training.

## Consequences

The local model improves semantic mapping without using retired provider for a cheap
form-filling task. The feature has a measurable resource cost, so real-model
accuracy and 16 GB VM memory benchmarks are an operational rollout gate rather
than a CI dependency. The fake adapter keeps normal CI deterministic.
