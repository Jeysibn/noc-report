#!/usr/bin/env python3
"""Sandbox entrypoint: invokes the real Claude Code CLI to run a skill
against mounted input, writing a structured JSON result to the writable
output mount (master plan §27 steps 9-12).

The Milestone 0.5 / Milestone 12 deterministic stand-in has been replaced
(ADR 0003) — this now shells out to the `claude` binary bind-mounted at
/usr/local/bin/claude by the bridge, authenticated via the OAuth
credential bind-mounted at /home/sandbox/.claude/.credentials.json (never
a separate ANTHROPIC_API_KEY — `--bare` is deliberately NOT passed, so the
CLI reads that credential file instead of requiring an API key).

Mount contract (unchanged from the stand-in):
  /input   (ro) — input files: log.txt for log-triage-summary,
                  snapshot.json for daily-alert-report
  /skills  (ro) — skills/<skill_name>/SKILL.md
  /output  (rw) — result.json is written here
Env vars (set by the bridge per job, via run_job_sandbox's `environment`):
  SKILL_NAME            e.g. "log-triage-summary"
  SKILL_MODEL           e.g. "claude-sonnet-5"
  SKILL_EFFORT          e.g. "medium" (Milestone 17 gap follow-up: AI
                        Configuration's default_effort/a job's own
                        requested effort — passed to the CLI as --effort)
  SKILL_MAX_BUDGET_USD  e.g. "0.50"
"""
import json
import os
import pathlib
import re
import subprocess
import sys

INPUT_DIR = pathlib.Path("/input")
SKILLS_DIR = pathlib.Path("/skills")
OUTPUT_DIR = pathlib.Path("/output")
CLAUDE_BINARY = "/usr/local/bin/claude"

# Mirrors skills/log-triage-summary/SKILL.md's contract (master plan §29).
# Kept here (rather than parsed from the SKILL.md prose) so the CLI's
# --json-schema flag gets a real JSON Schema regardless of skill wording.
_FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "label_en": {"type": "string"},
        "label_zh": {"type": "string"},
        "count": {"type": ["integer", "null"]},
        "percentage": {"type": ["number", "null"]},
        "detail_en": {"type": "string"},
        "detail_zh": {"type": "string"},
    },
    "required": ["label_en", "label_zh", "count", "percentage", "detail_en", "detail_zh"],
    "additionalProperties": False,
}

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary_en": {"type": "string"},
        "summary_zh": {"type": "string"},
        "key_finds": {"type": "array", "items": _FIND_SCHEMA, "minItems": 1},
        "secondary_finds": {"type": "array", "items": _FIND_SCHEMA},
        "likely_cause_en": {"type": "string"},
        "likely_cause_zh": {"type": "string"},
        "severity_signal": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "recommended_action_en": {"type": "string"},
        "recommended_action_zh": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": [
        "summary_en", "summary_zh", "key_finds", "secondary_finds",
        "likely_cause_en", "likely_cause_zh", "severity_signal",
        "recommended_action_en", "recommended_action_zh", "confidence",
    ],
    "additionalProperties": False,
}

_SCHEMAS = {
    "log-triage-summary": _ANALYSIS_SCHEMA,
    # Mirrors skills/daily-alert-report/SKILL.md's contract (master plan
    # §29, Milestone 14): bilingual overview, sections carrying through
    # grafana_url/log_filename/screenshots and each incident's analysis
    # object in the same bilingual shape as log-triage-summary above.
    "daily-alert-report": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "overview_en": {"type": "string"},
            "overview_zh": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "incident_display_id": {"type": "string"},
                        "title": {"type": "string"},
                        "status": {"type": "string"},
                        "grafana_url": {"type": ["string", "null"]},
                        "log_filename": {"type": ["string", "null"]},
                        "screenshots": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "bucket": {"type": "string"},
                                    "object_key": {"type": "string"},
                                    "filename": {"type": "string"},
                                },
                                "required": ["bucket", "object_key", "filename"],
                                "additionalProperties": False,
                            },
                        },
                        "analysis": {
                            "anyOf": [_ANALYSIS_SCHEMA, {"type": "null"}],
                        },
                    },
                    "required": [
                        "incident_display_id", "title", "status", "grafana_url",
                        "log_filename", "screenshots", "analysis",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title", "overview_en", "overview_zh", "sections"],
        "additionalProperties": False,
    },
}

# Per skill: which /input file it reads, and the phrase introducing it in
# the prompt. Both skills take one input file per job.
_INPUT_FILE_BY_SKILL = {
    "log-triage-summary": ("log.txt", "Here is the log excerpt to analyze:"),
    "daily-alert-report": ("snapshot.json", "Here is the frozen shift snapshot to report on:"),
}

# A real incident's attached log can run into the low single-digit MB
# range, which blows straight through the CLI's ~1M token request limit
# once the skill text/schema/system-prompt/tool-definition overhead is
# added on top (seen in practice: a ~1.09M-token request rejected with
# terminal_reason='prompt_too_long' for a log that was, on its own,
# nowhere near that size as raw text — the fixed overhead is large).
# Rather than blindly truncating (which would silently drop whichever
# error patterns happen to fall outside the kept window, undermining the
# skill's count/percentage contract), oversized logs are compacted by
# pattern: near-duplicate lines differing only in ids/timestamps/etc.
# collapse to one representative example plus an exact occurrence count,
# computed here in plain Python over the *entire* log, not sampled — so
# the skill's key_finds counts stay literal even after compaction.
# Lowered from 300000/60 (Milestone 17 gap follow-up: cost) — at the old
# ceiling, a single call could still hand the model 300k chars of context
# before compaction even engaged, which is exactly the "measure the
# prompt, not the tier" cost driver noted against the host-side CLI
# bridge design (~/claude-cli-bridge-notes): model tier barely moves
# wall-clock or cost at small prompt sizes, but a large prompt does. This
# triggers compaction sooner and keeps the compacted output itself
# smaller, on top of the model/effort tiering done in AI Configuration.
MAX_LOG_CHARS = int(os.environ.get("SKILL_MAX_LOG_CHARS", "80000"))
MAX_PATTERN_GROUPS = int(os.environ.get("SKILL_MAX_PATTERN_GROUPS", "40"))

# Substituted out before grouping so lines that are "the same error, one
# different request" collapse into the same signature: UUIDs/trace-style
# hex ids, IPv4 addresses, ISO-ish timestamps, and bare multi-digit
# numbers (line numbers, ports, byte counts, etc).
_SIG_PATTERNS = [
    re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    re.compile(r"\b[0-9a-fA-F]{16,40}\b"),
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
    re.compile(r"\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"),
    re.compile(r"\d+"),
]


def _signature(line: str) -> str:
    sig = line
    for pattern in _SIG_PATTERNS:
        sig = pattern.sub("#", sig)
    return sig


# Cost-optimization mission, Phase 5 fix: frequency-only ranking silently
# dropped rare-but-critical patterns (e.g. one OutOfMemoryError buried under
# 80,000 WARN retries never made it into the top MAX_PATTERN_GROUPS and was
# never sent to Claude at all). Severity is now a second, independent
# selection dimension: any pattern whose line matches one of these markers
# is always kept, regardless of how it ranks by frequency. This mirrors the
# "never use frequency alone" rule — frequency and severity are separate
# axes, not one ranking.
_SEVERITY_MARKERS = re.compile(
    r"\b(FATAL|OutOfMemoryError|OOM|StackOverflowError|SecurityException|"
    r"DataLoss|data\s*loss|corrupt(?:ion|ed)?|Deadlock|panic)\b",
    re.IGNORECASE,
)


def _is_severe(line: str) -> bool:
    return bool(_SEVERITY_MARKERS.search(line))


def _extract_lines(input_text: str) -> list[str]:
    """log.txt is usually a JSON array of {"line": ...} evidence entries
    (per app.models.models.Evidence's LOG upload shape); fall back to
    treating it as plain newline-delimited text for anything else."""
    try:
        parsed = json.loads(input_text)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "line" in parsed[0]:
        return [str(entry.get("line", "")) for entry in parsed]
    return input_text.splitlines()


def _compact_log_if_oversized(input_text: str) -> str:
    if len(input_text) <= MAX_LOG_CHARS:
        return input_text

    lines = _extract_lines(input_text)
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for line in lines:
        sig = _signature(line)
        if sig not in groups:
            groups[sig] = []
            order.append(sig)
        if len(groups[sig]) < 2:
            groups[sig].append(line)

    counts = {sig: 0 for sig in order}
    severe = {sig: False for sig in order}
    for line in lines:
        sig = _signature(line)
        counts[sig] += 1
        if not severe[sig] and _is_severe(line):
            severe[sig] = True

    ranked = sorted(order, key=lambda sig: counts[sig], reverse=True)

    # Selection: top-by-frequency patterns, PLUS every severe pattern that
    # frequency-ranking alone would have excluded (rare severe patterns are
    # rare precisely because they're rare — that's not a reason to drop
    # them; see _SEVERITY_MARKERS above).
    by_frequency = ranked[:MAX_PATTERN_GROUPS]
    frequency_set = set(by_frequency)
    rare_severe = [sig for sig in ranked if severe[sig] and sig not in frequency_set]
    shown = by_frequency + rare_severe
    shown_set = set(shown)

    parts = [
        f"[This log was too large to include verbatim ({len(input_text):,} characters, "
        f"{len(lines):,} lines) and has been compacted below by pattern: near-duplicate "
        f"lines (same error, different request id/timestamp/etc) are collapsed to one or "
        f"two representative examples plus an exact occurrence count computed over the "
        f"full log — {len(order):,} distinct patterns found. Use these counts directly for "
        f"any count/percentage fields; they are exact, not estimates. Patterns marked "
        f"'SEVERE' were kept regardless of frequency because they matched a critical-"
        f"severity marker (OOM, FATAL, data loss, corruption, deadlock, etc) — treat them "
        f"as important even if their count is low.]\n"
    ]
    for sig in by_frequency:
        examples = groups[sig]
        tag = " [SEVERE]" if severe[sig] else ""
        parts.append(f"--- pattern occurs {counts[sig]:,} time(s){tag} ---")
        parts.extend(examples)
    if rare_severe:
        parts.append("--- additional rare but severe patterns (excluded from top-frequency list) ---")
        for sig in rare_severe:
            examples = groups[sig]
            parts.append(f"--- pattern occurs {counts[sig]:,} time(s) [SEVERE] ---")
            parts.extend(examples)
    omitted = [sig for sig in ranked if sig not in shown_set]
    if omitted:
        omitted_lines = sum(counts[sig] for sig in omitted)
        parts.append(
            f"--- {len(omitted):,} additional low-frequency, non-severe pattern(s) not shown "
            f"({omitted_lines:,} lines total) ---"
        )
    return "\n".join(parts)


# Phase 4 (effort escalation): ordering used to decide whether escalating
# from `effort` to the configured escalation tier is actually an increase
# (never escalate "up" to the same or a lower tier — that would either be a
# no-op retry or a silent downgrade, and either way risks a retry loop).
_EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}


def _invoke_claude(prompt: str, *, schema: dict, model: str, effort: str, max_budget: str) -> tuple[dict, dict]:
    """Runs the `claude` CLI once and returns (result, envelope). Raises on
    a CLI-level failure (nonzero exit) or an unparseable/non-dict result."""
    cmd = [
        CLAUDE_BINARY,
        "-p",
        "--output-format", "json",
        "--model", model,
        "--effort", effort,
        # --restricted refuses bypassPermissions outright (verified against
        # `claude --help`); dontAsk auto-denies anything that would prompt,
        # which is what we want given --restricted already strips the
        # tools (Bash/code execution, WebFetch) that would need approval.
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
        "--restricted",
        "--max-budget-usd", max_budget,
        "--json-schema", json.dumps(schema),
        # No positional prompt argument here on purpose: a log excerpt can
        # be multi-MB (real evidence files run into the low single-digit
        # MB range), and argv has a kernel-enforced size ceiling — putting
        # a large prompt there fails with E2BIG ("Argument list too long")
        # well before it gets anywhere near the CLI's own token limits.
        # The CLI reads the prompt from stdin when no positional prompt is
        # given, which has no such ceiling.
    ]

    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("SKILL_CLI_TIMEOUT_SECONDS", "270")),
    )
    if proc.returncode != 0:
        # A CLI-level failure (e.g. an API error like prompt_too_long) comes
        # back as a well-formed JSON envelope on stdout with is_error=true,
        # not as a bare nonzero exit with stderr text — surface that
        # envelope's message instead of an empty/useless stderr.
        detail = proc.stderr[-2000:]
        try:
            envelope = json.loads(proc.stdout)
            if isinstance(envelope, dict) and envelope.get("is_error"):
                detail = (
                    f"terminal_reason={envelope.get('terminal_reason')!r} "
                    f"api_error_status={envelope.get('api_error_status')!r} "
                    f"result={envelope.get('result')!r}"
                )
        except (json.JSONDecodeError, ValueError):
            pass
        raise RuntimeError(f"claude CLI exited {proc.returncode}: {detail}")

    envelope = json.loads(proc.stdout)
    # Cost/usage visibility (cost follow-up): the CLI's json envelope
    # carries total_cost_usd/usage/duration alongside `result`. Printed to
    # stderr unconditionally (not just on failure) so the bridge's job log
    # always has a real per-run cost figure instead of relying on the
    # $-denominated --max-budget-usd cap as the only signal.
    if isinstance(envelope, dict):
        print(
            "claude CLI usage: "
            f"model={model!r} effort={effort!r} "
            f"cost_usd={envelope.get('total_cost_usd')!r} "
            f"duration_ms={envelope.get('duration_ms')!r} "
            f"num_turns={envelope.get('num_turns')!r} "
            f"usage={envelope.get('usage')!r}",
            file=sys.stderr,
        )
    # The CLI's json output-format wraps the actual result; be defensive
    # about the exact envelope shape since this hasn't been empirically
    # verified against a live run (only reasoned from `claude --help`).
    result = envelope.get("result", envelope) if isinstance(envelope, dict) else envelope
    if isinstance(result, str):
        result = json.loads(result)

    if not isinstance(result, dict):
        raise ValueError(f"unexpected claude CLI result shape: {result!r}")

    return result, (envelope if isinstance(envelope, dict) else {})


def _escalation_reason(result: dict) -> str | None:
    """Phase 4 escalation triggers, checked against a low-effort analysis
    result. Only applies to skills whose schema carries `confidence`/
    `severity_signal` at the top level (log-triage-summary); daily-report's
    cross-incident section has no per-run confidence of its own, so it's
    never escalated here. Returns a short human-readable reason, or None if
    the low-effort result looks sufficient."""
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)):
        return "invalid structured output: missing/invalid confidence field"
    if not result.get("key_finds"):
        return "invalid structured output: empty key_finds"

    threshold = float(os.environ.get("SKILL_ESCALATION_CONFIDENCE_THRESHOLD", "0.55"))
    if confidence < threshold:
        return f"confidence {confidence:.2f} below threshold {threshold:.2f}"

    # "critical ambiguity": a critical severity call the model itself isn't
    # very sure about is exactly the case worth a second, deeper look.
    if result.get("severity_signal") == "critical" and confidence < 0.75:
        return f"critical severity_signal with borderline confidence {confidence:.2f}"

    return None


def run_skill(input_text: str, skill_name: str) -> dict:
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"skill not found: {skill_path}")
    skill_md = skill_path.read_text()

    schema = _SCHEMAS.get(skill_name)
    if schema is None:
        raise ValueError(f"no output schema registered for skill: {skill_name}")

    _, intro = _INPUT_FILE_BY_SKILL[skill_name]
    if skill_name == "log-triage-summary":
        input_text = _compact_log_if_oversized(input_text)
    prompt = (
        f"{skill_md}\n\n"
        "---\n\n"
        f"{intro}\n\n"
        f"{input_text}\n"
    )

    model = os.environ.get("SKILL_MODEL", "claude-sonnet-5")
    # Phase 4 (cost optimization): default to the cheapest effort tier for
    # every job; escalate to a higher tier only when the low-effort result
    # itself signals it isn't good enough (see _escalation_reason).
    effort = os.environ.get("SKILL_EFFORT", "low")
    escalation_effort = os.environ.get("SKILL_EFFORT_ESCALATION", "medium")
    max_budget = os.environ.get("SKILL_MAX_BUDGET_USD", "0.50")

    result, _ = _invoke_claude(prompt, schema=schema, model=model, effort=effort, max_budget=max_budget)

    if (
        skill_name == "log-triage-summary"
        and _EFFORT_RANK.get(effort, 0) < _EFFORT_RANK.get(escalation_effort, 1)
    ):
        reason = _escalation_reason(result)
        if reason:
            print(f"ai.escalated: reason={reason!r} from={effort!r} to={escalation_effort!r}", file=sys.stderr)
            # A single, bounded retry at the higher tier — never recurses,
            # so there's no possibility of an escalation loop.
            result, _ = _invoke_claude(
                prompt, schema=schema, model=model, effort=escalation_effort, max_budget=max_budget
            )

    return result


def main() -> int:
    skill_name = os.environ.get("SKILL_NAME", "log-triage-summary")
    if skill_name not in _INPUT_FILE_BY_SKILL:
        print(f"no input-file mapping registered for skill: {skill_name}", file=sys.stderr)
        return 1

    input_filename, _ = _INPUT_FILE_BY_SKILL[skill_name]
    input_file = INPUT_DIR / input_filename
    if not input_file.exists():
        print(f"missing input file: {input_file}", file=sys.stderr)
        return 1

    result = run_skill(input_file.read_text(), skill_name=skill_name)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "result.json"
    out_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        print(f"sandbox entrypoint failed: {exc}", file=sys.stderr)
        sys.exit(1)
