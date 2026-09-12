import { useState } from "react";
import { FileUpload } from "@/components/ui/FileUpload";
import { Button } from "@/components/ui/Button";
import { StatusPill } from "@/components/ui/StatusPill";
import { Input } from "@/components/ui/Form";
import {
  simulateOcr,
  LOW_CONFIDENCE_THRESHOLD,
  type OcrField,
  type OcrResult,
  type OcrState,
} from "@/mock/ocrSimulator";

const stateLabel: Record<OcrState, { label: string; status: "neutral" | "warning" | "good" | "info" }> = {
  NOT_STARTED: { label: "Not started", status: "neutral" },
  UPLOADING: { label: "Uploading", status: "info" },
  PROCESSING: { label: "Processing", status: "info" },
  REVIEW_REQUIRED: { label: "Review required", status: "warning" },
  APPLIED: { label: "Applied", status: "good" },
  FAILED: { label: "Failed", status: "warning" },
};

/**
 * OCR Review (Milestone 4). State machine
 * NOT_STARTED -> UPLOADING -> PROCESSING -> REVIEW_REQUIRED -> APPLIED/FAILED.
 * Low-confidence fields never silently overwrite manual entries — the
 * operator applies the reviewed values explicitly via "Apply to form".
 */
export function OcrReviewPanel({ onApply }: { onApply: (values: Record<string, string>) => void }) {
  const [state, setState] = useState<OcrState>("NOT_STARTED");
  const [result, setResult] = useState<OcrResult | null>(null);
  const [fields, setFields] = useState<OcrField[]>([]);
  const [showRaw, setShowRaw] = useState(false);

  function handleFile(file: File) {
    setState("UPLOADING");
    simulateOcr(file, setState).then((res) => {
      setResult(res);
      setFields(res.fields);
    });
  }

  function updateField(key: string, value: string) {
    setFields((prev) =>
      prev.map((f) => (f.key === key ? { ...f, extractedValue: value, userModified: true } : f)),
    );
  }

  function applyToForm() {
    const values = Object.fromEntries(fields.map((f) => [f.key, f.extractedValue]));
    onApply(values);
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
                  className={
                    field.confidence < LOW_CONFIDENCE_THRESHOLD
                      ? "text-xs font-medium text-warning"
                      : "text-xs text-muted"
                  }
                >
                  {Math.round(field.confidence * 100)}% confidence
                  {field.confidence < LOW_CONFIDENCE_THRESHOLD ? " — verify" : ""}
                </span>
              </div>
              <Input value={field.extractedValue} onChange={(e) => updateField(field.key, e.target.value)} />
              <p className="mt-1 truncate text-xs text-muted">from: “{field.sourceSnippet}”</p>
            </div>
          ))}

          <Button variant="secondary" size="sm" onClick={() => setShowRaw((v) => !v)}>
            {showRaw ? "Hide" : "Show"} raw OCR text
          </Button>
          {showRaw && result && (
            <pre className="font-data whitespace-pre-wrap rounded-lg bg-ground p-3 text-xs">
              {result.rawText}
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
    </div>
  );
}
