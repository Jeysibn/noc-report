import type { ShiftService } from "@/services/shift.service";
import { mockCurrentShift } from "./fixtures/shift";

export class MockShiftService implements ShiftService {
  async getCurrentShift() {
    return mockCurrentShift;
  }

  async openShift() {
    return mockCurrentShift;
  }
}
