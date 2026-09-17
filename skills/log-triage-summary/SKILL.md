---
name: log-triage-summary
description: Summarize a NOC incident's attached log excerpt into a structured, bilingual (Chinese/English) triage summary with a Key Finds/Secondary Finds breakdown (likely cause, severity signal, recommended next action).
---

# Log Triage Summary

Given a log excerpt attached to an incident, produce a structured JSON triage
summary. The NOC team here is bilingual — every human-readable field must be
given in **both** Chinese and English, not one or the other.

Output shape:

- `summary_en` / `summary_zh` — one paragraph each, plain language, what the
  log shows overall: service name, error volume, time range, and whether the
  errors look like one dominant failure mode or several independent ones.
- `key_finds` — a list of the distinct error patterns that materially make up
  the log, ordered by frequency (highest count first). Each entry:
  - `label_en` / `label_zh` — short name for the pattern (e.g. the exception
    class or failing call).
  - `pattern_ids` — one or more IDs from the deterministic count manifest
    appended to the log. Select the IDs whose patterns belong to this finding;
    do not invent IDs. The runtime calculates the count from these IDs. The
    runtime-reserved `other` ID is the exact remainder; do not combine it with
    a specific pattern ID. `unquantified` is reserved for a finding that has
    no defensible manifest match and is intentionally left uncounted.
  - `count` — returned for schema compatibility, but ignored and replaced by
    the runtime with the exact sum of the selected pattern IDs.
  - `percentage` — returned for schema compatibility, but ignored and
    replaced by the runtime using the exact total entry count.
  - `detail_en` / `detail_zh` — one or two sentences: which class/method/
    endpoint is involved, what the underlying exception/condition is, and
    any other context worth a NOC operator's attention.
  A log dominated by a single request/error still gets exactly one
  `key_finds` entry — don't pad with invented patterns.
- `secondary_finds` — same shape as `key_finds`, for lower-frequency or
  less-actionable patterns worth noting but not leading with. The runtime
  appends every deterministic family that the model does not label, so this
  list may be longer than the model-authored list. It must not collapse
  omitted evidence into a generic "other" finding.
- `likely_cause_en` / `likely_cause_zh` — short phrase, the most probable
  root cause overall (drawn from the leading key find).
- `recommended_action_en` / `recommended_action_zh` — short phrase, what the
  NOC operator should do next.
- `severity_signal` — one of `low`, `medium`, `high`, `critical`.
- `confidence` — float 0.0–1.0.

- `total_entries` — returned for schema compatibility, but ignored and
  replaced by the runtime with the exact number of physical log entries.

Numerical truth is deterministic. The runtime owns `total_entries`, every
finding `count` and `percentage`, and expands every omitted deterministic
error family into its own secondary finding. Claude owns grouping selection,
labels, explanations, severity, and narrative for the families it discusses.
Never estimate a number from the prose or repeat a total that is not present
in the manifest. `other` is an internal compatibility marker and is removed
from the persisted result; it must not be used to hide an identifiable
family.
Do not put numeric count/percentage breakdowns in narrative sentences; use the
structured finding fields instead.

If the log itself contains non-English (e.g. Chinese) text, the `_zh` fields
should be genuine Chinese analysis (not a translation-only echo of the `_en`
field), and the `_en` fields should be genuine English analysis in turn —
both are first-class outputs, not one derived mechanically from the other.

Output must be valid JSON matching this shape exactly — no prose outside the
JSON object. This is the schema the Claude Bridge validates before uploading
the result to MinIO/Postgres (master plan §29, Log Analysis Job Contract).
