import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { ReportPreview } from "./ReportPreview";

const getDocument = vi.fn();
const getScreenshotBlob = vi.fn();

vi.mock("@/services", () => ({
  reportService: {
    getDocument: (...args: unknown[]) => getDocument(...args),
    getScreenshotBlob: (...args: unknown[]) => getScreenshotBlob(...args),
  },
}));

describe("ReportPreview", () => {
  it("renders sections in canonical order: Alerts -> General Summary -> Log Analysis", async () => {
    getDocument.mockResolvedValue({
      title: "Daily Alert & Log Analysis Report",
      metadata: [{ type: "metadata", label: "Shift", value: "MS" }],
      blocks: [
        { type: "heading", text: "Alerts", level: 1 },
        { type: "alert_navigation", entries: [{ text: "Alert #1 - Payment timeout", target: "log_analysis_abc" }] },
        {
          type: "incident_evidence",
          heading: "Alert #1 - Payment timeout",
          incident_id: "inc-001",
          metadata: [],
          links: [{ type: "link", label: "Grafana", url: "https://grafana.example/inc-001" }],
          log_file: { type: "log_file_reference", filename: "1-payments-logs.json" },
          screenshots: [],
          navigation_target: "log_analysis_abc",
        },
        { type: "page_break" },
        { type: "heading", text: "General Summary", level: 1 },
        {
          type: "bilingual_text",
          text_zh: "班次稳定。",
          text_en: "The shift was stable.",
          heading_zh: "Chinese Summary",
          heading_en: "English Summary",
        },
        { type: "page_break" },
        { type: "heading", text: "Log Analysis", level: 1 },
        {
          type: "analysis_reference",
          heading: "INC-001 — Payment timeout",
          available: true,
          analysis_run_id: "run-001",
          unavailable_text: null,
          bookmark: "log_analysis_abc",
          metadata: [],
          children: [],
          summary: {
            type: "bilingual_text",
            text_zh: "完整摘要",
            text_en: "Full summary",
            heading_zh: "Chinese",
            heading_en: "English",
          },
          key_finds: {
            type: "bilingual_find_list",
            heading_zh: "重点发现",
            heading_en: "Key Finds",
            finds_zh: [{ label: "重点1", detail: "细节1", stat: "(10 / 50%)" }],
            finds_en: [{ label: "Finding 1", detail: "Detail 1", stat: "(10 / 50%)" }],
          },
          secondary_finds: null,
          likely_cause: null,
          recommended_action: null,
        },
      ],
    });

    render(<ReportPreview reportId="report-1" />);
    await act(async () => {});

    const headings = screen.getAllByRole("heading", { level: 2 }).map((el) => el.textContent);
    expect(headings.indexOf("Alerts")).toBeLessThan(headings.indexOf("General Summary"));
    expect(headings.indexOf("General Summary")).toBeLessThan(headings.indexOf("Log Analysis"));

    expect(screen.getAllByText("Alert #1 - Payment timeout")).toHaveLength(2);
    expect(screen.getByText("1-payments-logs.json")).toBeInTheDocument();
    expect(screen.getByText("The shift was stable.")).toBeInTheDocument();
    expect(screen.getByText("Full summary")).toBeInTheDocument();
    expect(screen.getByText("Finding 1")).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "Alert Navigation" });
    expect(navigation.querySelector('a[href="#log_analysis_abc"]')).toBeInTheDocument();
    expect(document.getElementById("log_analysis_abc")).toBeInTheDocument();
  });

  it("shows a friendly message when no structured preview exists", async () => {
    const { ApiError } = await import("@/lib/http");
    getDocument.mockRejectedValue(new ApiError(404, "not found"));

    render(<ReportPreview reportId="report-2" />);
    await act(async () => {});

    expect(
      screen.getByText(/no structured preview is available/i),
    ).toBeInTheDocument();
  });

  it("renders single-language findings in Chinese-before-English order", async () => {
    getDocument.mockResolvedValue({
      title: "Report",
      metadata: [],
      blocks: [
        { type: "heading", text: "Log Analysis", level: 1 },
        {
          type: "analysis_reference",
          heading: "Alert #1 - API",
          available: true,
          metadata: [],
          children: [
            { type: "heading", text: "Chinese", level: 3 },
            { type: "find_list", heading: "Key Finds", language: "Chinese", finds: [{ label: "中文发现", detail: null, stat: null }] },
            { type: "heading", text: "English", level: 3 },
            { type: "find_list", heading: "Key Finds", language: "English", finds: [{ label: "English finding", detail: null, stat: null }] },
          ],
          screenshots: [],
          log_file: null,
          summary: null,
          key_finds: null,
          secondary_finds: null,
          likely_cause: null,
          recommended_action: null,
        },
      ],
    });
    render(<ReportPreview reportId="report-language" />);
    await act(async () => {});
    const chinese = screen.getByText("中文发现");
    const english = screen.getByText("English finding");
    expect(chinese.compareDocumentPosition(english) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
