"""AI cost-optimization mission Phase 2, Issue 8: real A/B benchmark.

Invokes the ACTUAL `claude` CLI (via sandbox/entrypoint.py's own
`run_skill`, not a mock) against representative, safe, synthetic fixture
logs covering every error type the mission brief calls out, and records
real cost/tokens/duration/output-quality per fixture.

This spends real Claude subscription usage — gated behind
NOC_LIVE_BENCHMARK=1, same convention as bridge/tests/test_bridge.py's
NOC_BRIDGE_LIVE_CLAUDE_TESTS gate. Run explicitly:

    NOC_LIVE_BENCHMARK=1 python3 scripts/benchmark_live_ab.py

Unlike scripts/benchmark_preprocessing.py (which only measures the
deterministic byte-size effect of log compaction, with no live calls),
this is the actual token/cost/duration/quality measurement the mission
asked for, run directly against the real `claude` binary already
authenticated on this machine (not through the Docker sandbox — the
sandbox only adds process isolation, it doesn't change what gets sent to
or received from Claude, so invoking entrypoint.run_skill() directly here
measures the exact same prompt/schema/CLI-flags real jobs use).
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
_ENTRYPOINT_PATH = ROOT / "sandbox" / "entrypoint.py"
_spec = importlib.util.spec_from_file_location("sandbox_entrypoint", _ENTRYPOINT_PATH)
entrypoint = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(entrypoint)

# This machine's real `claude` binary isn't bind-mounted at the sandbox's
# expected in-container path — point entrypoint.py at the real one.
import shutil
_real_claude = shutil.which("claude")
if _real_claude:
    entrypoint.CLAUDE_BINARY = _real_claude

os.environ.setdefault("SKILL_MODEL", "claude-sonnet-5")
os.environ.setdefault("SKILL_EFFORT", "low")
os.environ.setdefault("SKILL_EFFORT_ESCALATION", "medium")
os.environ.setdefault("SKILL_MAX_BUDGET_USD", "0.50")
os.environ.setdefault("SKILL_CLI_TIMEOUT_SECONDS", "120")
entrypoint.SKILLS_DIR = ROOT / "skills"


# --- Fixtures: one per error type called out in the mission brief -------

FIXTURES: dict[str, str] = {
    "NullPointerException": (
        "2026-09-13T02:11:04Z ERROR [OrderService] "
        "java.lang.NullPointerException: Cannot invoke "
        '"Customer.getId()" because "customer" is null\n'
        "\tat com.acme.orders.OrderService.charge(OrderService.java:88)\n"
        "\tat com.acme.orders.OrderController.post(OrderController.java:41)\n"
    ),
    "DuplicateKeyException": (
        "2026-09-13T02:12:10Z ERROR [InventoryWorker] "
        "org.springframework.dao.DuplicateKeyException: could not execute statement; "
        "SQL [insert into sku_reservation]; constraint [uq_sku_reservation_order_id]\n"
    ),
    "DateTimeParseException": (
        "2026-09-13T02:13:00Z ERROR [ReportScheduler] "
        "java.time.format.DateTimeParseException: Text '2026-13-40' could not be parsed: "
        "Invalid value for MonthOfYear (valid values 1 - 12)\n"
    ),
    "JsonParsingFailure": (
        "2026-09-13T02:14:22Z ERROR [WebhookIngest] "
        "com.fasterxml.jackson.core.JsonParseException: Unexpected character ('}' (code 125)): "
        "was expecting double-quote to start field name\n"
        " at [Source: (String)\"{\"order_id\": 1001, }\"; line: 1, column: 22]\n"
    ),
    "BusinessException_zh": (
        "2026-09-13T02:15:47Z ERROR [PaymentService] "
        "BusinessException: errorCode=4102 message=余额不足 "
        "(insufficient balance) orderId=ORD-88213 userId=48213\n"
    ),
    "HTTP403Forbidden": (
        "2026-09-13T02:16:03Z ERROR [GatewayClient] request to /api/v2/payouts failed "
        "with HTTP 403 Forbidden: token scope does not permit payouts.write\n"
    ),
    "ClickHouseTimeout": (
        "2026-09-13T02:17:55Z ERROR [AnalyticsWorker] "
        "ClickHouseException: Timeout error: Read timed out after 30000ms querying "
        "table events_raw, query_id=8f2c1e\n"
    ),
    "ElasticsearchFailure": (
        "2026-09-13T02:18:12Z ERROR [SearchIndexer] "
        "ElasticsearchStatusException: [search_phase_execution_exception] all shards failed; "
        "shard=[3], node unavailable, index=[orders-2026.09]\n"
    ),
    "JVM_OOM": (
        "2026-09-13T02:19:40Z FATAL [Worker-3] java.lang.OutOfMemoryError: Java heap space\n"
        "\tat com.acme.batch.LedgerWorker.flush(LedgerWorker.java:88)\n"
    ),
    "RepetitiveError": "\n".join(
        f"2026-09-13T02:2{i%6}:00Z WARN [RetryWorker] retry attempt {i} for req-{i:04d} timed out"
        for i in range(40)
    ),
    "MultipleUnrelatedErrors": (
        "2026-09-13T02:30:00Z ERROR [A] NullPointerException at com.acme.A.run(A.java:1)\n"
        "2026-09-13T02:30:05Z ERROR [B] request to /api/x failed with HTTP 500\n"
        "2026-09-13T02:30:10Z ERROR [C] DuplicateKeyException on unique_email\n"
    ),
    "RareCriticalHiddenInNoise": "\n".join(
        [f"2026-09-13T03:{i%60:02d}:00Z WARN [Poller] retry attempt {i} timed out" for i in range(300)]
        + ["2026-09-13T03:59:59Z FATAL [Worker-1] java.lang.OutOfMemoryError: Java heap space, "
           "Caused by: com.acme.payments.LedgerWorker.flush(LedgerWorker.java:88)"]
        + [f"2026-09-13T04:{i%60:02d}:00Z WARN [Poller] retry attempt {i} timed out" for i in range(300, 600)]
    ),
}


def main() -> None:
    if not os.environ.get("NOC_LIVE_BENCHMARK"):
        print(
            "Refusing to spend real Claude usage without NOC_LIVE_BENCHMARK=1 set.\n"
            "Run: NOC_LIVE_BENCHMARK=1 python3 scripts/benchmark_live_ab.py",
            file=sys.stderr,
        )
        sys.exit(1)

    rows = []
    out_path = ROOT / "scripts" / "benchmark_live_ab_results.json"
    print(f"{'fixture':<28} {'cost_usd':>9} {'in_tok':>7} {'out_tok':>7} {'ms':>7} "
          f"{'conf':>5} {'sev':>9} {'escal':>6}", flush=True)
    for name, log_text in FIXTURES.items():
        t0 = time.time()
        try:
            result, telemetry = entrypoint.run_skill(log_text, "log-triage-summary")
        except Exception as exc:  # pragma: no cover - live/manual script
            print(f"{name:<28} FAILED: {exc}", flush=True)
            rows.append({"fixture": name, "error": str(exc)})
            out_path.write_text(json.dumps(rows, indent=2))
            continue
        wall_ms = int((time.time() - t0) * 1000)
        row = {
            "fixture": name,
            "cost_usd": telemetry.get("estimated_cost_usd") or telemetry.get("cost_usd"),
            "input_tokens": telemetry.get("input_tokens"),
            "output_tokens": telemetry.get("output_tokens"),
            "duration_ms": telemetry.get("duration_ms"),
            "wall_ms": wall_ms,
            "confidence": result.get("confidence"),
            "severity_signal": result.get("severity_signal"),
            "escalated": telemetry.get("escalated"),
            "likely_cause_en": result.get("likely_cause_en"),
        }
        rows.append(row)
        print(
            f"{name:<28} {row['cost_usd'] or 0:>9.4f} {row['input_tokens'] or 0:>7} "
            f"{row['output_tokens'] or 0:>7} {row['duration_ms'] or 0:>7} "
            f"{row['confidence'] or 0:>5.2f} {str(row['severity_signal']):>9} "
            f"{str(row['escalated']):>6}",
            flush=True,
        )
        out_path.write_text(json.dumps(rows, indent=2))  # incremental — survives a timeout
    print(f"\nWrote full results (incl. likely_cause_en per fixture) to {out_path}", flush=True)


if __name__ == "__main__":
    main()
