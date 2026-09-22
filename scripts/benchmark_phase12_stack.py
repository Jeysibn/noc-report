#!/usr/bin/env python3
"""Sample the local Phase 12 stack's memory, CPU, and swap footprint.

This is intentionally an observation tool, not a process supervisor. Start
the representative API/web/Ollama stack first, then pass the relevant
host PIDs. It reports actual host measurements and does not pretend a 32 GiB
developer host is a 16 GiB VM.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path


def _number(value: str) -> float:
    match = re.search(r"([0-9.]+)", value)
    return float(match.group(1)) if match else 0.0


def _docker_stats(containers: list[str]) -> list[dict]:
    command = ["docker", "stats", "--no-stream", "--format", "{{json .}}", *containers]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    rows = []
    for line in completed.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        memory = item.get("MemUsage", "")
        used, _, limit = memory.partition(" /")
        rows.append(
            {
                "name": item.get("Name"),
                "memory_used_mib": round(_number(used) * (1024 if "GiB" in used else 1), 1),
                "memory_limit": limit.strip(),
                "cpu_percent": _number(item.get("CPUPerc", "0")),
            }
        )
    return rows


def _host_memory() -> dict:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, raw = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            values[key] = int(raw.strip().split()[0]) // 1024
    return values


def _swap_counters() -> dict:
    values = {}
    for line in Path("/proc/vmstat").read_text().splitlines():
        key, value = line.split()[:2]
        if key in {"pswpin", "pswpout"}:
            values[key] = int(value)
    return values


def _process_rss(pids: list[int]) -> list[dict]:
    result = []
    for pid in pids:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid=,rss=,pcpu=,comm="],
            capture_output=True,
            text=True,
            check=False,
        )
        parts = completed.stdout.split()
        if len(parts) >= 4:
            result.append(
                {
                    "pid": int(parts[0]),
                    "rss_mib": round(int(parts[1]) / 1024, 1),
                    "cpu_percent": float(parts[2]),
                    "command": parts[3],
                }
            )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--pid", type=int, action="append", default=[])
    parser.add_argument(
        "--container",
        action="append",
        dest="containers",
        default=["noc-report-postgres", "noc-report-rabbitmq", "noc-report-minio", "noc-report-ollama"],
    )
    args = parser.parse_args()
    started = time.monotonic()
    before_swap = _swap_counters()
    samples = []
    while True:
        samples.append(
            {
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "host": _host_memory(),
                "containers": _docker_stats(args.containers),
                "processes": _process_rss(args.pid),
            }
        )
        if time.monotonic() - started >= args.duration:
            break
        time.sleep(args.interval)
    after_swap = _swap_counters()
    peak_container_memory = {}
    for sample in samples:
        for container in sample["containers"]:
            name = container["name"]
            peak_container_memory[name] = max(
                peak_container_memory.get(name, 0.0), container["memory_used_mib"]
            )
    print(
        json.dumps(
            {
                "duration_seconds": round(time.monotonic() - started, 2),
                "sample_count": len(samples),
                "initial_host": samples[0]["host"],
                "final_host": samples[-1]["host"],
                "peak_container_memory_mib": peak_container_memory,
                "peak_process_rss_mib": {
                    str(pid): max(
                        (item["rss_mib"] for sample in samples for item in sample["processes"] if item["pid"] == pid),
                        default=0.0,
                    )
                    for pid in args.pid
                },
                "swap_counter_delta": {
                    key: after_swap.get(key, 0) - before_swap.get(key, 0)
                    for key in {"pswpin", "pswpout"}
                },
                "samples": samples,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
