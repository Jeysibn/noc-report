---
name: daily-alert-report
description: Produce the cross-incident, shift-level narrative for a daily alert report from compact per-incident summaries.
---

# Daily Alert Report

The frozen input contains the shift window and projected incident facts.
Use those facts to write the report's semantic structure. The result is a
`ReportDocument` contract: section order and wording belong to this skill;
the bridge only renders the typed blocks into DOCX.

Create metadata with:

- `title`: `Daily Alert Report — <shift start> to <shift end>`
- `date`: the shift date
- `shift`: the shift time window

Create blocks in this order:

1. `heading`: `Alerts`
2. One `incident_evidence` block per incident. Copy its `incident_id`, title,
   status, Grafana URL, log filename, and screenshot references exactly from
   the input. Do not invent or remove evidence references.
3. `heading`: `General Summary`, followed by one `bilingual_text` block
   summarizing the shift as a whole.
4. `heading`: `Cross-Incident Findings`, followed by one `bilingual_text`
   block identifying real correlations across incidents. If none exists, say
   so plainly in both languages.
5. `heading`: `Log Analysis`, followed by one `analysis_reference` block per
   incident. Copy the analysis run ID exactly. If analysis is unavailable,
   set `available` to false. Otherwise use nested bilingual text and
   paragraphs for the available summary, findings, likely cause, and action.

For bilingual text, provide both `zh` and `en`. Keep Chinese before English.
Return JSON only. Every block must match `output.schema.json`.
