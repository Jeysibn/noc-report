---
name: daily-alert-report
description: Produce the cross-incident, shift-level narrative for a daily alert report from compact per-incident summaries.
---

# Daily Alert Report

AI cost-optimization mission Phase 2, Issue 6: this skill no longer
receives (or reproduces) each incident's full log-triage-summary analysis,
screenshots, MinIO references, or Grafana/log-filename metadata — none of
that is reasoning-dependent, so Python assembles it deterministically
after this call (see `apps/api/app/api/v1/routers/reports.py`'s
`_build_snapshot` and `bridge/noc_bridge/service.py`'s daily-report merge
step). You only ever see a **compact** per-incident summary: `display_id`,
`title`, `status`, `severity_signal`, `main_error` (the analysis's
`likely_cause_en`, one line), `impact` (the analysis's `summary_en`, one
line), `starts_at`, `ends_at`. An incident with no analysis yet is
included with `severity_signal`/`main_error`/`impact` set to `null`.

Given the shift's `shift_starts_at`/`shift_ends_at` and this list of
compact incident summaries, produce **only**:

- `overview_en` / `overview_zh` — one paragraph each, plain language,
  summarizing the shift as a whole (incident count, overall severity mix).
- `cross_incident_findings_en` / `cross_incident_findings_zh` — one
  paragraph each identifying any real correlation, recurring pattern, or
  shared root cause **across multiple incidents** in this shift (e.g. the
  same downstream dependency failing in three unrelated services). If
  there is no real cross-incident correlation, say so plainly (e.g. "No
  cross-incident correlation was found; these incidents appear
  unrelated.") in both languages — do not invent a connection that isn't
  actually there.

Do not restate each incident's own analysis (you were not given it in
full, and it is merged back in deterministically afterward) — this output
is reasoning that only makes sense across incidents, not a per-incident
report.

Output must be valid JSON matching this shape exactly — no prose outside
the JSON object: `{overview_en, overview_zh, cross_incident_findings_en,
cross_incident_findings_zh}`. This is the schema the Claude Bridge
validates before deterministically merging it with the frozen shift
snapshot's incident metadata, converting the merged result to a DOCX
file, and uploading it to MinIO/Postgres (master plan §29, Daily Report
Job Contract).
