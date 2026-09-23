---
name: log-triage-summary
description: Summarize a NOC incident's attached log excerpt into a structured, bilingual (Chinese/English) triage summary with a Key Finds/Secondary Finds breakdown and severity signal.
---

# Log Triage Summary

Given a log excerpt attached to an incident, produce a structured JSON triage
summary. The NOC team here is bilingual — every human-readable field must be
given in **both** Chinese and English, not one or the other.

The runtime may append deterministic aggregate facts containing level, service,
time-range, and exception counters computed over the full input. Use those
facts to ground the overall summary and prioritize findings; do not invent
metadata or repeat raw stack traces. The physical pattern catalogue is not
provided to the semantic runtime because different logger layers can describe
the same operational cause.

Output shape:

- `summary_en` / `summary_zh` — one paragraph each, plain language, what the
  log shows overall: service name, error volume, time range, and whether the
  errors look like one dominant failure mode or several independent ones.
- `key_finds` — a list of stable error templates that materially make up
  the log, ordered by frequency (highest count first). Each entry:
  - `label_en` / `label_zh` — short name for the template (e.g. the exception
    class or failing operation). Group variable request IDs, order numbers,
    URLs, tokens, and stack locations into the same template.
  - `id` — one stable finding identity shared by both language presentations;
    it must be unique across Key Finds and Secondary Finds.
- `evidence_entry_ids` — the exact integer IDs from the `[entry_id=N]`
    markers in the supplied normalized log evidence that support this finding.
    Group as many physically different entries as belong to the same
    operational cause. Never assign one entry to more than one finding. The
    application calculates the authoritative count from these IDs.
  - `pattern_ids` — return `["unquantified"]` in the model response. The
    application replaces this placeholder with a stable finding identity after
    reconciling `evidence_entry_ids`; do not invent physical pattern IDs.
  - `count` and `percentage` — return `null` in the model response. The
    application replaces them with exact deterministic values after inference.
  - `detail_en` / `detail_zh` — one to three evidence-grounded sentences:
    identify the class/method/endpoint, immediate failure point, relevant
    values or time/context, and any limitation that matters to a NOC
    operator. Key Find explanations should carry the substantive evidence,
    not just restate the label.
  - When a lower-volume variant is clearly part of the dominant failure,
    group it with that finding by using multiple `evidence_entry_ids`; do not
    create a separate finding only because wording, endpoint, or request
    context is slightly different.
  A log dominated by a single request/error still gets exactly one
  `key_finds` entry — don't pad with invented patterns.
- `secondary_finds` — same shape as `key_finds`, for lower-frequency or
  less-actionable causes worth noting but not leading with. The application
  preserves the runtime's semantic selection and never expands every
  logger/request variant into a separate operator finding.
- `severity_signal` — one of `low`, `medium`, `high`, `critical`.
- `confidence` — float 0.0–1.0.

- `total_entries` — returned for schema compatibility, but ignored and
  replaced by the runtime with the exact number of physical log entries.

Numerical truth is deterministic. The application owns `total_entries`, every
finding `count` and `percentage`, and reconciles them from
`evidence_entry_ids`. The semantic runtime owns grouping selection, labels,
explanations, severity, and narrative.
Never estimate a number from prose or claim a count without selecting the
supporting entry IDs. The runtime may use `unquantified` only as the temporary
model response placeholder before application reconciliation.
Do not put numeric count/percentage breakdowns in narrative sentences; use the
structured finding fields instead.

## Output style and evidence guardrails

- Be concise: each short summary is 2–4 sentences and no more than a compact
  paragraph. It gives the exact total/time window when reliable, identifies
  whether the log has one dominant pattern or several independent patterns,
  names only the top one or two patterns, and may acknowledge meaningful
  lower-volume errors.
- Chinese comes first, followed by English. Use only `Short Summary`, `Key
  Finds`, and `Secondary Finds` in the operator-facing result. Do not emit
  `Most Likely Cause`, `Likely Cause`, `Recommended Action`, `Remediation`,
  `Root Cause`, or `Cross-incident Findings` fields or sections.
- `Key Finds` contains major operational patterns only, normally no more than
  5–6 entries. Give each one a short label, its evidence-grounded detail, and
  the structured count/percentage. Key details may use up to three concise
  sentences when the evidence warrants it. `Secondary Finds` are materially
  shorter: use one brief sentence after the label, normally no more than 320
  characters, and omit deep mechanism discussion unless it is essential to
  identify the operational condition. Group only truthful, related
  low-volume variants.
- Do not repeat the same evidence in the summary, a finding, and another
  finding. Do not include recommendations unless a separate task explicitly
  requests them.
- Counts mean matching physical log entries, not estimated unique failures.
  The same request may be logged by multiple layers; say so only when the log
  provides that evidence. Never independently invent different counts,
  percentages, order, or Key-vs-Secondary classification for Chinese and
  English—the single finding object owns those shared facts.
- Temporal coexistence is not evidence of shared causation. Do not say that
  simultaneous errors share a root cause because they occurred in the same
  hour, file, service, or application. Make a shared-cause statement only
  when the log links the events through a trace/request/correlation ID,
  caller/callee chain, nested exception, same failing dependency, or equivalent
  direct evidence. Otherwise say what the logs confirm and that the deeper
  reason cannot be confirmed from this file alone.
- Separate fact from hypothesis with wording such as “The logs show”, “The
  immediate failure point is”, and “The underlying reason cannot be confirmed
  from this file alone”. Avoid unsupported “probably caused by”, “likely due
  to”, or “strongly indicates” claims.

If the log itself contains non-English (e.g. Chinese) text, the `_zh` fields
should be genuine Chinese analysis (not a translation-only echo of the `_en`
field), and the `_en` fields should be genuine English analysis in turn —
both are first-class outputs, not one derived mechanically from the other.

Output must be valid JSON matching this shape exactly — no prose outside the
JSON object. This is the schema the runtime boundary validates before uploading
the result to MinIO/Postgres (master plan §29, Log Analysis Job Contract).
