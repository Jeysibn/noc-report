---
name: log-triage-summary
description: Summarize a NOC incident's attached log excerpt into a structured, bilingual (Chinese/English) triage summary with a Key Finds/Secondary Finds breakdown and severity signal.
---

# Log Triage Summary

Given a log excerpt attached to an incident, produce a structured JSON triage
summary. The NOC team here is bilingual — every human-readable field must be
given in **both** Chinese and English, not one or the other.

The runtime may append a deterministic log profile containing level, service,
logger, time-range, and Caused by root-cause counters computed over the full
input. Use that profile to ground the overall summary and prioritize findings;
do not invent metadata or repeat raw stack traces.

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
- `pattern_ids` — one or more IDs from the deterministic count manifest
    appended to the log. Select the IDs whose patterns belong to this finding;
    do not invent IDs. The runtime calculates the count from these IDs. The
    runtime-reserved `other` ID is the exact remainder; do not combine it with
    a specific pattern ID. `unquantified` is reserved for a finding that has
    no defensible manifest match and is intentionally left uncounted.
    The input also includes deterministic `pattern_families`, which are
    grouping candidates for physical templates emitted by different logger
    layers. When several templates describe the same dependency failure or
    exception chain, use one finding with all of their exact `pattern_ids`.
    Do not emit one finding per family member.
  - `count` — returned for schema compatibility, but ignored and replaced by
    the runtime with the exact sum of the selected pattern IDs.
  - `percentage` — returned for schema compatibility, but ignored and
    replaced by the runtime using the exact total entry count.
  - `detail_en` / `detail_zh` — one to three evidence-grounded sentences:
    identify the class/method/endpoint, immediate failure point, relevant
    values or time/context, and any limitation that matters to a NOC
    operator. Key Find explanations should carry the substantive evidence,
    not just restate the label.
  - When a lower-volume variant is clearly part of the dominant failure,
    group it with that finding by using multiple `pattern_ids`; do not create
    a separate finding only because wording, endpoint, or request context is
    slightly different.
  A log dominated by a single request/error still gets exactly one
  `key_finds` entry — don't pad with invented patterns.
- `secondary_finds` — same shape as `key_finds`, for lower-frequency or
  less-actionable patterns worth noting but not leading with. The runtime
  appends deterministic templates that the model does not label and may
  consolidate clearly related low-volume variants under the dominant finding.
  It must not collapse omitted evidence into a generic "other" finding or
  split one cause family into one finding per logger/request variant.
- `severity_signal` — one of `low`, `medium`, `high`, `critical`.
- `confidence` — float 0.0–1.0.

- `total_entries` — returned for schema compatibility, but ignored and
  replaced by the runtime with the exact number of physical log entries.

Numerical truth is deterministic. The runtime owns `total_entries`, every
finding `count` and `percentage`, expands omitted deterministic templates, and
consolidates clearly related low-volume variants without dropping their
pattern IDs. The semantic runtime owns grouping selection, labels, explanations, severity,
and narrative for the templates it discusses.
Never estimate a number from the prose or repeat a total that is not present
in the manifest. `other` is an internal compatibility marker and is removed
from the persisted result; it must not be used to hide an identifiable
family.
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
