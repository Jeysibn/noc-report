# Phase 12 implementation report

Date: 2026-09-16

## Finding classification

| Finding | Classification | Evidence / resolution |
|---|---|---|
| Concurrent open Shift race | CONFIRMED | Runtime used read/close/insert without a database fence. Added `uq_shifts_one_active`, deterministic 409 handling, and a concurrent PostgreSQL test. |
| Concurrent report version race | CONFIRMED | Runtime used `MAX(version)+1` without serialization. Added Shift-row `FOR UPDATE`, `(shift_id, version)` uniqueness, and a 3→4/5 race test. |
| OCR semantic mapping seam | CONFIRMED | OCR only exposed heuristic fields and the UI used a simulator. Added `IncidentPrefill`, `LocalInference`, Ollama/Fake adapters, persistence, resolver, and review UI. |
| Pre-incident screenshot evidence | PARTIALLY CONFIRMED | Existing evidence was incident-owned. Added a draft prefill object/run and attach-after-confirmation path without changing immutable incident evidence behavior. |
| Operator correction telemetry | NOT PRESENT | Attach audit now records suggested vs final field values, without raw screenshot text. |
| Ollama production runtime | NOT PRESENT | Added feature-flagged profile and measured the model manually; it is not a CI dependency and remains disabled by default. |
| Existing Phase 11 reporting/auth/governance architecture | ALREADY FIXED | Rechecked coherence gate and regression suites; preserved the active implementations. |

## Transactional proof

- Two concurrent open requests leave exactly one `ACTIVE` Shift; the loser
  receives HTTP 409.
- With existing versions 1, 2, and 3, two concurrent allocations commit
  versions 4 and 5. PostgreSQL also rejects any duplicate pair.
- Migration `0a1b2c3d4e5f_phase12_transactional_prefill.py` round-tripped
  downgrade/upgrade successfully and was left at `0a1b2c3d4e5f`.

## OCR/local AI proof

The runtime flow is:

`Screenshot → PaddleOCR → normalized extraction → deterministic rules → optional Ollama → evidence resolver → review form → operator-confirmed Incident`

Every field has `value`, exact `source_text`, quality, method, and OCR
confidence. Missing environment and unsupported service output remain empty.
The resolver does not trust syntactically valid JSON by itself. OCR and
deterministic fields remain usable when Ollama is unavailable, times out, or
returns invalid JSON. The path performs zero Claude calls.

The shared strict contract is
`packages/contracts/incident_prefill.schema.json`; backend and TypeScript
types mirror it.

## Benchmark and resource result

The sanitized corpus contains 50 cases. Latest rules-only result:

| Mode | Exact complete-form match |
|---|---:|
| OCR only | 0/50 (0%) |
| OCR + deterministic rules | 40/50 (80%) |
| OCR + rules + local Q4 3B sample | 4/10 (40%) |

The local sample uses six ambiguous label variants and four controls. The
initial CPU run measured 3.66–3.77 GiB model memory under the 4 GiB Compose
limit, approximately 26–41 seconds per successful request at two CPUs, and
roughly 198–217% CPU. The host had zero swap activity but has 32 GiB, not the
16 GiB deployment target. The local model did not yet improve the sample over
the deterministic baseline, so `LOCAL_PREFILL_AI_ENABLED` remains false by
default pending a better prompt/model or sanitized operational corpus.

## Verification summary

- API: 120 passed, 4 warnings.
- Focused Phase 12 tests: deterministic/evidence/failure/concurrency tests
  pass; the final attachment test also verifies correction audit metadata.
- Bridge: 79 passed, 2 documented skips.
- Sandbox: 53 passed.
- Frontend: 21 passed; lint and production build pass.
- Coherence gate and Compose config validation pass.
- Alembic migration round-trip pass.

## Remaining risks

- P0: none found in the implemented transactional seams.
- P1: local model semantic quality and the full 16 GiB stack benchmark are
  not release-accepted; keep the feature disabled or use deterministic/manual
  fallback.
- P2: expand the sanitized corpus with real Nightingale/Teams OCR variants,
  collect correction telemetry, and remeasure memory with API/web/bridge
  processes running on an actual 16 GiB VM.
