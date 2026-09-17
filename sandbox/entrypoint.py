#!/usr/bin/env python3
"""Sandbox entrypoint: invokes the real Claude Code CLI to run a skill
against mounted input, writing a structured JSON result to the writable
output mount (master plan §27 steps 9-12).

The Milestone 0.5 / Milestone 12 deterministic stand-in has been replaced
(ADR 0003) — this now shells out to the `claude` binary bind-mounted at
/usr/local/bin/claude by the bridge, authenticated via the OAuth
credential bind-mounted at $HOME/.claude/.credentials.json (never
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
from contextvars import ContextVar

import yaml

INPUT_DIR = pathlib.Path("/input")
SKILLS_DIR = pathlib.Path("/skills")
PACKAGE_SKILLS_DIR = pathlib.Path(__file__).resolve().parents[1] / "skills"
OUTPUT_DIR = pathlib.Path("/output")
CLAUDE_BINARY = "/usr/local/bin/claude"

_AI_CALL_BUDGET: ContextVar[int | None] = ContextVar("ai_call_budget", default=None)
_AI_CALL_COUNT: ContextVar[int] = ContextVar("ai_call_count", default=0)


class PaidAIBudgetExhausted(RuntimeError):
    """The job-level paid Claude invocation budget has been consumed."""

_SCHEMA_FILENAME = "output.schema.json"


def _load_output_schema(skill_name: str) -> dict:
    """Skill Runtime mission Phase 3: the skill's own output.schema.json
    (mounted at /skills/<skill_name>/output.schema.json — either the live
    checkout, or, once the bridge materializes exact SkillSnapshot content
    there per-job, the frozen snapshot content) is now the single source
    of truth for the CLI's --json-schema enforcement. This used to be a
    hard-coded Python dict duplicating that same file, which could (and
    did) silently drift from it. A skill's output format can now change by
    editing output.schema.json alone — no sandbox code change required."""
    schema_path = SKILLS_DIR / skill_name / _SCHEMA_FILENAME
    if not schema_path.exists():
        raise ValueError(f"no output schema found for skill {skill_name!r} at {schema_path}")
    return json.loads(schema_path.read_text())


def _compact_incident_summaries(snapshot: dict) -> list[dict]:
    """AI cost-optimization mission Phase 2, Issue 6: reduces a full shift
    snapshot's incidents (which carry the ENTIRE per-incident analysis
    object, screenshots, MinIO refs, Grafana links, log filenames) down to
    just what a cross-incident reasoning step actually needs: title,
    status, severity, main error, impact, and the incident's time window.
    Everything dropped here (screenshots, links, the full analysis object)
    is still available to Python from the original snapshot and is merged
    back into the final report deterministically — Claude never needs it
    to write a shift-level overview or spot a cross-incident correlation."""
    compact = []
    for incident in snapshot.get("incidents", []):
        analysis = incident.get("analysis")
        compact.append(
            {
                "display_id": incident.get("display_id"),
                "title": incident.get("title"),
                "status": incident.get("status"),
                "severity_signal": analysis.get("severity_signal") if analysis else None,
                "main_error": analysis.get("likely_cause_en") if analysis else None,
                "impact": analysis.get("summary_en") if analysis else None,
                "starts_at": incident.get("triggered_at"),
                "ends_at": incident.get("recovered_at"),
            }
        )
    return compact

_MANIFEST_FILENAME = "skill.yaml"


def _load_manifest(skill_name: str) -> dict:
    manifest_path = SKILLS_DIR / skill_name / _MANIFEST_FILENAME
    if not manifest_path.exists():
        raise ValueError(f"no skill manifest found for skill {skill_name!r} at {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    if not isinstance(manifest, dict):
        raise ValueError(f"skill {skill_name!r} manifest must be an object")
    return manifest


def _path_value(value: object, path: str) -> object:
    """Read a dotted JSON path used by a manifest-owned projection."""
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _project_snapshot(snapshot: dict, projection: dict) -> dict:
    """Apply a declarative, skill-owned input projection.

    The generic sandbox understands only JSON paths and collection shape;
    which facts matter is declared by the selected skill manifest.
    """
    projected = {
        output_name: _path_value(snapshot, source_path)
        for output_name, source_path in (projection.get("fields") or {}).items()
    }
    for output_name, collection in (projection.get("collections") or {}).items():
        source_path = collection.get("source")
        source_items = _path_value(snapshot, source_path) if isinstance(source_path, str) else None
        if not isinstance(source_items, list):
            projected[output_name] = []
            continue
        projected[output_name] = [
            {
                field_name: _path_value(item, field_path)
                for field_name, field_path in (collection.get("fields") or {}).items()
            }
            for item in source_items
        ]
    return projected


def _load_input_contract(skill_name: str) -> tuple[str, str]:
    """Skill Runtime mission Phase 5: which /input file a skill reads, and
    the phrase introducing it in the prompt, now comes from the skill's
    own manifest (`input_contract.filename`/`input_contract.intro_text`
    in skill.yaml) instead of a second, hard-coded Python dict that used
    to duplicate — and could silently drift from — the same facts. A
    skill can change its input contract by editing skill.yaml alone."""
    manifest = _load_manifest(skill_name)
    try:
        contract = manifest["input_contract"]
        return contract["filename"], contract["intro_text"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"skill {skill_name!r} manifest is missing input_contract.filename/intro_text") from exc


def _load_execution_policy(skill_name: str) -> dict:
    path = SKILLS_DIR / skill_name / _MANIFEST_FILENAME
    # Unit-test/legacy mounts may carry an older manifest without the
    # optional execution policy.  Read the repository's contract as a
    # compatibility fallback; production snapshot mounts always include
    # their complete manifest and never consult the checkout.
    mounted_manifest_exists = path.exists()
    if not mounted_manifest_exists and SKILLS_DIR != pathlib.Path("/skills"):
        return {}
    if not mounted_manifest_exists:
        path = PACKAGE_SKILLS_DIR / skill_name / _MANIFEST_FILENAME
    if not path.exists():
        return {}
    manifest = yaml.safe_load(path.read_text()) or {}
    policy = manifest.get("execution_policy")
    if isinstance(policy, dict):
        return policy
    fallback = PACKAGE_SKILLS_DIR / skill_name / _MANIFEST_FILENAME
    if path != fallback and fallback.exists():
        legacy_manifest = yaml.safe_load(fallback.read_text()) or {}
        policy = legacy_manifest.get("execution_policy")
        if isinstance(policy, dict):
            return policy
    return {}

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

# AI cost-optimization mission Phase 2, Issue 7 fix: field-aware
# normalization. Substituted out before grouping so lines that are "the
# same error, one different request/trace/user" collapse into the same
# signature — but ONLY known-dynamic fields are touched. The previous
# version ended with a blanket `re.compile(r"\d+")` that collapsed EVERY
# digit sequence, which silently merged semantically-different errors
# into the same pattern: an HTTP 403 and an HTTP 500 became the same
# signature, as did `errorCode=1001` and `errorCode=2007` — exactly the
# kind of business-meaningful number this mission requires to be
# preserved. There is no blanket digit-collapse pattern anymore: a number
# is normalized only if it's part of one of the specific dynamic-field
# shapes below; any other number (HTTP status, business/exception error
# code, port, DB status code, JVM threshold, line number, byte count,
# etc) is left exactly as-is in the signature.
_SIG_PATTERNS = [
    # UUIDs and hex-style trace/request ids.
    re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    re.compile(r"\b[0-9a-fA-F]{16,40}\b"),
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
    re.compile(r"\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"),
    # Known-dynamic key=value / key: value identifier fields — trace/span/
    # request/correlation ids, user/record/session ids — regardless of
    # whether the value itself is numeric or alphanumeric. The field name
    # is kept (it's structural, not dynamic); only the value is collapsed.
    re.compile(
        r"\b((?:trace|span|request|correlation|session)[_-]?id)\s*[:=]\s*[\w-]+",
        re.IGNORECASE,
    ),
    re.compile(r"\b((?:user|record)[_-]?id)\s*[:=]\s*[\w-]+", re.IGNORECASE),
    # Thread/worker names carrying a dynamic numeric suffix, e.g. "Thread-42".
    re.compile(r"\b(Thread|Worker|pool)-\d+\b"),
    # Kubernetes-style pod/replicaset name suffixes, e.g.
    # "payments-7f8b9c9d75-abcde" -> "payments-#-#".
    re.compile(r"-[0-9a-f]{8,10}-[0-9a-z]{5}\b"),
]


def _signature(line: str) -> str:
    sig = line
    sig = _SIG_PATTERNS[0].sub("#", sig)
    sig = _SIG_PATTERNS[1].sub("#", sig)
    sig = _SIG_PATTERNS[2].sub("#", sig)
    sig = _SIG_PATTERNS[3].sub("#", sig)
    sig = _SIG_PATTERNS[4].sub("#", sig)
    sig = _SIG_PATTERNS[5].sub(lambda m: f"{m.group(1)}=#", sig)
    sig = _SIG_PATTERNS[6].sub(lambda m: f"{m.group(1)}=#", sig)
    sig = _SIG_PATTERNS[7].sub(lambda m: f"{m.group(1)}-#", sig)
    sig = _SIG_PATTERNS[8].sub("-#-#", sig)
    return sig


def _family_signature(line: str) -> str:
    """Return a stable operator-facing error family.

    ``_signature`` intentionally preserves a lot of detail for evidence
    grounding. That is useful for exact deduplication, but it is too narrow
    for presentation: request envelopes, logger line numbers, tokens, and
    stack-frame coordinates can turn one operational failure into hundreds
    of one-off patterns. A family removes those transport/runtime details
    while preserving the actual message, endpoint, exception class, and
    meaningful HTTP/business error codes.
    """
    family = _signature(line)
    level = re.search(r"\b(?:TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\b", family, re.IGNORECASE)
    if level:
        family = family[level.start():]
    if " -- " in family:
        family = family.split(" -- ", 1)[1]
    else:
        # Strip the logger and source line when a log formatter used a single
        # dash instead of the usual `logger:line -- message` form.
        family = re.sub(
            r"^(?:TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\s+[\w.$-]+:\d+\s+",
            "",
            family,
            flags=re.IGNORECASE,
        )
        family = re.sub(r"^-[a-z0-9]{8,20}-", "-#-", family, flags=re.IGNORECASE)

    # Values that identify one request/caller/credential rather than the
    # failure family. Keep HTTP statuses and named business error codes.
    family = re.sub(
        r"\b(?:trace|span|request|correlation|session|user|record|compare|account|phone|retry|attempt|switches|totalAttempts|partitions|taskId|orderNo|firmCode|token|rechJson)[_-]?(?:id)?\s*[:=]\s*[^,\s]+",
        lambda match: match.group(0).split("=", 1)[0].split(":", 1)[0] + "=#",
        family,
        flags=re.IGNORECASE,
    )
    family = re.sub(r"\b[0-9a-f]{8,40}\b", "#", family, flags=re.IGNORECASE)
    family = re.sub(r"\b\d{4,}\b", "#", family)
    family = re.sub(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b", "#", family)
    family = re.sub(r"\s+", " ", family).strip()
    return family


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


def _pattern_stats(
    lines: list[str],
    signature_fn=_signature,
) -> tuple[list[str], dict[str, list[str]], dict[str, int], dict[str, bool]]:
    """Skill Runtime mission Phase 15: the deterministic core shared by
    both the oversized-log compaction path and the always-on grounding
    stats appended below — one exact pass over every line, grouped by
    `_signature`, is computed exactly once regardless of which caller
    needs it. Returns (pattern order of first appearance, up-to-2
    representative example lines per pattern, exact occurrence count per
    pattern, whether any line in that pattern matched a severity marker)."""
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for line in lines:
        sig = signature_fn(line)
        if sig not in groups:
            groups[sig] = []
            order.append(sig)
        if len(groups[sig]) < 2:
            groups[sig].append(line)

    counts = {sig: 0 for sig in order}
    severe = {sig: False for sig in order}
    for line in lines:
        sig = signature_fn(line)
        counts[sig] += 1
        if not severe[sig] and _is_severe(line):
            severe[sig] = True

    return order, groups, counts, severe


def _count_manifest(input_text: str) -> tuple[int, dict[str, int], dict[str, str]]:
    """Build the deterministic numeric seam for log-triage-summary.

    Claude may decide which displayed patterns belong in a finding, but it
    must refer to these stable IDs. Counts and percentages are then derived
    from the full physical log by this process, never copied from model text.
    The reserved ``other`` bucket makes coverage explicit when the model only
    discusses the most operationally important patterns.
    """
    lines = _extract_lines(input_text)
    order, _groups, counts, severe = _pattern_stats(lines, _family_signature)
    all_signatures = _manifest_signatures(order, counts, severe)
    shown = all_signatures[:MAX_PATTERN_GROUPS]
    for sig in all_signatures[MAX_PATTERN_GROUPS:]:
        if severe[sig]:
            shown.append(sig)
    ids_by_signature = {sig: f"p{index:03d}" for index, sig in enumerate(shown, start=1)}
    counts_by_id = {pattern_id: counts[sig] for sig, pattern_id in ids_by_signature.items()}
    counts_by_id["other"] = max(0, len(lines) - sum(counts_by_id.values()))
    return len(lines), counts_by_id, ids_by_signature


def _manifest_signatures(
    order: list[str],
    counts: dict[str, int],
    severe: dict[str, bool],
) -> list[str]:
    """Return one stable ID order: prominent families first, then the rest."""
    ranked = sorted(order, key=lambda sig: counts[sig], reverse=True)
    prominent = ranked[:MAX_PATTERN_GROUPS]
    rare_severe = [sig for sig in ranked[MAX_PATTERN_GROUPS:] if severe[sig]]
    selected = prominent + rare_severe
    return selected + [sig for sig in order if sig not in selected]


def _count_manifest_text(input_text: str) -> str | None:
    lines = _extract_lines(input_text)
    if not lines:
        return None
    total, counts_by_id, ids_by_signature = _count_manifest(input_text)
    _order, _groups, counts, severe = _pattern_stats(lines, _family_signature)
    parts = [
        f"[DETERMINISTIC COUNT MANIFEST: total_entries={total:,}. "
        "For every finding, return pattern_ids from this manifest only. "
        "The runtime overwrites count and percentage from these IDs. "
        "The 'other' ID is an internal compatibility remainder only; the "
        "runtime expands omitted families into separate findings. Do not "
        "estimate any number.]"]
    by_id = {pattern_id: signature for signature, pattern_id in ids_by_signature.items()}
    for pattern_id, signature in by_id.items():
        tag = " [SEVERE]" if severe.get(signature) else ""
        parts.append(f"- id={pattern_id} occurs {counts_by_id[pattern_id]:,} time(s){tag}: {signature}")
    parts.append(f"- id=other occurs {counts_by_id['other']:,} time(s): all remaining patterns")
    return "\n".join(parts)


def _reconcile_log_triage_counts(result: dict, input_text: str) -> dict:
    """Replace model arithmetic with exact, exhaustive family accounting.

    The model may group the prominent manifest families and write their
    bilingual explanations. Every family it does not mention is added as a
    deterministic secondary finding with its normalized evidence signature.
    This keeps the useful model narrative while ensuring the operator never
    sees thousands of entries hidden behind one generic remainder bucket.
    """
    lines = _extract_lines(input_text)
    order, _groups, counts, severe = _pattern_stats(lines, _family_signature)
    all_signatures = _manifest_signatures(order, counts, severe)
    all_ids = {signature: f"p{index:03d}" for index, signature in enumerate(all_signatures, start=1)}
    counts_by_id = {pattern_id: counts[signature] for signature, pattern_id in all_ids.items()}
    total = len(lines)
    normalized = dict(result)
    normalized["total_entries"] = total
    # Keep the narrative's total aligned with the same deterministic value.
    # Numeric breakdowns belong in structured finding fields; the skill prompt
    # also tells Claude not to repeat estimated percentages in prose.
    summary_en = normalized.get("summary_en")
    if isinstance(summary_en, str):
        normalized["summary_en"] = re.sub(
            r"(?i)(?:approximately|roughly|about|~)?\s*[\d,]+\s+(?:warn/error\s+)?log entries",
            f"{total:,} log entries",
            summary_en,
        )
        normalized["summary_en"] = f"Exact log-entry total: {total:,}. {normalized['summary_en']}"
    summary_zh = normalized.get("summary_zh")
    if isinstance(summary_zh, str):
        normalized["summary_zh"] = re.sub(
            r"(?:约|大约|近)?\s*[\d,]+\s*条(?:日志(?:条目)?|WARN/ERROR日志(?:条目)?)",
            f"{total:,}条日志条目",
            summary_zh,
        )
        normalized["summary_zh"] = f"日志条目总数（确定值）：{total:,}。{normalized['summary_zh']}"
    used: set[str] = set()
    mentioned_other = False
    accounted = 0

    for group_name in ("key_finds", "secondary_finds"):
        findings = normalized.get(group_name)
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            raw_ids = finding.get("pattern_ids")
            ids = raw_ids if isinstance(raw_ids, list) else []
            valid_ids = []
            for pattern_id in ids:
                if pattern_id == "other":
                    mentioned_other = True
                    continue
                if isinstance(pattern_id, str) and pattern_id in counts_by_id and pattern_id not in used:
                    valid_ids.append(pattern_id)
                    used.add(pattern_id)
            if valid_ids:
                count = sum(counts_by_id[pattern_id] for pattern_id in valid_ids)
                finding["pattern_ids"] = valid_ids
                finding["count"] = count
                finding["percentage"] = round((count / total) * 100, 2) if total else 0.0
                accounted += count
            else:
                # A narrative finding without deterministic evidence remains
                # useful, but it must not carry an unverified number.
                # Keep this distinct from the real ``other`` remainder. The
                # latter is a quantified bucket; this marker deliberately is
                # not counted and makes the missing evidence explicit.
                finding["pattern_ids"] = ["unquantified"]
                finding["count"] = None
                finding["percentage"] = None

    secondary = normalized.setdefault("secondary_finds", [])
    # Replace the old catch-all if a model or cached preprocessor emitted it.
    secondary[:] = [
        finding
        for finding in secondary
        if not (isinstance(finding, dict) and "other" in (finding.get("pattern_ids") or []))
    ]
    for signature in all_signatures:
        pattern_id = all_ids[signature]
        if pattern_id in used:
            continue
        count = counts[signature]
        secondary.append(
            {
                "label_en": f"Deterministic error family: {signature[:180]}",
                "label_zh": f"确定性错误模式：{signature[:180]}",
                "count": count,
                "percentage": round((count / total) * 100, 2) if total else 0.0,
                "pattern_ids": [pattern_id],
                "detail_en": "Deterministic family derived from the supplied log evidence; the model did not provide a semantic label.",
                "detail_zh": "该错误模式由提供的日志证据确定性归并；模型未提供语义标签。",
            }
        )
        accounted += count
    if mentioned_other and not order:
        # Keep the result schema's non-empty finding invariant meaningful for
        # an empty/degenerate input without reviving the generic catch-all.
        accounted = total
    return normalized


def _compact_log_if_oversized(input_text: str) -> str:
    if len(input_text) <= MAX_LOG_CHARS:
        return input_text

    lines = _extract_lines(input_text)
    order, groups, counts, severe = _pattern_stats(lines)

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
    manifest = _count_manifest_text(input_text)
    if manifest:
        parts.append(manifest)
    return "\n".join(parts)


# Max distinct patterns listed in the always-on grounding appendix below —
# deliberately small (a handful of lines), since below MAX_LOG_CHARS the
# full raw log is still sent verbatim; this is only exact-count grounding
# on top of it, not a replacement for it.
MAX_GROUNDING_PATTERN_GROUPS = 15


def _deterministic_stats_appendix(input_text: str) -> str | None:
    """Skill Runtime mission Phase 15: before this, a log under
    MAX_LOG_CHARS was sent to Claude with *no* deterministic extraction at
    all — count/percentage fields in log-triage-summary's output for a
    small log were pure model estimates, while the same fields for a large
    (compacted) log were grounded in exact counts (see
    `_compact_log_if_oversized`'s own prompt text: "Use these counts
    directly ... they are exact, not estimates"). That inconsistency meant
    accuracy of a finding's count/percentage depended on log size, which
    has nothing to do with whether an exact count is knowable — it always
    is, deterministically, from the raw lines.

    This computes the same exact per-pattern counts/severity `_pattern_
    stats` gives the oversized path, as a short appendix appended after
    the (still verbatim, untruncated) raw log — never a replacement for
    it. Returns None for an empty log (nothing to ground)."""
    manifest = _count_manifest_text(input_text)
    if manifest:
        return manifest.replace("DETERMINISTIC COUNT MANIFEST", "DETERMINISTIC COUNT MANIFEST — exact, not estimates")
    return None


# Phase 4 (effort escalation): ordering used to decide whether escalating
# from `effort` to the configured escalation tier is actually an increase
# (never escalate "up" to the same or a lower tier — that would either be a
# no-op retry or a silent downgrade, and either way risks a retry loop).
_EFFORT_RANK = {"low": 0, "medium": 1, "high": 2}


# AI cost-optimization mission Phase 2, Issue 1: the minimal system prompt
# that replaces Claude Code's own default (generic coding-agent) system
# prompt for every sandboxed skill run. Before this, entrypoint.py passed
# no --system-prompt at all, so the default prompt (mentions of tools,
# coding-agent framing, etc — all irrelevant here and paid for on every
# call) was silently active in every real job. This one is short on
# purpose: the skill's own SKILL.md (sent as part of the prompt body)
# already carries the actual task instructions and output contract; this
# only needs to set identity/scope, not repeat that.
_NOC_SYSTEM_PROMPT = (
    "You are a NOC (Network Operations Center) log and incident analysis "
    "assistant. You receive log evidence or incident summaries and produce "
    "exactly one structured JSON result matching the given schema. You do "
    "not use tools, run commands, or browse; you reason only over the text "
    "given to you in this single turn."
)

# Empirically confirmed (live test calls against the installed CLI, see
# ADR 0004/0005) NOT to be added, despite being suggested by the original
# mission brief:
#   --disallowed-tools "*"   breaks --json-schema structured output: the
#       CLI implements schema validation internally via a "StructuredOutput"
#       tool call, which "*" also blocks, producing permission_denials and
#       a plain-text fallback instead of a parsed result (num_turns inflates
#       to 4 from repeated denied attempts).
#   --max-turns <n>          does not exist on the installed CLI (`claude
#       --help` has no such flag); --json-schema's internal StructuredOutput
#       tool call means a real run is never fewer than 2 turns anyway, so
#       this would have been incompatible with structured output even if
#       it existed.
# --restricted already strips Bash/code-exec/WebFetch, which is the actual
# "zero unnecessary tools" lever available here without breaking output
# parsing.


def _invoke_claude(prompt: str, *, schema: dict, model: str, effort: str, max_budget: str) -> tuple[dict, dict]:
    """Runs the `claude` CLI and returns (result, envelope).

    Phase 13 (structured-output retry/accounting): a malformed/unparseable
    structured result (`_parse_claude_result` raising `ValueError`) is
    retried exactly once at the same model/effort before giving up — a
    single bad JSON parse from the CLI shouldn't fail the whole job, but it
    also shouldn't retry indefinitely. If the retry also fails to produce a
    parseable structured result, raises a `RuntimeError` whose message
    contains "structured_output_retry_exhausted" so
    `bridge/noc_bridge/failures.py` can classify it as retryable-at-the-job-
    level (a fresh job attempt gets its own two CLI invocations) rather than
    terminal. A CLI-level failure (nonzero exit) is not retried here — that
    is a distinct, immediate `RuntimeError`."""
    last_error: ValueError | None = None
    for attempt in range(2):
        budget = _AI_CALL_BUDGET.get()
        if budget is not None and _AI_CALL_COUNT.get() >= budget:
            raise PaidAIBudgetExhausted("paid AI retry budget exhausted")
        _AI_CALL_COUNT.set(_AI_CALL_COUNT.get() + 1)
        try:
            return _invoke_claude_once(prompt, schema=schema, model=model, effort=effort, max_budget=max_budget)
        except ValueError as exc:
            last_error = exc
            print(
                f"claude CLI structured output attempt {attempt + 1}/2 failed: {exc}",
                file=sys.stderr,
            )
    raise RuntimeError(
        f"structured_output_retry_exhausted: claude CLI produced an unparseable/invalid "
        f"structured result twice in a row: {last_error}"
    ) from last_error


def _invoke_claude_once(prompt: str, *, schema: dict, model: str, effort: str, max_budget: str) -> tuple[dict, dict]:
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
        "--system-prompt", _NOC_SYSTEM_PROMPT,
        # No session/transcript persistence: each job is a fully
        # independent, one-shot analysis (Milestone 12/§27) — there is
        # nothing to resume later, and persisting one would be pure
        # overhead (disk + a stray credential-adjacent artifact) for no
        # benefit.
        "--no-session-persistence",
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
    result = _parse_claude_result(envelope, schema=schema)
    return result, (envelope if isinstance(envelope, dict) else {})


def _parse_claude_result(envelope: object, *, schema: dict) -> dict:
    """AI cost-optimization mission Phase 2, Issue 2: version-tolerant,
    robust extraction of the actual structured result from a `claude -p
    --output-format json` envelope.

    Empirically confirmed (live test calls against the installed CLI, see
    ADR 0004/0005) that a successful --json-schema call's envelope carries
    BOTH:
      - `structured_output`: already a parsed dict matching the schema
        (produced by the CLI's internal schema-validation mechanism), and
      - `result`: a JSON *string* with equivalent content.
    The previous implementation read only `envelope.get("result", envelope)`
    and never looked at `structured_output` at all — fragile, since a
    future CLI version could change how/whether `result` mirrors the
    validated structured payload, while `structured_output` is the field
    the CLI's own schema validation actually produced. Preference order,
    each with a fallback to the next on absence/parse failure:
      1. `structured_output` (already validated + already a dict)
      2. `result` (parse as JSON if it's a string; use directly if already
         a dict — some CLI versions/paths may return it unwrapped)
      3. the envelope itself (defensive fallback for a maximally-stripped
         or non-standard envelope shape)
    """
    if not isinstance(envelope, dict):
        raise ValueError(f"unexpected claude CLI envelope shape: {envelope!r}")

    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        return structured

    result = envelope.get("result", envelope)
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"claude CLI result was not valid JSON and no structured_output "
                f"was present: {result[:500]!r}"
            ) from exc

    if not isinstance(result, dict):
        raise ValueError(f"unexpected claude CLI result shape: {result!r}")

    return result


def _escalation_reason(result: dict, policy: dict | None = None) -> str | None:
    """Apply declarative quality signals from the selected skill manifest.
    Generic sandbox code does not know result field names; a skill may opt
    into escalation by declaring its own signal fields and thresholds."""
    policy = policy or _load_execution_policy("log-triage-summary")
    signals = policy.get("quality_signals") or {}
    confidence_field = signals.get("confidence_field")
    confidence = result.get(confidence_field) if confidence_field else None
    if not isinstance(confidence, (int, float)):
        return None if not confidence_field else f"invalid structured output: missing/invalid {confidence_field} field"
    list_field = signals.get("required_nonempty_list_field")
    if list_field and not result.get(list_field):
        return f"invalid structured output: empty {list_field}"

    threshold = float(os.environ.get("SKILL_ESCALATION_CONFIDENCE_THRESHOLD", signals.get("confidence_threshold", 0.55)))
    if confidence < threshold:
        return f"confidence {confidence:.2f} below threshold {threshold:.2f}"

    # "critical ambiguity": a critical severity call the model itself isn't
    # very sure about is exactly the case worth a second, deeper look.
    severity_field = signals.get("severity_field")
    critical_value = signals.get("critical_value")
    critical_threshold = float(signals.get("critical_confidence_threshold", 0.75))
    if severity_field and result.get(severity_field) == critical_value and confidence < critical_threshold:
        return f"{severity_field}={critical_value!r} with borderline confidence {confidence:.2f}"

    return None


def _envelope_telemetry(envelope: dict) -> dict:
    """Phase 1 (AI usage telemetry): the fields the CLI's json envelope
    actually offers, pulled out by name so a missing/renamed field degrades
    to null rather than blowing up telemetry capture."""
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    input_tokens = usage.get("input_tokens")
    cache_creation_tokens = usage.get("cache_creation_input_tokens")
    cache_read_tokens = usage.get("cache_read_input_tokens")
    token_parts = [value for value in (input_tokens, cache_creation_tokens, cache_read_tokens) if value is not None]
    return {
        "cost_usd": envelope.get("total_cost_usd"),
        "duration_ms": envelope.get("duration_ms"),
        "num_turns": envelope.get("num_turns"),
        "input_tokens": input_tokens,
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_model_input_tokens": sum(token_parts) if token_parts else None,
    }


def _run_skill_impl(input_text: str, skill_name: str) -> tuple[dict, dict]:
    """Returns (result, telemetry). `telemetry` is best-effort operational
    data about this run (Phase 1) — never allowed to fail the analysis
    itself; a missing sub-field is simply null."""
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"skill not found: {skill_path}")
    skill_md = skill_path.read_text()

    schema = _load_output_schema(skill_name)

    _, intro = _load_input_contract(skill_name)
    try:
        manifest = _load_manifest(skill_name)
    except ValueError:
        # Direct unit callers may provide a synthetic skill directory and
        # mock `_load_input_contract` without a manifest. The real `main()`
        # path has already required one before invoking run_skill.
        manifest = {}
    raw_input_bytes = len(input_text.encode("utf-8"))
    raw_log_input = input_text
    if skill_name == "log-triage-summary":
        if len(input_text) > MAX_LOG_CHARS:
            input_text = _compact_log_if_oversized(input_text)
        else:
            # Skill Runtime mission Phase 15: even an under-the-limit log
            # gets exact deterministic grounding now, not just an oversized
            # one — see _deterministic_stats_appendix's own docstring.
            appendix = _deterministic_stats_appendix(input_text)
            if appendix:
                input_text = f"{input_text}\n\n{appendix}"
    elif isinstance(manifest.get("input_projection"), dict):
        try:
            snapshot = json.loads(input_text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"skill {skill_name!r} input projection requires JSON input") from exc
        input_text = json.dumps(_project_snapshot(snapshot, manifest["input_projection"]), indent=2)
    elif skill_name == "daily-alert-report":
        # AI cost-optimization mission Phase 2, Issue 6: send Claude only
        # the compact per-incident summaries for pre-projection snapshots.
        # New snapshots declare this projection in skill.yaml, so the
        # changing list of report facts is skill-owned rather than encoded
        # in the generic sandbox.
        snapshot = json.loads(input_text)
        compact_snapshot = {
            "shift_starts_at": snapshot.get("shift_starts_at"),
            "shift_ends_at": snapshot.get("shift_ends_at"),
            "incidents": _compact_incident_summaries(snapshot),
        }
        input_text = json.dumps(compact_snapshot, indent=2)
    evidence_bytes = len(input_text.encode("utf-8"))
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

    result, envelope = _invoke_claude(prompt, schema=schema, model=model, effort=effort, max_budget=max_budget)
    initial = _envelope_telemetry(envelope)
    telemetry = {
        "model": model,
        "effort": effort,
        "raw_input_bytes": raw_input_bytes,
        "evidence_bytes": evidence_bytes,
        "preprocessing_ratio": (
            round(1 - (evidence_bytes / raw_input_bytes), 4) if raw_input_bytes else None
        ),
        "escalated": False,
        "escalation_reason": None,
        "attempt_count": 1,
        # AI cost-optimization mission Phase 2, Issue 4: the initial
        # (always-made) attempt's own numbers, kept separately from the
        # top-level totals below so they survive even after escalation.
        "initial_model": model,
        "initial_effort": effort,
        "initial_input_tokens": initial.get("input_tokens"),
        "initial_output_tokens": initial.get("output_tokens"),
        "initial_cache_read_tokens": initial.get("cache_read_tokens"),
        "initial_cache_creation_tokens": initial.get("cache_creation_tokens"),
        "initial_duration_ms": initial.get("duration_ms"),
        "initial_estimated_cost_usd": initial.get("cost_usd"),
        "escalation_model": None,
        "escalation_effort": None,
        "escalation_input_tokens": None,
        "escalation_output_tokens": None,
        "escalation_cache_read_tokens": None,
        "escalation_cache_creation_tokens": None,
        "escalation_duration_ms": None,
        "escalation_estimated_cost_usd": None,
        **initial,
    }
    # `**initial` above seeds the top-level (total) fields with the
    # initial attempt's own numbers; on escalation these are replaced below
    # with initial + escalation sums, so a non-escalated run's top-level
    # fields and initial_* fields end up identical (as they should — one
    # call is the only call), while an escalated run's top-level fields
    # become real totals.

    execution_policy = _load_execution_policy(skill_name)
    if _EFFORT_RANK.get(effort, 0) < _EFFORT_RANK.get(escalation_effort, 1):
        # Keep the one-argument compatibility seam for older test/tool
        # overrides while production snapshot manifests pass their
        # declarative policy explicitly.
        reason = (_escalation_reason(result, execution_policy)
                  if execution_policy else _escalation_reason(result))
        if reason:
            print(f"ai.escalated: reason={reason!r} from={effort!r} to={escalation_effort!r}", file=sys.stderr)
            # A single, bounded retry at the higher tier — never recurses,
            # so there's no possibility of an escalation loop.
            result, escalated_envelope = _invoke_claude(
                prompt, schema=schema, model=model, effort=escalation_effort, max_budget=max_budget
            )
            escalation = _envelope_telemetry(escalated_envelope)
            telemetry["escalation_model"] = model
            telemetry["escalation_effort"] = escalation_effort
            telemetry["escalation_input_tokens"] = escalation.get("input_tokens")
            telemetry["escalation_output_tokens"] = escalation.get("output_tokens")
            telemetry["escalation_cache_read_tokens"] = escalation.get("cache_read_tokens")
            telemetry["escalation_cache_creation_tokens"] = escalation.get("cache_creation_tokens")
            telemetry["escalation_duration_ms"] = escalation.get("duration_ms")
            telemetry["escalation_estimated_cost_usd"] = escalation.get("cost_usd")
            telemetry["attempt_count"] = 2
            telemetry["effort"] = escalation_effort
            telemetry["escalated"] = True
            telemetry["escalation_reason"] = reason

            # AI cost-optimization mission Phase 2, Issue 4 fix: total
            # usage is the SUM of both real calls, not just the escalated
            # call's own numbers (the previous behavior silently dropped
            # the low-effort attempt's real spend from the totals, even
            # though that attempt was real, billed usage). Sums are
            # None-safe: a missing sub-field on either side degrades the
            # total to None rather than raising or silently treating it
            # as zero.
            def _sum(*values):
                present = [v for v in values if v is not None]
                if not present:
                    return None
                return sum(present)

            telemetry["input_tokens"] = _sum(initial.get("input_tokens"), escalation.get("input_tokens"))
            telemetry["output_tokens"] = _sum(initial.get("output_tokens"), escalation.get("output_tokens"))
            telemetry["cache_read_tokens"] = _sum(
                initial.get("cache_read_tokens"), escalation.get("cache_read_tokens")
            )
            telemetry["cache_creation_tokens"] = _sum(
                initial.get("cache_creation_tokens"), escalation.get("cache_creation_tokens")
            )
            telemetry["total_model_input_tokens"] = _sum(
                initial.get("total_model_input_tokens"), escalation.get("total_model_input_tokens")
            )
            telemetry["duration_ms"] = _sum(initial.get("duration_ms"), escalation.get("duration_ms"))
            telemetry["cost_usd"] = _sum(initial.get("cost_usd"), escalation.get("cost_usd"))
            # num_turns is the escalated call's own turn count (turns
            # aren't additive in the same way spend/tokens are — it isn't
            # meaningful to report "6 turns" for two independent 2-3 turn
            # calls); the escalated call's value is kept since it's the
            # one whose result was actually used.
            telemetry["num_turns"] = escalation.get("num_turns")

    if skill_name == "log-triage-summary":
        result = _reconcile_log_triage_counts(result, raw_log_input)

    telemetry["confidence"] = result.get("confidence")
    telemetry["claude_calls"] = _AI_CALL_COUNT.get()

    return result, telemetry


def run_skill(input_text: str, skill_name: str) -> tuple[dict, dict]:
    """Run one skill under a job-level paid Claude call budget.

    The bridge reserves the budget before launching the sandbox. RabbitMQ or
    artifact retries therefore cannot reset it; a redelivery either reuses a
    completed artifact or fails without purchasing another model call.
    """
    try:
        budget = int(os.environ.get("SKILL_AI_CALL_BUDGET", "2"))
    except ValueError as exc:
        raise ValueError("SKILL_AI_CALL_BUDGET must be an integer") from exc
    budget_token = _AI_CALL_BUDGET.set(max(0, budget))
    count_token = _AI_CALL_COUNT.set(0)
    try:
        return _run_skill_impl(input_text, skill_name)
    finally:
        _AI_CALL_COUNT.reset(count_token)
        _AI_CALL_BUDGET.reset(budget_token)


def main() -> int:
    skill_name = os.environ.get("SKILL_NAME", "log-triage-summary")
    try:
        input_filename, _ = _load_input_contract(skill_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    input_file = INPUT_DIR / input_filename
    if not input_file.exists():
        print(f"missing input file: {input_file}", file=sys.stderr)
        return 1

    result, telemetry = run_skill(input_file.read_text(), skill_name=skill_name)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "result.json"
    out_path.write_text(json.dumps(result, indent=2))
    # Phase 1: telemetry is a separate file, not merged into result.json —
    # result.json must stay exactly what the skill's schema promises
    # (additionalProperties: false), so telemetry travels alongside it
    # instead of inside it. Never allowed to block a successful analysis:
    # if writing it somehow fails, that's logged, not raised.
    try:
        (OUTPUT_DIR / "telemetry.json").write_text(json.dumps(telemetry, indent=2))
    except OSError as exc:  # pragma: no cover - defensive
        print(f"failed to write telemetry.json: {exc}", file=sys.stderr)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        print(f"sandbox entrypoint failed: {exc}", file=sys.stderr)
        sys.exit(1)
