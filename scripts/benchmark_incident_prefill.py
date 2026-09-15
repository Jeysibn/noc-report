#!/usr/bin/env python3
"""Benchmark OCR field coverage, deterministic rules, and optional Ollama.

The default run is dependency-free beyond the API installation and produces a
50-case sanitized corpus. Pass --ollama to exercise the configured local
model; the script never downloads a model implicitly.
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.ocr import OcrLine, _extract_fields
from app.incident_prefill import build_incident_prefill, deterministic_prefill
from app.local_inference import OllamaLocalInference


FIELDS = ("title", "service", "status", "triggered_at", "recovered_at", "trigger_value", "environment")
SERVICES = ("betbingo-app-service", "payments-api", "checkout-worker", "auth-gateway", "notification-service")


@dataclass(frozen=True)
class Case:
    text: str
    expected: dict[str, str]


def corpus() -> list[Case]:
    cases: list[Case] = []
    for index in range(50):
        service = SERVICES[index % len(SERVICES)]
        status = "recovered" if index % 3 == 0 else "open"
        environment = "staging" if index % 4 == 0 else "production"
        title = f"{service} error rate high {index + 1}"
        triggered = f"2026-09-{(index % 20) + 1:02d} {(index % 12) + 1:02d}:2{index % 10}:00+08:00"
        value = f"{190 + index}K"
        status_line = "Recovered" if status == "recovered" else "Triggered"
        title_label = "Alert name" if index % 5 == 0 else "Alert"
        service_label = "Application name" if index % 5 == 0 else "Service"
        cases.append(
            Case(
                text=(
                    f"{title_label}: {title}\n{service_label}: {service}\nEnvironment: {environment}\n"
                    f"{status_line}: {triggered}\nTrigger value: {value}"
                ),
                expected={
                    "title": title,
                    "service": service,
                    "status": status,
                    "triggered_at": "" if status == "recovered" else triggered.replace(" ", "T", 1),
                    "recovered_at": triggered.replace(" ", "T", 1) if status == "recovered" else "",
                    "trigger_value": value,
                    "environment": environment,
                },
            )
        )
    return cases


def extraction(case: Case) -> dict:
    return {
        "raw_text": case.text,
        "lines": [{"text": line, "confidence": 0.96, "bbox": []} for line in case.text.splitlines()],
    }


def values_from_ocr_only(case: Case) -> dict[str, str]:
    lines = [OcrLine(line, 0.96, []) for line in case.text.splitlines()]
    fields = _extract_fields(lines)
    return {
        "title": fields.get("alert_title", {}).get("value", ""),
        "triggered_at": fields.get("triggered_at", {}).get("value", ""),
        "recovered_at": "",
        "trigger_value": fields.get("trigger_value", {}).get("value", ""),
    }


def values_from_prefill(result) -> dict[str, str]:
    return {field: getattr(result, field).value or "" for field in FIELDS}


def score(actual: dict[str, str], cases: list[Case]) -> dict:
    totals = {field: 0 for field in FIELDS}
    exact = 0
    for item, case in zip(actual, cases):
        matched = 0
        for field in FIELDS:
            if item.get(field, "") == case.expected[field]:
                totals[field] += 1
                matched += 1
        exact += matched == len(FIELDS)
    return {
        "cases": len(cases),
        "field_accuracy": {field: round(totals[field] / len(cases), 4) for field in FIELDS},
        "complete_form_exact_match": round(exact / len(cases), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama", action="store_true", help="call the configured local Ollama model")
    parser.add_argument("--ollama-sample-size", type=int, default=6)
    parser.add_argument("--ollama-timeout", type=float, default=75)
    parser.add_argument("--output", type=Path, help="write JSON results to this path")
    args = parser.parse_args()
    cases = corpus()
    # Full corpus establishes the baseline. Real CPU model calls use only the
    # six ambiguous label variants; explicit-label controls are already HIGH
    # quality and must not invoke local AI just to inflate the sample.
    ai_cases = [cases[index] for index in (5, 10, 20, 25, 35, 40)][: max(1, args.ollama_sample_size)]
    report = {"corpus": {"cases": len(cases), "sanitized": True}, "modes": {}}

    start = time.perf_counter()
    ocr_only = [values_from_ocr_only(case) for case in cases]
    report["modes"]["ocr_only"] = {**score(ocr_only, cases), "duration_ms": round((time.perf_counter() - start) * 1000)}

    start = time.perf_counter()
    rules = [values_from_prefill(deterministic_prefill(extraction(case), known_services=SERVICES)) for case in cases]
    report["modes"]["ocr_plus_rules"] = {
        **score(rules, cases),
        "duration_ms": round((time.perf_counter() - start) * 1000),
    }
    report["comparison_on_local_ai_sample"] = {
        "sample_cases": len(ai_cases),
        "ocr_plus_rules": score(
            [rules[cases.index(case)] for case in ai_cases],
            ai_cases,
        ),
    }

    if args.ollama:
        adapter = OllamaLocalInference(
            base_url="http://localhost:11434",
            model="qwen2.5:3b-instruct-q4_K_M",
            context_size=2048,
            keep_alive="5m",
            timeout_seconds=args.ollama_timeout,
            max_concurrency=1,
        )
        start = time.perf_counter()
        enhanced = [
            values_from_prefill(build_incident_prefill(extraction(case), inference=adapter, known_services=SERVICES))
            for case in ai_cases
        ]
        report["modes"]["ocr_plus_rules_plus_local_ai"] = {
            **score(enhanced, ai_cases),
            "duration_ms": round((time.perf_counter() - start) * 1000),
            "model": adapter.model,
            "sample_note": f"{len(ai_cases)} of 6 ambiguous label variants; explicit-label controls remain deterministic; full corpus remains 50 cases",
            "peak_rss_kb_process": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
    else:
        report["modes"]["ocr_plus_rules_plus_local_ai"] = {"status": "not_run", "reason": "pass --ollama; no model is downloaded implicitly"}

    report["process_peak_rss_kb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    payload = json.dumps(report, indent=2)
    print(payload)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
