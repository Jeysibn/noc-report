import { useSyncExternalStore } from "react";
import { ApiHealthService } from "@/api/ApiHealthService";
import type { OperationalHealth } from "@/types/health";

interface HealthState {
  data: OperationalHealth | null;
  loading: boolean;
  error: string | null;
}

const service = new ApiHealthService();
let state: HealthState = { data: null, loading: true, error: null };
const listeners = new Set<() => void>();
let timer: number | undefined;
let request: Promise<void> | null = null;

function emit() {
  listeners.forEach((listener) => listener());
}

async function refresh() {
  if (request) return request;
  state = { ...state, loading: state.data === null, error: null };
  emit();
  request = service
    .getDependencies()
    .then((data) => {
      state = { data, loading: false, error: null };
    })
    .catch((error: unknown) => {
      state = {
        ...state,
        loading: false,
        error: error instanceof Error ? error.message : "Health check failed",
      };
    })
    .finally(() => {
      request = null;
      emit();
    });
  return request;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    void refresh();
    timer = window.setInterval(() => void refresh(), 30_000);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== undefined) {
      window.clearInterval(timer);
      timer = undefined;
    }
  };
}

function getSnapshot() {
  return state;
}

export function useOperationalHealth() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
