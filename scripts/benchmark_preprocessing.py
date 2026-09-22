"""Deterministic preprocessing benchmark harness.

Measures what is actually measurable without live external-runtime invocations (no
API-key billing path exists in this architecture, and burning real
subscription usage just to produce a benchmark number would be wasteful):
the deterministic, code-level effect of Phase 5's log compaction on input
size, across representative synthetic incident logs. Token/cost-per-call
numbers are read back from real `telemetry.json` files once this has run
in a live worker against queued jobs.

Run: python3 scripts/benchmark_preprocessing.py
"""
from __future__ import annotations

import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from sandbox.preprocessing import compact_log

random.seed(7)


def _gen_log(num_lines: int, *, noisy_patterns: int = 6, rare_severe: bool = True) -> str:
    lines = []
    for i in range(num_lines):
        pattern = i % noisy_patterns
        ts = f"2026-09-{(i % 28) + 1:02d}T{(i % 24):02d}:{(i % 60):02d}:{(i % 60):02d}Z"
        ip = f"10.0.{(i % 255)}.{(i // 255) % 255}"
        lines.append(f"{ts} WARN retry pattern-{pattern} from {ip} req={i}")
    if rare_severe:
        lines.insert(num_lines // 2, "2026-09-15T03:00:00Z FATAL OutOfMemoryError: heap space exhausted")
    return "\n".join(lines)


def main() -> None:
    scenarios = {
        "small (500 lines, under threshold)": _gen_log(500),
        "medium (5,000 lines)": _gen_log(5_000),
        "large (60,000 lines, one buried OOM)": _gen_log(60_000),
    }

    print(f"{'scenario':<40} {'raw bytes':>12} {'evidence bytes':>16} {'reduction':>10} {'severe kept':>12}")
    for name, raw in scenarios.items():
        raw_bytes = len(raw.encode("utf-8"))
        compacted = compact_log(raw)
        evidence_bytes = len(compacted.encode("utf-8"))
        reduction = 1 - (evidence_bytes / raw_bytes) if raw_bytes else 0
        severe_kept = "OOM" in compacted or "OutOfMemoryError" in compacted
        print(
            f"{name:<40} {raw_bytes:>12,} {evidence_bytes:>16,} {reduction:>9.1%} {str(severe_kept):>12}"
        )


if __name__ == "__main__":
    main()
