---
name: daily-alert-report
description: Compile a shift's frozen incident/analysis snapshot into a structured, bilingual (Chinese/English) daily alert report.
---

# Daily Alert Report

Given a JSON snapshot of one shift (`shift_id`, `shift_starts_at`,
`shift_ends_at`, and a list of `incidents`, each with `display_id`, `title`,
`service`, `environment`, `status`, `triggered_at`, `recovered_at`,
`grafana_url`, `log_filename`, `screenshots` (a list of `{bucket,
object_key, filename}`, may be empty), and an optional `analysis` object
produced by the log-triage-summary skill — itself already bilingual with
`summary_en`/`summary_zh`, `key_finds`, `secondary_finds`, `likely_cause_en`/
`_zh`, `recommended_action_en`/`_zh`), produce a structured JSON report. The
NOC team is bilingual — every human-readable field must be given in **both**
Chinese and English.

Output shape:

- `title` — short report title, e.g. "Daily Alert Report — Night Shift".
- `overview_en` / `overview_zh` — one paragraph each, plain language,
  summarizing the shift as a whole (incident count, overall severity mix,
  anything recurring across incidents).
- `sections` — one entry per incident in the snapshot, each with:
  - `incident_display_id`
  - `title`
  - `status`
  - `grafana_url` (nullable) — carried through from the snapshot as-is.
  - `log_filename` (nullable) — carried through from the snapshot as-is.
  - `screenshots` — carried through from the snapshot as-is (list, may be
    empty); don't drop or reorder it.
  - `analysis` — when the snapshot incident has an `analysis` object, carry
    it through **unmodified** (don't re-derive or re-translate it — the
    log-triage-summary skill already produced genuine bilingual analysis for
    it); when absent, set this to `null` and note in the section that no
    analysis was available yet.

Output must be valid JSON matching this shape exactly — no prose outside
the JSON object. This is the schema the Claude Bridge validates before
converting the result to a DOCX file and uploading it to MinIO/Postgres
(master plan §29, Daily Report Job Contract).
