import { ApiIncidentService } from "@/api/ApiIncidentService";
import { ApiShiftService } from "@/api/ApiShiftService";
import { ApiAnalysisService } from "@/api/ApiAnalysisService";
import { ApiEvidenceService } from "@/api/ApiEvidenceService";
import { ApiReportService } from "@/api/ApiReportService";
import { ApiSearchService } from "@/api/ApiSearchService";
import { ApiAnalyticsService } from "@/api/ApiAnalyticsService";
import { ApiAdminService } from "@/api/ApiAdminService";

/**
 * Single wiring point: swap the Mock* implementations for Api* ones here
 * when the real backend lands — nothing else in the app changes, per
 * coding-agent rule 18 (mock/real behind the same interface).
 *
 * Milestone 13 (frontend wiring): flipped from Mock* to Api* for real.
 * The Mock* classes stay in src/mock/ for tests and as a reference for the
 * interface contract, they're just no longer what the running app uses.
 */
export const incidentService = new ApiIncidentService();
export const shiftService = new ApiShiftService();
export const analysisService = new ApiAnalysisService();
export const evidenceService = new ApiEvidenceService();
export const reportService = new ApiReportService();
export const searchService = new ApiSearchService();
export const analyticsService = new ApiAnalyticsService();
export const adminService = new ApiAdminService();
