export type OcrState =
  | "NOT_STARTED"
  | "UPLOADING"
  | "PROCESSING"
  | "REVIEW_REQUIRED"
  | "APPLIED"
  | "FAILED";

export interface OcrField {
  key: string;
  label: string;
  extractedValue: string;
  confidence: number; // 0-1
  sourceSnippet: string;
  userModified: boolean;
}

export interface OcrResult {
  fields: OcrField[];
  rawText: string;
}

/**
 * Deterministic mock OCR pipeline — no real PaddleOCR/OpenCV until
 * Milestone 10. Simulates UPLOADING -> PROCESSING -> REVIEW_REQUIRED with
 * realistic timing so the state-machine UX can be built and tested now.
 */
export function simulateOcr(
  _file: File,
  onState: (state: OcrState) => void,
): Promise<OcrResult> {
  return new Promise((resolve) => {
    onState("UPLOADING");
    setTimeout(() => {
      onState("PROCESSING");
      setTimeout(() => {
        onState("REVIEW_REQUIRED");
        resolve({
          rawText:
            "ALERT: PaymentGatewayTimeout\nService: payments-api\nEnvironment: production\nTriggered: 2026-09-09 01:52 UTC\nHost: pay-worker-03",
          fields: [
            {
              key: "title",
              label: "Title",
              extractedValue: "Payment gateway timeout",
              confidence: 0.94,
              sourceSnippet: "ALERT: PaymentGatewayTimeout",
              userModified: false,
            },
            {
              key: "service",
              label: "Service",
              extractedValue: "payments-api",
              confidence: 0.97,
              sourceSnippet: "Service: payments-api",
              userModified: false,
            },
            {
              key: "environment",
              label: "Environment",
              extractedValue: "production",
              confidence: 0.9,
              sourceSnippet: "Environment: production",
              userModified: false,
            },
            {
              key: "host",
              label: "Host",
              extractedValue: "pay-worker-03",
              confidence: 0.58,
              sourceSnippet: "Host: pay-worker-03",
              userModified: false,
            },
          ],
        });
      }, 900);
    }, 500);
  });
}

export const LOW_CONFIDENCE_THRESHOLD = 0.7;
