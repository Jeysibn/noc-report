---
name: daily-alert-report
description: Produce the bilingual shift-level narrative for a Daily Alert & Log Analysis Report from compact per-incident summaries.
---

# Daily Alert Report

The frozen input contains a compact shift window, incident facts, and stable
analysis report fragments. Produce a narrative-only `ReportPlan`, not a
complete `ReportDocument`.

Return JSON with:

```json
{
  "general_summary": {"zh": "...", "en": "..."}
}
```

`general_summary` is required. Both language values must be substantive,
non-empty strings. It may concisely mention supported recurring themes, but
must not create a separate cross-incident findings section.

Claude owns only semantic reasoning: the Chinese and English shift summary
and operational assessment. Do not emit incident IDs, AnalysisRun IDs,
screenshots, bucket/object keys, URLs, log filenames, timestamps, or copied
incident metadata.

Report Composition derives the mandatory incident and analysis coverage from
the frozen `ReportSnapshot`. It owns canonical section order, numbering,
evidence, filenames, links, and complete analysis presentation. Do not repeat
references for facts the application already owns.

Return JSON only and match `output.schema.json`.
