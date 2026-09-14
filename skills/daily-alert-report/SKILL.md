---
name: daily-alert-report
description: Produce the cross-incident, shift-level narrative for a daily alert report from compact per-incident summaries.
---

# Daily Alert Report

The frozen input contains a compact shift window, incident facts, and stable
analysis report fragments. Produce a `ReportPlan`, not a complete
ReportDocument. Section order and wording belong to this skill; the
application resolves all trusted facts and evidence after this response.

Create a `blocks` array using only `heading`, `paragraph`,
`bilingual_generated_text`, `incident_reference`, `analysis_reference`,
`divider`, and `page_break` nodes.

Use `incident_reference` with an incident `id` or `display_id` from the
input. Use `analysis_reference` with the frozen `analysis_run_id`, or with
an `incident_id` when no analysis exists. The application resolves all
incident evidence and analysis content from the frozen snapshot.

Include exactly one `incident_reference` for every incident in the input and
one `analysis_reference` for every supplied `analysis_run_id`. Do not omit a
lower-severity or already-recovered incident; ordering is yours, coverage is
not.

Never emit screenshots, bucket names, object keys, URLs, log filenames,
copied incident metadata, or full analysis objects.

For generated bilingual text, provide both `zh` and `en`. Keep Chinese
before English. Return JSON only. Every node must match `output.schema.json`.
