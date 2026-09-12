import { clsx, type ClassValue } from "clsx";

/** Small classname join helper — no tailwind-merge yet, add if conflicts arise. */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
