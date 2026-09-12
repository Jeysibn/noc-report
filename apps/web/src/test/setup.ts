import "@testing-library/jest-dom/vitest";
import { beforeEach, vi } from "vitest";
import { __setCurrentUserForTests } from "@/lib/session";

/**
 * Milestone 13 frontend wiring made `@/services` point at real Api*Service
 * implementations that hit a real backend over fetch — component tests
 * have no backend to hit, so every test gets the old Mock* behavior by
 * default here. A test that cares about real-service wiring specifically
 * (see api/*.test.ts, if any) mocks `@/services` itself, which overrides
 * this for that file.
 */
vi.mock("@/services", async () => {
  const { MockIncidentService } = await import("@/mock/MockIncidentService");
  const { MockShiftService } = await import("@/mock/MockShiftService");
  const { MockSearchService } = await import("@/mock/MockSearchService");
  const { MockAnalyticsService } = await import("@/mock/MockAnalyticsService");
  const { MockAdminService } = await import("@/mock/MockAdminService");
  return {
    incidentService: new MockIncidentService(),
    shiftService: new MockShiftService(),
    searchService: new MockSearchService(),
    analyticsService: new MockAnalyticsService(),
    adminService: new MockAdminService(),
    analysisService: {
      listRuns: async () => [],
      getRun: async () => {
        throw new Error("analysisService.getRun is not mocked in this test");
      },
      requestAnalysis: async () => {
        throw new Error("analysisService.requestAnalysis is not mocked in this test");
      },
    },
    evidenceService: {
      upload: async (_incidentId: string, file: File) => ({
        id: "mock-evidence-id",
        evidenceType: "LOG",
        originalFilename: file.name,
      }),
      list: async () => [],
    },
    reportService: {
      list: async () => [],
      generate: async () => {
        throw new Error("reportService.generate is not mocked in this test");
      },
      getDownloadUrl: async () => {
        throw new Error("reportService.getDownloadUrl is not mocked in this test");
      },
    },
  };
});

/** Every test starts signed in as an Admin (full permissions) unless it says otherwise. */
beforeEach(() => {
  __setCurrentUserForTests({
    id: "00000000-0000-0000-0000-000000000001",
    username: "admin",
    displayName: "Admin User",
    email: "admin@example.com",
    roles: ["Admin"],
    permissions: [
      "incident.read",
      "incident.create",
      "incident.update",
      "incident.evidence.upload",
      "incident.analysis.execute",
      "report.read",
      "report.generate",
      "report.download",
    ],
  });
});
