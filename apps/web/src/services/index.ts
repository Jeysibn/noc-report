import { ApiIncidentService } from "@/api/ApiIncidentService";
import { ApiShiftService } from "@/api/ApiShiftService";
import { ApiAnalysisService } from "@/api/ApiAnalysisService";
import { ApiEvidenceService } from "@/api/ApiEvidenceService";
import { ApiReportService } from "@/api/ApiReportService";
import { ApiSearchService } from "@/api/ApiSearchService";
import { ApiAnalyticsService } from "@/api/ApiAnalyticsService";
import { ApiAdminService } from "@/api/ApiAdminService";
import { ApiOcrService } from "@/api/ApiOcrService";

/** Production service wiring. Test setup replaces these services with fakes. */
export const incidentService = new ApiIncidentService();
export const shiftService = new ApiShiftService();
export const analysisService = new ApiAnalysisService();
export const evidenceService = new ApiEvidenceService();
export const reportService = new ApiReportService();
export const searchService = new ApiSearchService();
export const analyticsService = new ApiAnalyticsService();
export const adminService = new ApiAdminService();
export const ocrService = new ApiOcrService();
