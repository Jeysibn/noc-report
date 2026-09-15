import { useRef, useState } from "react";
import { FileUpload } from "@/components/ui/FileUpload";
import { Button } from "@/components/ui/Button";
import { StatusPill } from "@/components/ui/StatusPill";
import { Input } from "@/components/ui/Form";
import { ocrService } from "@/services";
import type { IncidentPrefill } from "@/services/ocr.service";

type OcrState = "NOT_STARTED" | "UPLOADING" | "PROCESSING" | "REVIEW_REQUIRED" | "APPLIED" | "FAILED";
interface ReviewField {
  key: keyof IncidentPrefill;
  label: string;
  extractedValue: string;
  quality: "HIGH" | "REVIEW" | "UNRESOLVED";
  sourceSnippet: string;
  userModified: boolean;
}

const stateLabel: Record<OcrState, { label: string; status: "neutral" | "warning" | "good" | "info" }> = {
  NOT_STARTED: { label: "Not started", status: "neutral" },
  UPLOADING: { label: "Uploading", status: "info" },
  PROCESSING: { label: "Interpreting alert", status: "info" },
  REVIEW_REQUIRED: { label: "Prefill ready — review", status: "warning" },
  APPLIED: { label: "Applied", status: "good" },
  FAILED: { label: "Failed", status: "warning" },
};

/**
 * OCR Review (Milestone 4). State machine
 * NOT_STARTED -> UPLOADING -> PROCESSING -> REVIEW_REQUIRED -> APPLIED/FAILED.
 * Low-confidence fields never silently overwrite manual entries — the
 * operator applies the reviewed values explicitly via "Apply to form".
 */
const FIELD_LABELS: Record<string, string> = {
  title: "Title",
  service: "Service",
  notes: "Description / notes",
  status: "Status suggestion",
  triggered_at: "Trigger time",
  recovered_at: "Recovery time",
  trigger_value: "Trigger value",
  environment: "Environment",
};

export function OcrReviewPanel({
  onApply,
  onPrefillInvalidated,
}: {
  onApply: (values: Record<string, string>, prefill: { id: string; file: File }) => void;
  onPrefillInvalidated?: () => void;
}) {
  const [state, setState] = useState<OcrState>("NOT_STARTED");
  const [rawText, setRawText] = useState<string>("");
  const [fields, setFields] = useState<ReviewField[]>([]);
  const [prefill, setPrefill] = useState<{ id: string; file: File } | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  const requestGeneration = useRef(0);

  function handleFile(file: File) {
    const generation = ++requestGeneration.current;
    onPrefillInvalidated?.();
    setPrefill(null);
    setFields([]);
    setRawText("");
    setState("UPLOADING");
    setState("PROCESSING");
    ocrService
      .prefill(file)
      .then((run) => {
        if (generation !== requestGeneration.current) return;
        if (!run.prefill_json || run.status === "OCR_FAILED") {
          setState("FAILED");
          return;
        }
        setRawText(run.raw_ocr_text ?? "");
        setFields(
          Object.entries(run.prefill_json)
            .filter(([key, value]) => key in FIELD_LABELS && value && typeof value === "object")
            .map(([key, value]) => {
              const field = value as IncidentPrefill["title"];
              return {
                key: key as keyof IncidentPrefill,
                label: FIELD_LABELS[key],
                extractedValue: field.value ?? "",
                quality: field.quality,
                sourceSnippet: field.source_text ?? "No supporting OCR evidence",
                userModified: false,
              };
            }),
        );
        setPrefill({ id: run.id, file });
        setState("REVIEW_REQUIRED");
      })
      .catch(() => {
        if (generation === requestGeneration.current) setState("FAILED");
      });
  }

  function updateField(key: string, value: string) {
    setFields((prev) =>
      prev.map((f) => (f.key === key ? { ...f, extractedValue: value, userModified: true } : f)),
    );
  }

  function applyToForm() {
    const values = Object.fromEntries(fields.map((f) => [f.key, f.extractedValue]));
    if (!prefill) return;
    onApply(values, prefill);
    setState("APPLIED");
  }

  const { label, status } = stateLabel[state];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Evidence / OCR</h3>
        <StatusPill status={status} label={label} />
      </div>

      {state === "NOT_STARTED" && <FileUpload onFileSelected={handleFile} />}

      {(state === "UPLOADING" || state === "PROCESSING") && (
        <p className="text-sm text-muted">
          {state === "UPLOADING" ? "Uploading screenshot..." : "Running OCR..."}
        </p>
      )}

      {state === "REVIEW_REQUIRED" && (
        <div className="flex flex-col gap-3">
          {fields.map((field) => (
            <div key={field.key}>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-sm font-medium">{field.label}</label>
                <span
                  className={field.quality === "HIGH" ? "text-xs text-good" : "text-xs font-medium text-warning"}
                >
                  {field.quality === "HIGH" ? "High evidence" : field.quality === "REVIEW" ? "Review evidence" : "Unresolved"}
                </span>
              </div>
              <Input value={field.extractedValue} onChange={(e) => updateField(field.key, e.target.value)} />
              <p className="mt-1 truncate text-xs text-muted">from: “{field.sourceSnippet}”</p>
            </div>
          ))}

          <Button variant="secondary" size="sm" onClick={() => setShowRaw((v) => !v)}>
            {showRaw ? "Hide" : "Show"} raw OCR text
          </Button>
          {showRaw && (
            <pre className="font-data whitespace-pre-wrap rounded-lg bg-ground p-3 text-xs">
              {rawText}
            </pre>
          )}

          <div className="flex gap-2">
            <Button onClick={applyToForm}>Apply to form</Button>
            <Button variant="secondary" onClick={() => setState("NOT_STARTED")}>
              Discard
            </Button>
          </div>
        </div>
      )}

      {state === "APPLIED" && (
        <p className="text-sm text-muted">
          Fields applied to the incident form. Re-run OCR by uploading another screenshot.
        </p>
      )}

      {state === "FAILED" && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-warning">
            OCR or local interpretation was unavailable. Complete the incident manually; no Incident was created automatically.
          </p>
          <Button variant="secondary" size="sm" onClick={() => setState("NOT_STARTED")}>
            Try another screenshot
          </Button>
        </div>
      )}
    </div>
  );
}
