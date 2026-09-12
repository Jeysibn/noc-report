"""Milestone 0.5 spike entry point: run one real end-to-end sandbox
invocation and print the result. Run with:

    python3 -m noc_bridge.spike_check
"""
from __future__ import annotations

import pathlib
import sys

from noc_bridge.sandbox_runner import run_skill_job

SAMPLE_LOG = """\
2026-09-09T02:11:03Z INFO  request received path=/api/incidents
2026-09-09T02:11:03Z ERROR unhandled exception in request path: NullReferenceException
2026-09-09T02:11:04Z INFO  retry scheduled
"""


def main() -> int:
    skills_dir = pathlib.Path(__file__).resolve().parents[2] / "skills"
    result = run_skill_job(SAMPLE_LOG, skills_dir)

    print(f"exit_code: {result.exit_code}")
    print(f"logs:\n{result.logs}")
    print(f"output: {result.output}")

    if result.exit_code != 0 or result.output is None:
        print("SPIKE FAILED", file=sys.stderr)
        return 1

    print("SPIKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
