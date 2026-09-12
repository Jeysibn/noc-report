import type { Shift } from "@/types/domain";

/** Behind this interface: Mock now, Api later — call sites never change. */
export interface ShiftService {
  getCurrentShift(): Promise<Shift | null>;
  openShift(): Promise<Shift>;
}
