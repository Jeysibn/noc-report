import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardTitle } from "@/components/ui/Card";
import { FormField, Input, Select, Textarea } from "@/components/ui/Form";
import { Button } from "@/components/ui/Button";
import { FileUpload } from "@/components/ui/FileUpload";
import { OcrReviewPanel } from "@/components/incidents/OcrReviewPanel";
import { incidentService, evidenceService, ocrService } from "@/services";
import { ApiError } from "@/lib/http";
import type { IncidentStatus } from "@/types/domain";

interface IncidentDraft {
  title: string;
  service: string;
  environment: string;
  status: IncidentStatus;
  triggeredAt: string;
  recoveredAt: string;
  triggerValue: string;
  notes: string;
}

const emptyDraft: IncidentDraft = {
  title: "",
  service: "",
  environment: "production",
  status: "open",
  triggeredAt: "",
  recoveredAt: "",
  triggerValue: "",
  notes: "",
};

/**
 * Create Incident (Milestone 4, real-wired in Milestone 13). Two-column
 * layout: form + Evidence/OCR panel, per the UI Phase Plan. Can complete
 * without a log — log/evidence attachment is additive, never required to
 * save. Saving now calls the real POST /api/v1/incidents, and an attached
 * log file is uploaded via the real presigned-URL evidence flow
 * (ApiEvidenceService) once the incident exists.
 */
export function CreateIncident() {
  const navigate = useNavigate();
  const [draft, setDraft] = useState<IncidentDraft>(emptyDraft);
  const [logFile, setLogFile] = useState<File | null>(null);
  const [prefill, setPrefill] = useState<{ id: string; file: File } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update<K extends keyof IncidentDraft>(key: K, value: IncidentDraft[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  function handleOcrApply(values: Record<string, string>, appliedPrefill: { id: string; file: File }) {
    const triggeredAt = values.triggered_at
      ? values.triggered_at.replace(/\.\d{3}Z$/, '').replace(/Z$/, '').slice(0, 16)
      : '';
    const status = ["open", "investigating", "recovered"].includes(values.status)
      ? (values.status as IncidentStatus)
      : undefined;
    const recoveredAt = values.recovered_at
      ? values.recovered_at.replace(/\.\d{3}Z$/, '').replace(/Z$/, '').slice(0, 16)
      : '';
    setDraft((prev) => ({
      ...prev,
      title: values.title ?? prev.title,
      service: values.service ?? prev.service,
      environment: values.environment ?? prev.environment,
      status: status ?? prev.status,
      triggeredAt: triggeredAt || prev.triggeredAt,
      recoveredAt: recoveredAt || prev.recoveredAt,
      triggerValue: values.trigger_value ?? prev.triggerValue,
      notes: values.notes ?? prev.notes,
    }));
    setPrefill(appliedPrefill);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const incident = await incidentService.create({
        title: draft.title,
        service: draft.service || "unspecified",
        environment: draft.environment,
        status: draft.status,
        triggeredAt: draft.triggeredAt ? new Date(draft.triggeredAt).toISOString() : new Date().toISOString(),
        recoveredAt: draft.recoveredAt ? new Date(draft.recoveredAt).toISOString() : undefined,
        triggerValue: draft.triggerValue || undefined,
        notes: draft.notes || undefined,
      });
      if (prefill) {
        await ocrService.attachPrefill(prefill.id, incident.id);
      }
      if (logFile) {
        await evidenceService.upload(incident.id, logFile, "LOG");
      }
      navigate(`/incidents/${incident.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the incident.");
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Create Incident</h1>
        <p className="text-sm text-muted">
          Complete now, or attach the log later — nothing here blocks saving.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="flex flex-col gap-4">
          <CardTitle>Incident details</CardTitle>
          <FormField label="Title" htmlFor="title">
            <Input
              id="title"
              value={draft.title}
              onChange={(e) => update("title", e.target.value)}
              placeholder="e.g. Payment gateway timeout"
            />
          </FormField>
          <div className="grid grid-cols-2 gap-4">
            <FormField label="Service" htmlFor="service">
              <Input id="service" value={draft.service} onChange={(e) => update("service", e.target.value)} />
            </FormField>
            <FormField label="Environment" htmlFor="environment">
              <Select
                id="environment"
                value={draft.environment}
                onChange={(e) => update("environment", e.target.value)}
              >
                <option value="production">Production</option>
                <option value="staging">Staging</option>
              </Select>
            </FormField>
          </div>
          <FormField label="Status" htmlFor="status">
            <Select id="status" value={draft.status} onChange={(e) => update("status", e.target.value as IncidentStatus)}>
              <option value="open">Triggered / open</option>
              <option value="investigating">Investigating</option>
              <option value="recovered">Recovered</option>
            </Select>
          </FormField>
          <FormField label="Trigger value" htmlFor="triggerValue" hint="Optional — the alert threshold value">
            <Input
              id="triggerValue"
              value={draft.triggerValue}
              onChange={(e) => update("triggerValue", e.target.value)}
            />
          </FormField>
          <FormField label="Recovery time" htmlFor="recoveredAt" hint="Optional — used for recovered alerts">
            <Input
              id="recoveredAt"
              type="datetime-local"
              value={draft.recoveredAt}
              onChange={(e) => update("recoveredAt", e.target.value)}
            />
          </FormField>
          <FormField label="Trigger time" htmlFor="triggeredAt" hint="Optional — defaults to now">
            <Input
              id="triggeredAt"
              type="datetime-local"
              value={draft.triggeredAt}
              onChange={(e) => update("triggeredAt", e.target.value)}
            />
          </FormField>
          <FormField label="Notes" htmlFor="notes">
            <Textarea id="notes" value={draft.notes} onChange={(e) => update("notes", e.target.value)} />
          </FormField>

          <div>
            <p className="mb-1.5 text-sm font-medium">Log attachment</p>
            {logFile ? (
              <p className="text-sm text-muted">{logFile.name} — attached</p>
            ) : (
              <FileUpload label="Attach a log file (optional)" onFileSelected={setLogFile} />
            )}
          </div>

          {error && <p className="text-sm text-critical">{error}</p>}

          <div className="flex gap-2 pt-2">
            <Button onClick={save} disabled={!draft.title || saving}>
              {saving ? "Saving..." : "Save Incident"}
            </Button>
          </div>
        </Card>

        <Card>
          <OcrReviewPanel onApply={handleOcrApply} onPrefillInvalidated={() => setPrefill(null)} />
        </Card>
      </div>
    </div>
  );
}
