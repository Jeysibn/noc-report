# Phase 12 P1 implementation report

Date: 2026-09-16

## Finding classification

| Finding | Classification | Resolution |
|---|---|---|
| Q4 3B semantic mapper did not beat the deterministic slice | CONFIRMED | Replaced generic JSON mode with a per-request schema, restricted requests to fields with OCR evidence, and changed evidence references to compact OCR line indexes. Added label-prefix normalization and retained strict resolver validation. |
| Mapper asked about absent facts | CONFIRMED | Missing fields are no longer sent to local AI; they remain unresolved/manual. |
| Full 16 GiB VM validation | PARTIALLY CONFIRMED | The repository now has a repeatable stack sampler, and the representative stack ran without swap on a 32 GiB host. A physical 16 GiB validation remains outstanding. |

## Semantic benchmark

The sanitized corpus remains 50 cases. OCR-only exact match is `0/50`; OCR
plus deterministic rules is `40/50` (80%). The selected three ambiguous cases
were `0/3` with rules and `3/3` with Q4 3B local mapping. Per-field accuracy
on that live sample was 100% for title, service, status, triggered time,
recovered time, trigger value, and environment. This is a measured sample,
not a claim that all six ambiguous cases have been completed.

The live model was `qwen2.5:3b-instruct-q4_K_M`, context 2048, two CPUs,
single concurrency, and a five-minute keep-alive. The three-case run took
140.2 seconds total (about 46.7 seconds per successful request). The local
timeout is now 75 seconds, with deterministic/manual fallback preserved.

## Evidence and cost boundary

The model returns `{value, source_index}`. The resolver maps that index to the
original OCR line and emits the public `{value, source_text, quality, method,
ocr_confidence}` field. Unsupported values, invalid indexes, invalid enums,
and values not supported by the cited line are rejected. OCR prefill remains
outside the retired provider bridge and consumes zero retired provider calls.

## Stack resource validation

PostgreSQL, RabbitMQ, MinIO, API, Vite web, bridge, and Ollama were started
together. On the available 32 GiB host, the sampler measured about 27.5 GiB
available at start and end, zero swap-in/out, and these peaks:

| Component | Peak |
|---|---:|
| Ollama container | 2,244 MiB |
| PostgreSQL | 125 MiB |
| RabbitMQ | 157 MiB |
| MinIO | 140 MiB |
| API RSS | 127 MiB |
| Web RSS | 154 MiB |
| Bridge RSS | 89 MiB |

This provides substantial measured headroom, but is not a substitute for
running the same sampler on the actual 16 GiB deployment VM. The repeatable
command is:

```bash
python3 scripts/benchmark_phase12_stack.py \
  --pid <api-pid> --pid <web-pid> --pid <bridge-pid> \
  --duration 60
```

## Operational decision

`LOCAL_PREFILL_AI_ENABLED` remains disabled by default. Developer/admin
testing may enable it; NOC rollout should wait for a larger sanitized
operational corpus and a real 16 GiB validation. The feature continues to
degrade to deterministic extraction and manual completion when Ollama is
offline, times out, or fails validation.
