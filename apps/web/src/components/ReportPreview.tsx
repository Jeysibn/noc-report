import { useEffect, useState } from "react";
import { reportService } from "@/services";
import type {
  ReportAnalysisReference,
  ReportBilingualFindList,
  ReportBilingualText,
  ReportBlock,
  ReportDocument,
  ReportFindList,
  ReportIncidentEvidence,
  ReportLink,
  ReportLogFileReference,
} from "@/types/report";
import { ApiError } from "@/lib/http";

/**
 * Web Report Builder preview (per the canonical Daily Report layout):
 * renders the exact same composed ReportDocument the DOCX was rendered
 * from (see bridge/noc_bridge/report_document_json.py) as continuous,
 * scrollable HTML — Alerts -> General Summary -> Log Analysis, same
 * order, same evidence, same full bilingual analysis. It does not
 * simulate DOCX page breaks; that's the DOCX adapter's job, not this
 * one's (see docs: "the important thing is semantic consistency").
 */
export function ReportPreview({ reportId }: { reportId: string }) {
  const [doc, setDoc] = useState<ReportDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    reportService
      .getDocument(reportId)
      .then((fetched) => {
        if (!cancelled) setDoc(fetched);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setError("No structured preview is available for this report.");
        } else {
          setError(err instanceof ApiError ? err.message : "Could not load report preview.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reportId]);

  if (loading) return <p className="text-sm text-muted">Loading preview…</p>;
  if (error) return <p className="text-sm text-muted">{error}</p>;
  if (!doc) return null;

  return (
    <div className="flex flex-col gap-6 rounded-md border border-border bg-surface p-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold">{doc.title}</h2>
        <p className="mt-1 text-sm text-muted">
          {doc.metadata.map((m) => `${m.label}: ${m.value}`).join("  ·  ")}
        </p>
      </div>
      <div className="flex flex-col gap-4">
        {doc.blocks.map((block, i) => (
          <ReportBlockView key={i} block={block} reportId={reportId} />
        ))}
      </div>
    </div>
  );
}

function ReportBlockView({ block, reportId }: { block: ReportBlock; reportId: string }) {
  switch (block.type) {
    case "heading": {
      const Tag = block.level <= 1 ? "h2" : block.level === 2 ? "h3" : "h4";
      const className =
        block.level <= 1
          ? "mt-2 text-base font-semibold uppercase tracking-wide text-accent"
          : block.level === 2
            ? "text-base font-semibold text-accent"
            : "text-sm font-semibold";
      return <Tag className={className}>{block.text}</Tag>;
    }
    case "paragraph":
      return <p className="text-sm">{block.text}</p>;
    case "metadata":
      return <p className="text-sm"><span className="font-medium">{block.label}: </span>{block.value}</p>;
    case "link":
      return <LinkView block={block} />;
    case "log_file_reference":
      return <LogFileView block={block} />;
    case "screenshot":
      return <PreviewScreenshot reportId={reportId} index={block.index} filename={block.filename} />;
    case "alert_navigation":
      return (
        <nav aria-label="Alert Navigation" className="rounded-md border border-border p-4">
          <h3 className="text-base font-semibold text-accent">Alert Navigation</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {block.entries.map((entry) => (
              <li key={entry.target}><a className="text-accent underline" href={`#${entry.target}`}>{entry.text}</a></li>
            ))}
          </ul>
        </nav>
      );
    case "divider":
      return <hr className="border-border" />;
    case "page_break":
      // Semantic section boundary — the web preview stays continuous
      // (no simulated page numbers), so a visible divider marks it
      // instead of a blank page.
      return <hr className="my-2 border-t-2 border-dashed border-border" />;
    case "bilingual_text":
      return <BilingualTextView block={block} />;
    case "bilingual_find_list":
      return (
        <div className="flex flex-col gap-3">
          <FindListView block={{ type: "find_list", heading: block.heading_zh, language: "Chinese", finds: block.finds_zh }} />
          <FindListView block={{ type: "find_list", heading: block.heading_en, language: "English", finds: block.finds_en }} />
        </div>
      );
    case "find_list":
      return <FindListView block={block} />;
    case "incident_evidence":
      return <IncidentEvidenceView block={block} reportId={reportId} />;
    case "analysis_reference":
      return <AnalysisReferenceView block={block} reportId={reportId} />;
    default:
      return null;
  }
}

function BilingualTextView({ block }: { block: ReportBilingualText }) {
  return (
    <div className="flex flex-col gap-2">
      {block.heading_zh && <h4 className="text-sm font-semibold">{block.heading_zh}</h4>}
      {block.text_zh && <p className="text-sm">{block.text_zh}</p>}
      {block.heading_en && <h4 className="text-sm font-semibold">{block.heading_en}</h4>}
      {block.text_en && <p className="text-sm">{block.text_en}</p>}
    </div>
  );
}

function FindListView({ block }: { block: ReportFindList }) {
  return (
    <div className="flex flex-col gap-2">
      <h5 className="text-sm font-semibold">{block.heading}</h5>
      <ul className="mt-1 flex flex-col gap-2">
        {block.finds.map((find, i) => (
          <li key={i} className="text-sm">
            <span className="font-medium">{find.label}</span>
            {find.stat && <span className="text-muted"> ({find.stat})</span>}
            {find.detail && <p className="text-muted">{find.detail}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function BilingualFindListView({ block }: { block: ReportBilingualFindList }) {
  return (
    <div className="flex flex-col gap-3">
      <FindListView block={{ type: "find_list", heading: block.heading_zh, language: "Chinese", finds: block.finds_zh }} />
      <FindListView block={{ type: "find_list", heading: block.heading_en, language: "English", finds: block.finds_en }} />
    </div>
  );
}

function PreviewScreenshot({ reportId, index, filename }: { reportId: string; index: number; filename: string | null }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    reportService.getScreenshotBlob(reportId, index)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [reportId, index]);

  if (error) return <p className="text-xs text-danger">Screenshot could not be retrieved.</p>;
  if (!src) return <p className="text-xs text-muted">Loading screenshot…</p>;
  // Original dimensions kept; the container just bounds width so it never
  // overflows the report card, matching the DOCX adapter's "fit within
  // margins, never stretch" rule.
  return <img src={src} alt={filename ?? "Alert screenshot"} className="max-w-full rounded border border-border" />;
}

function IncidentEvidenceView({ block, reportId }: { block: ReportIncidentEvidence; reportId: string }) {
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border p-4">
      <h3 className="text-base font-semibold text-accent">
        {block.navigation_target ? <a className="underline" href={`#${block.navigation_target}`}>{block.heading}</a> : block.heading}
      </h3>
      {block.screenshots.map((shot, i) => (
        <PreviewScreenshot key={i} reportId={reportId} index={shot.index} filename={shot.filename} />
      ))}
      {block.links.map((link, i) => (
        <LinkView key={i} block={link} />
      ))}
      {block.log_file && (
        <LogFileView block={block.log_file} />
      )}
      {block.metadata.length > 0 && (
        <p className="text-sm text-muted">
          {block.metadata.map((m) => `${m.label}: ${m.value}`).join("  ·  ")}
        </p>
      )}
    </div>
  );
}

function LinkView({ block }: { block: ReportLink }) {
  return (
    <p className="text-sm">
      <span className="font-medium">{block.prefix ?? block.label}: </span>
      <a href={block.url} target="_blank" rel="noreferrer" className="text-accent underline">
        {block.text ?? block.url}
      </a>
    </p>
  );
}

function LogFileView({ block }: { block: ReportLogFileReference }) {
  return (
    <p className="text-sm">
      <span className="font-medium">Log File  File Name: </span>
      {block.url ? (
        <a href={block.url} target="_blank" rel="noreferrer" className="text-accent underline">
          {block.filename}
        </a>
      ) : block.filename}
    </p>
  );
}

function AnalysisReferenceView({ block, reportId }: { block: ReportAnalysisReference; reportId: string }) {
  return (
    <div id={block.bookmark ?? undefined} className="flex flex-col gap-3 rounded-md border border-border bg-ground p-4">
      <h3 className="text-base font-semibold text-accent">{block.heading}</h3>
      {!block.available ? (
        <p className="text-sm text-muted">{block.unavailable_text ?? "No analysis available."}</p>
      ) : (
        <>
          {(block.screenshots ?? []).map((shot, i) => (
            <PreviewScreenshot key={i} reportId={reportId} index={shot.index} filename={shot.filename} />
          ))}
          {block.log_file && <LogFileView block={block.log_file} />}
          {(block.metadata ?? []).length > 0 && (
            <p className="text-sm text-muted">
              {(block.metadata ?? []).map((m) => `${m.label}: ${m.value}`).join("  ·  ")}
            </p>
          )}
          {block.children.map((child, i) => (
            <ReportBlockView key={i} block={child} reportId={reportId} />
          ))}
          {block.summary && <BilingualTextView block={block.summary} />}
          {block.key_finds && <BilingualFindListView block={block.key_finds} />}
          {block.secondary_finds && <BilingualFindListView block={block.secondary_finds} />}
        </>
      )}
    </div>
  );
}
