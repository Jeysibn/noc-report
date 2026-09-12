import type { Shift, ShiftType } from "@/types/domain";
import type { ShiftService } from "@/services/shift.service";
import { httpRequest, ApiError } from "@/lib/http";
import { getCurrentUser } from "@/lib/session";

interface RawShift {
  id: string;
  starts_at: string;
  ends_at: string | null;
  state: string;
}

function shiftTypeFromHour(iso: string): ShiftType {
  const hour = new Date(iso).getUTCHours();
  if (hour >= 6 && hour < 14) return "day";
  if (hour >= 14 && hour < 22) return "swing";
  return "night";
}

/**
 * Real ShiftOut (app/schemas/schemas.py) has no shift-type or operator-name
 * columns — `type` is inferred from starts_at's hour bucket, `operator` is
 * the logged-in user (a real shift has no single named operator field
 * today; the current viewer is a reasonable stand-in until a real
 * assignment concept exists).
 */
function toShift(raw: RawShift): Shift {
  return {
    id: raw.id,
    type: shiftTypeFromHour(raw.starts_at),
    operator: getCurrentUser()?.displayName ?? "Unknown",
    startsAt: raw.starts_at,
    endsAt: raw.ends_at ?? raw.starts_at,
    status: raw.state === "active" ? "active" : raw.state === "ended" ? "ended" : "upcoming",
  };
}

export class ApiShiftService implements ShiftService {
  async getCurrentShift(): Promise<Shift | null> {
    try {
      const raw = await httpRequest<RawShift>("/api/v1/shifts/current");
      return toShift(raw);
    } catch (err) {
      // No active shift (404) — a real, expected state until someone
      // opens one, not an error worth surfacing as a failed request. Any
      // other failure (auth, network, 500) should still propagate.
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  }

  async openShift(): Promise<Shift> {
    const raw = await httpRequest<RawShift>("/api/v1/shifts/open", { method: "POST", body: {} });
    return toShift(raw);
  }
}
