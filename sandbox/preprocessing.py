"""Deterministic evidence preparation for semantic log analysis.

The worker owns this code path. It produces authoritative counts and a
bounded, representative context package; Hermes is only asked to label and
explain those facts. Nothing in this module calls a model or a provider.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime


PREPROCESSOR_VERSION = "6"
_TIMESTAMP = re.compile(r"\b(\d{4}-\d{2}-\d{2}T[^\s]+|\d{4}-\d{2}-\d{2}[^\s]+)")
_LEVEL = re.compile(r"\b(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\b", re.IGNORECASE)
_ENDPOINT = re.compile(r"(?:GET|POST|PUT|PATCH|DELETE)\s+([^\s?]+)")
_TRACE = re.compile(r"\b(?:trace[_-]?id|traceId)=([A-Za-z0-9_-]+)", re.IGNORECASE)
_EXCEPTION = re.compile(r"\b[\w$]*(?:Error|Exception)\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", re.IGNORECASE)
_HEX = re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\]\)]+", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?![A-Za-z])")
_QUOTED = re.compile(r"(['\"])(?:\\.|(?!\1).)*\1")
_WHITESPACE = re.compile(r"\s+")


def _timestamp(value: str | None) -> str | None:
    match = _TIMESTAMP.search(value or "")
    return match.group(1) if match else None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_object(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _level(line: str, structured: dict | None) -> str | None:
    value = (structured or {}).get("level") or (structured or {}).get("severity")
    if not isinstance(value, str):
        match = _LEVEL.search(line)
        value = match.group(1) if match else None
    if not isinstance(value, str):
        return None
    value = value.upper()
    return "WARN" if value == "WARNING" else value


def _template(line: str, structured: dict | None) -> str:
    """Normalize request-specific values without removing technical causes."""
    message = None
    if structured:
        for key in ("message", "msg", "error", "exception", "error_message", "detail"):
            if isinstance(structured.get(key), str) and structured[key].strip():
                message = structured[key]
                break
    value = message or line
    value = _URL.sub("<url>", value)
    value = _UUID.sub("<uuid>", value)
    value = _HEX.sub("<hex>", value)
    value = _QUOTED.sub('"<value>"', value)
    value = _NUMBER.sub("<n>", value)
    value = re.sub(r"\b(?:trace[_-]?id|traceId|request[_-]?id|requestId|correlation[_-]?id)\s*[=:]\s*\S+", "trace=<id>", value, flags=re.IGNORECASE)
    value = _TIMESTAMP.sub("<timestamp>", value)
    value = _LEVEL.sub("<level>", value)
    return _WHITESPACE.sub(" ", value).strip()[:300] or "unclassified log entry"


def _pattern_id(template: str) -> str:
    return "p-" + hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]


def preprocess_log(log_text: str, *, max_patterns: int = 80, max_samples: int = 40) -> dict:
    """Return exact counts, stable pattern IDs, correlations, and samples."""
    lines = [line for line in log_text.splitlines() if line.strip()]
    levels: Counter[str] = Counter()
    endpoints: Counter[str] = Counter()
    traces: Counter[str] = Counter()
    exceptions: Counter[str] = Counter()
    timestamps: list[datetime] = []
    pattern_counts: Counter[str] = Counter()
    pattern_templates: dict[str, str] = {}
    pattern_samples: dict[str, list[str]] = defaultdict(list)
    representative_entries: list[dict] = []

    for line in lines:
        structured = _json_object(line)
        level = _level(line, structured)
        if level:
            levels[level] += 1
        if structured:
            for key in ("timestamp", "time", "ts"):
                parsed = _parse_timestamp(structured.get(key)) if isinstance(structured.get(key), str) else None
                if parsed:
                    timestamps.append(parsed)
                    break
            endpoint = structured.get("path") or structured.get("endpoint")
            if isinstance(endpoint, str):
                endpoints[endpoint] += 1
            trace_id = structured.get("trace_id") or structured.get("traceId")
            if isinstance(trace_id, str):
                traces[trace_id] += 1
        endpoint_match = _ENDPOINT.search(line)
        if endpoint_match:
            endpoints[endpoint_match.group(1)] += 1
        for trace in _TRACE.findall(line):
            traces[trace] += 1
        for exception in _EXCEPTION.findall(line):
            exceptions[exception] += 1
        parsed = _parse_timestamp(_timestamp(line))
        if parsed:
            timestamps.append(parsed)

        template = _template(line, structured)
        pattern_id = _pattern_id(template)
        pattern_counts[pattern_id] += 1
        pattern_templates[pattern_id] = template
        if len(pattern_samples[pattern_id]) < 2:
            pattern_samples[pattern_id].append(line[:500])
        if len(representative_entries) < max_samples and (level in {"ERROR", "CRITICAL", "FATAL"} or len(representative_entries) < 8):
            representative_entries.append({"level": level, "text": line[:700]})

    ranked = sorted(pattern_counts, key=lambda item: (-pattern_counts[item], item))
    patterns = []
    for pattern_id in ranked[:max_patterns]:
        patterns.append({
            "id": pattern_id,
            "template": pattern_templates[pattern_id],
            "count": pattern_counts[pattern_id],
            "samples": pattern_samples[pattern_id],
        })
    omitted_count = sum(pattern_counts[item] for item in ranked[max_patterns:])
    if omitted_count:
        patterns.append({
            "id": "other",
            "template": "Other deterministic log patterns",
            "count": omitted_count,
            "samples": [],
        })

    return {
        "preprocessing_version": PREPROCESSOR_VERSION,
        "total_entries": len(lines),
        "level_counts": dict(sorted(levels.items())),
        "error_count": sum(levels[level] for level in ("ERROR", "CRITICAL", "FATAL")),
        "exception_count": sum(exceptions.values()),
        "exception_counts": dict(exceptions.most_common(20)),
        "endpoint_counts": dict(endpoints.most_common(20)),
        "trace_counts": dict(traces.most_common(20)),
        "representative_entries": representative_entries,
        "timestamp_start": min(timestamps).isoformat() if timestamps else None,
        "timestamp_end": max(timestamps).isoformat() if timestamps else None,
        "pattern_manifest": patterns,
    }


def compact_log(log_text: str, *, max_chars: int = 80_000) -> str:
    """Bound raw context while retaining a deterministic omission marker."""
    if len(log_text) <= max_chars:
        return log_text
    omitted = len(log_text) - max_chars
    marker = f"[deterministic preprocessing omitted {omitted} bytes; see representative_entries]"
    if len(marker) >= max_chars:
        return marker[:max_chars]
    available = max_chars - len(marker) - 2
    head = available // 2
    tail = available - head
    return "\n".join([log_text[:head], marker, log_text[-tail:]])


def build_hermes_input(*, job_id: str, incident: dict, evidence: dict, log_text: str, language_order: list[str] | None = None) -> dict:
    """Build the provider-neutral request sent by the worker to Hermes."""
    statistics = preprocess_log(log_text)
    return {
        "schema_version": "1.0",
        "task": "log_analysis",
        "job_id": job_id,
        "incident": incident,
        "evidence": evidence,
        "statistics": statistics,
        "representative_entries": statistics["representative_entries"],
        "log_excerpt": compact_log(log_text),
        "language_order": language_order or ["zh-CN", "en"],
    }


def reconcile_result(result: dict, statistics: dict) -> dict:
    """Replace model-supplied numbers with deterministic values.

    Unknown or duplicate pattern IDs are rejected by the worker validator.
    Patterns the model omitted are retained as concise Secondary Finds so no
    deterministic evidence silently disappears from the persisted result.
    """
    pattern_by_id = {item["id"]: item for item in statistics["pattern_manifest"]}
    used: set[str] = set()
    for group_name in ("key_finds", "secondary_finds"):
        for finding in result.get(group_name, []):
            ids = finding.get("pattern_ids") or []
            for pattern_id in ids:
                if pattern_id == "unquantified":
                    continue
                if pattern_id not in pattern_by_id:
                    raise ValueError(f"unknown deterministic pattern id: {pattern_id}")
                if pattern_id in used:
                    raise ValueError(f"deterministic pattern assigned more than once: {pattern_id}")
                used.add(pattern_id)
            if ids != ["unquantified"]:
                count = sum(pattern_by_id[item]["count"] for item in ids)
                finding["count"] = count
                finding["percentage"] = round((count / statistics["total_entries"]) * 100, 2) if statistics["total_entries"] else 0.0

    omitted = [item for item in statistics["pattern_manifest"] if item["id"] not in used and item["id"] != "other"]
    for item in omitted:
        result.setdefault("secondary_finds", []).append({
            "id": f"unlabeled-{item['id']}",
            "label_en": item["template"],
            "label_zh": item["template"],
            "count": item["count"],
            "percentage": round((item["count"] / statistics["total_entries"]) * 100, 2) if statistics["total_entries"] else 0.0,
            "pattern_ids": [item["id"]],
            "detail_en": "This deterministic pattern was present in the evidence but was not given a separate semantic label by the runtime.",
            "detail_zh": "该确定性模式出现在证据中，但运行时未为其提供单独的语义标签。",
        })
    result["total_entries"] = statistics["total_entries"]
    return result
