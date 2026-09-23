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


PREPROCESSOR_VERSION = "15"
MAX_DIRECT_LOG_CHARS = 2_000_000
_TIMESTAMP = re.compile(r"\b(\d{4}-\d{2}-\d{2}T[^\s]+|\d{4}-\d{2}-\d{2}[^\s]+)")
_LEVEL = re.compile(r"\b(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\b", re.IGNORECASE)
_ENDPOINT = re.compile(r"(?:GET|POST|PUT|PATCH|DELETE)\s+([^\s?]+)")
_HTTP_STATUS = re.compile(r"\b(?:status|status_code|statusCode|http_status)\s*[=:]\s*[\"']?(\d{3})\b", re.IGNORECASE)
_TRACE = re.compile(r"\b(?:trace[_-]?id|traceId)=([A-Za-z0-9_-]+)", re.IGNORECASE)
_EXCEPTION = re.compile(r"\b[\w$]*(?:Error|Exception)\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", re.IGNORECASE)
_HEX = re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\]\)]+", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?![A-Za-z])")
_QUOTED = re.compile(r"(['\"])(?:\\.|(?!\1).)*\1")
_WHITESPACE = re.compile(r"\s+")
_SENTENCE_ENDINGS = frozenset(".!?。！？")


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


def _entries(log_text: str) -> list[tuple[str, dict | None]]:
    """Normalize JSON arrays/objects and line-oriented logs into one stream.

    NOC uploads commonly contain either JSON-lines or one JSON array. Keeping
    this normalization deterministic prevents the latter from being treated
    as one giant unstructured line and preserves the same downstream facts.
    """
    try:
        document = json.loads(log_text)
    except (json.JSONDecodeError, TypeError):
        document = None
    if isinstance(document, list):
        normalized = []
        for item in document:
            if isinstance(item, dict):
                normalized.append((json.dumps(item, ensure_ascii=False, separators=(",", ":")), item))
            else:
                normalized.append((str(item), None))
        return [(line, structured) for line, structured in normalized if line.strip()]
    if isinstance(document, dict):
        return [(json.dumps(document, ensure_ascii=False, separators=(",", ":")), document)]
    return [(line, _json_object(line)) for line in log_text.splitlines() if line.strip()]


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
        # Exported JSON logs in the NOC commonly wrap the real log line under
        # ``line`` and put transport metadata in sibling fields. Prefer the
        # line/message payload; templating the whole JSON envelope turns every
        # field into ``<value>`` and collapses unrelated failures into one
        # meaningless deterministic pattern.
        for key in ("line", "message", "msg", "error", "exception", "error_message", "detail"):
            if isinstance(structured.get(key), str) and structured[key].strip():
                message = structured[key]
                break
    value = message or line
    value = _URL.sub("<url>", value)
    value = _UUID.sub("<uuid>", value)
    value = _HEX.sub("<hex>", value)
    value = _QUOTED.sub('"<value>"', value)
    value = _TIMESTAMP.sub("<timestamp>", value)
    value = _NUMBER.sub("<n>", value)
    value = re.sub(
        r"\b(?:trace[_-]?id|traceId|span[_-]?id|spanId|request[_-]?id|requestId|"
        r"correlation[_-]?id|correlationId)\s*[=:]\s*\S+",
        "trace=<id>",
        value,
        flags=re.IGNORECASE,
    )
    value = _LEVEL.sub("<level>", value)
    return _WHITESPACE.sub(" ", value).strip()[:300] or "unclassified log entry"


def _pattern_id(template: str) -> str:
    return "p-" + hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]


def _cause_family(template: str) -> tuple[str, str]:
    """Return a conservative deterministic family for related manifestations.

    A physical log template is not necessarily an operational error type. The
    same dependency failure is often logged by several layers with different
    logger names, wrappers, or request context. These families are grouping
    candidates for the semantic runtime and for the deterministic fallback;
    they never replace the physical pattern IDs or their counts.
    """
    value = template.casefold()
    rules = (
        ("elasticsearch-timeout", ("elasticsearch", "timeout"), "Elasticsearch timeout"),
        ("elasticsearch-failure", ("elasticsearch",), "Elasticsearch failure"),
        ("ndrp-availability", ("ndrp",), "NDRP availability or response failure"),
        ("clickhouse-sql", ("clickhouse", "sql"), "ClickHouse SQL failure"),
        ("face-compare-timeout", ("facecompare", "timeout"), "Face comparison timeout"),
        ("payment-order-missing", ("payment", "does not exist"), "Payment order lookup failure"),
        ("sms-opted-out", ("sms", "optout"), "SMS recipient opted out"),
        ("sms-invalid-destination", ("sms", "invalid"), "SMS destination validation failure"),
        ("sms-command-failure", ("sms", "command_not_recognised"), "SMS provider command failure"),
    )
    for family_key, required, label in rules:
        if all(token in value for token in required):
            return family_key, label

    exceptions = tuple(dict.fromkeys(_EXCEPTION.findall(template)))
    dependency = next(
        (
            token
            for token in ("elasticsearch", "clickhouse", "kafka", "redis", "mysql", "postgres", "payment", "sms")
            if token in value
        ),
        None,
    )
    if exceptions:
        key = ":".join((dependency or "generic", *sorted(exception.casefold() for exception in exceptions)))
        label = "Related " + (dependency or "exception") + " failure: " + ", ".join(exceptions)
        return key, label
    # Keep otherwise unrelated templates separate. This fallback is still
    # stable, but it intentionally does not claim semantic equivalence.
    return "template:" + _pattern_id(template), template[:120]


def preprocess_log(log_text: str, *, max_patterns: int = 80, max_samples: int = 40) -> dict:
    """Return exact counts, stable pattern IDs, correlations, and samples."""
    entries = _entries(log_text)
    levels: Counter[str] = Counter()
    endpoints: Counter[str] = Counter()
    traces: Counter[str] = Counter()
    exceptions: Counter[str] = Counter()
    http_statuses: Counter[str] = Counter()
    user_ids: Counter[str] = Counter()
    record_ids: Counter[str] = Counter()
    timestamps: list[datetime] = []
    pattern_counts: Counter[str] = Counter()
    pattern_templates: dict[str, str] = {}
    pattern_samples: dict[str, list[str]] = defaultdict(list)
    pattern_families: dict[str, tuple[str, str]] = {}
    representative_entries: list[dict] = []
    entry_manifest: list[dict] = []

    for entry_id, (line, structured) in enumerate(entries):
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
            status_code = structured.get("status_code") or structured.get("statusCode") or structured.get("http_status") or structured.get("status")
            if isinstance(status_code, int) and 100 <= status_code <= 599:
                http_statuses[str(status_code)] += 1
            user_id = structured.get("user_id") or structured.get("userId") or structured.get("username")
            if isinstance(user_id, (str, int)):
                user_ids[str(user_id)] += 1
            record_id = structured.get("record_id") or structured.get("recordId")
            if isinstance(record_id, (str, int)):
                record_ids[str(record_id)] += 1
        endpoint_match = _ENDPOINT.search(line)
        if endpoint_match:
            endpoints[endpoint_match.group(1)] += 1
        for status_code in _HTTP_STATUS.findall(line):
            if 100 <= int(status_code) <= 599:
                http_statuses[status_code] += 1
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
        pattern_families[pattern_id] = _cause_family(template)
        entry_manifest.append({
            "id": entry_id,
            "level": level,
            "text": line[:1200],
            "pattern_id": pattern_id,
            # Keep the family identity internal to the worker. Hermes does
            # not receive this catalogue; it only selects semantic evidence
            # entries. Reconciliation uses the family to expand a selected
            # representative to every physically equivalent entry.
            "family_id": pattern_families[pattern_id][0],
        })
        if len(pattern_samples[pattern_id]) < 2:
            pattern_samples[pattern_id].append(line[:500])
        if len(representative_entries) < max_samples and (level in {"ERROR", "CRITICAL", "FATAL"} or len(representative_entries) < 8):
            representative_entries.append({"id": entry_id, "level": level, "text": line[:700]})

    ranked = sorted(pattern_counts, key=lambda item: (-pattern_counts[item], item))
    patterns = []
    for pattern_id in ranked[:max_patterns]:
        family_id, family_label = pattern_families[pattern_id]
        patterns.append({
            "id": pattern_id,
            "template": pattern_templates[pattern_id],
            "count": pattern_counts[pattern_id],
            "samples": pattern_samples[pattern_id],
            "family_id": family_id,
            "family_label": family_label,
        })
    omitted_count = sum(pattern_counts[item] for item in ranked[max_patterns:])
    if omitted_count:
        patterns.append({
            "id": "other",
            "template": "Other deterministic log patterns",
            "count": omitted_count,
            "samples": [],
            "family_id": "other",
            "family_label": "Other deterministic log patterns",
        })

    # Build complete family totals from every physical pattern, not only the
    # bounded public pattern manifest. The manifest is intentionally compact;
    # authoritative post-inference reconciliation must still see families
    # represented by low-frequency templates.
    family_members: dict[str, list[str]] = defaultdict(list)
    family_labels: dict[str, str] = {}
    for pattern_id in pattern_counts:
        family_id, family_label = pattern_families[pattern_id]
        family_members[family_id].append(pattern_id)
        family_labels[family_id] = family_label
    families = []
    for family_id, member_ids in sorted(
        family_members.items(),
        key=lambda item: (-sum(pattern_counts[pattern_id] for pattern_id in item[1]), item[0]),
    ):
        families.append({
            "id": family_id,
            "label": family_labels[family_id],
            "pattern_ids": member_ids,
            "count": sum(pattern_counts[pattern_id] for pattern_id in member_ids),
        })

    return {
        "preprocessing_version": PREPROCESSOR_VERSION,
        "total_entries": len(entries),
        "level_counts": dict(sorted(levels.items())),
        "error_count": sum(levels[level] for level in ("ERROR", "CRITICAL", "FATAL")),
        "exception_count": sum(exceptions.values()),
        "exception_counts": dict(exceptions.most_common(20)),
        "endpoint_counts": dict(endpoints.most_common(20)),
        "http_status_counts": dict(http_statuses.most_common(20)),
        "trace_counts": dict(traces.most_common(20)),
        "user_id_counts": dict(user_ids.most_common(20)),
        "record_id_counts": dict(record_ids.most_common(20)),
        "representative_entries": representative_entries,
        # This is deliberately kept out of the Hermes request. The worker
        # uses it after inference to translate semantic evidence selections
        # into authoritative counts and immutable physical identities.
        "entry_manifest": entry_manifest,
        "timestamp_start": min(timestamps).isoformat() if timestamps else None,
        "timestamp_end": max(timestamps).isoformat() if timestamps else None,
        "duration_seconds": round((max(timestamps) - min(timestamps)).total_seconds(), 3) if timestamps else None,
        "pattern_manifest": patterns,
        "pattern_families": families,
        "normalization": "json-array-or-json-lines-or-text-lines",
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


def _compact_narrative(value: str, *, max_chars: int, max_sentences: int, chinese: bool = False) -> str:
    """Bound model narrative text without changing its factual values.

    This is used only by the terminal structured-output repair path. It keeps
    complete sentences when possible so a verbose provider response cannot
    turn an otherwise safe deterministic result into a failed job.
    """
    text = value.strip()
    endings = [index + 1 for index, char in enumerate(text) if char in _SENTENCE_ENDINGS]
    if len(text) <= max_chars and len(endings) <= max_sentences:
        return text
    usable = [index for index in endings if index <= max_chars]
    if usable:
        cutoff = usable[min(max_sentences, len(usable)) - 1]
        return text[:cutoff].strip()
    bounded = text[:max_chars].rstrip()
    if bounded and bounded[-1] not in _SENTENCE_ENDINGS:
        punctuation = "。" if chinese else "."
        bounded = bounded[:-1].rstrip() + punctuation
    return bounded


def compact_log_triage_narrative(result: dict) -> dict:
    """Apply frozen narrative field limits to a final safe fallback result."""
    for field in ("summary_zh", "summary_en"):
        value = result.get(field)
        if isinstance(value, str):
            result[field] = _compact_narrative(
                value,
                max_chars=800,
                max_sentences=4,
                chinese=field.endswith("_zh"),
            )
    for group_name, max_chars, max_sentences in (("key_finds", 600, 3), ("secondary_finds", 320, 1)):
        for finding in result.get(group_name, []):
            for field in ("detail_zh", "detail_en"):
                value = finding.get(field)
                if isinstance(value, str):
                    finding[field] = _compact_narrative(
                        value,
                        max_chars=max_chars,
                        max_sentences=max_sentences,
                        chinese=field.endswith("_zh"),
                    )
    return result


def _annotated_log(statistics: dict, log_text: str) -> str:
    """Annotate normalized entries so Hermes can return semantic evidence IDs."""
    entries = statistics.get("entry_manifest") or []
    if not entries:
        return log_text
    return "\n".join(
        f"[entry_id={item['id']}] {item['text']}"
        for item in entries
    )


def _public_statistics(statistics: dict) -> dict:
    """Expose deterministic facts without exposing physical pattern IDs."""
    return {
        key: value
        for key, value in statistics.items()
        if key not in {"pattern_manifest", "pattern_families", "entry_manifest"}
    }


def build_hermes_input(
    *,
    job_id: str,
    incident: dict,
    evidence: dict,
    log_text: str,
    language_order: list[str] | None = None,
    statistics: dict | None = None,
) -> dict:
    """Build a semantic-grouping request sent by the worker to Hermes.

    The model receives normalized entries annotated with stable entry IDs and
    deterministic aggregate facts. It does not receive the application's
    physical pattern catalogue, so it can group different logger/stack-trace
    manifestations as one cause. The full deterministic statistics remain in
    the worker for post-inference reconciliation.
    """
    statistics = statistics or preprocess_log(log_text)
    annotated_log = _annotated_log(statistics, log_text)
    return {
        "schema_version": "1.0",
        "task": "log_analysis",
        "job_id": job_id,
        "incident": incident,
        "evidence": evidence,
        "statistics": _public_statistics(statistics),
        "representative_entries": statistics["representative_entries"],
        "log_excerpt": compact_log(annotated_log, max_chars=MAX_DIRECT_LOG_CHARS),
        "language_order": language_order or ["zh-CN", "en"],
    }


def _semantic_pattern_id(finding: dict) -> str:
    """Create a stable persisted identity for one model-defined cause group."""
    finding_id = str(finding.get("id") or "finding")
    digest = hashlib.sha256(f"semantic-v1:{finding_id}".encode("utf-8")).hexdigest()[:12]
    return f"semantic-{digest}"


def _reconcile_entry_grouped_result(result: dict, statistics: dict) -> dict:
    """Reconcile semantic selections against exact physical evidence.

    Hermes selects one or more representative entries for a semantic cause.
    Those selections are not counts: a model can reasonably cite one cache
    timeout while the same cause appears in several logger/stack-trace
    manifestations. Expand each selected entry through the deterministic
    family assigned during preprocessing, then calculate the authoritative
    count and evidence identity from the expanded physical entries.
    """
    entries = statistics.get("entry_manifest") or []
    entry_by_id = {item["id"]: item for item in entries}
    total_entries = statistics.get("total_entries", len(entries))
    claimed: set[int] = set()

    family_entries: dict[str, list[int]] = defaultdict(list)
    pattern_entries: dict[str, list[int]] = defaultdict(list)
    for item in entries:
        entry_id = item["id"]
        pattern_entries[item["pattern_id"]].append(entry_id)
        family_id = item.get("family_id")
        if family_id:
            family_entries[family_id].append(entry_id)

    for group_name in ("key_finds", "secondary_finds"):
        for finding in result.get(group_name, []):
            if "evidence_entry_ids" not in finding:
                continue
            raw_ids = finding.get("evidence_entry_ids")
            if not isinstance(raw_ids, list) or any(
                not isinstance(entry_id, int) or isinstance(entry_id, bool)
                for entry_id in raw_ids
            ):
                raise ValueError("evidence_entry_ids must contain integer entry IDs")
            entry_ids = list(dict.fromkeys(raw_ids))
            unknown = [entry_id for entry_id in entry_ids if entry_id not in entry_by_id]
            if unknown:
                raise ValueError(f"unknown evidence entry id: {unknown[0]}")
            overlap = claimed.intersection(entry_ids)
            if overlap:
                raise ValueError(f"evidence entry assigned to more than one finding: {min(overlap)}")

            # A selected entry is a semantic anchor. Expand to every entry in
            # its deterministic cause family. For an older snapshot without
            # family_id, retain exact physical-pattern expansion as a safe
            # backward-compatible fallback.
            expanded_ids: set[int] = set()
            for entry_id in entry_ids:
                entry = entry_by_id[entry_id]
                family_id = entry.get("family_id")
                if family_id and family_id in family_entries:
                    expanded_ids.update(family_entries[family_id])
                else:
                    expanded_ids.update(pattern_entries[entry["pattern_id"]])

            expanded_ids.difference_update(claimed)
            expanded = sorted(expanded_ids)
            claimed.update(expanded)

            if expanded:
                finding["pattern_ids"] = [_semantic_pattern_id(finding)]
                finding["evidence_entry_ids"] = expanded
                finding["count"] = len(expanded)
                finding["percentage"] = round((len(expanded) / total_entries) * 100, 2) if total_entries else 0.0
            else:
                finding["pattern_ids"] = ["unquantified"]
                finding["count"] = None
                finding["percentage"] = None

    result["total_entries"] = total_entries
    return result


def reconcile_result(
    result: dict,
    statistics: dict,
    *,
    allow_unquantified_fallback: bool = False,
) -> dict:
    """Replace model-supplied numbers with deterministic values.

    Unknown or duplicate pattern IDs are rejected by default. The worker may
    enable ``allow_unquantified_fallback`` only after the bounded Hermes
    repair attempt: an unknown model reference is then stripped from a mixed
    finding or represented as one unquantified finding. It is never treated as
    a deterministic pattern, and all exact manifest patterns remain appended
    below so evidence is not silently discarded.
    """
    if any(
        "evidence_entry_ids" in finding
        for group_name in ("key_finds", "secondary_finds")
        for finding in result.get(group_name, [])
    ):
        return _reconcile_entry_grouped_result(result, statistics)

    pattern_by_id = {item["id"]: item for item in statistics["pattern_manifest"]}
    family_by_pattern = {
        pattern_id: item.get("family_id", "template:" + pattern_id)
        for pattern_id, item in pattern_by_id.items()
        if pattern_id != "other"
    }
    family_labels = {
        item.get("family_id", "template:" + item["id"]): item.get("family_label", item["template"])
        for item in statistics["pattern_manifest"]
        if item["id"] != "other"
    }
    used: set[str] = set()
    unquantified_seen = False
    for group_name in ("key_finds", "secondary_finds"):
        normalized_findings = []
        for finding in result.get(group_name, []):
            ids = finding.get("pattern_ids") or []
            unknown_ids = [
                pattern_id
                for pattern_id in ids
                if pattern_id != "unquantified" and pattern_id not in pattern_by_id
            ]
            if unknown_ids and allow_unquantified_fallback:
                known_ids = [pattern_id for pattern_id in ids if pattern_id in pattern_by_id]
                if known_ids:
                    ids = list(dict.fromkeys(known_ids))
                    finding["pattern_ids"] = ids
                else:
                    ids = ["unquantified"]
                    finding["pattern_ids"] = ids
                    finding["count"] = None
                    finding["percentage"] = None
            elif unknown_ids:
                raise ValueError(f"unknown deterministic pattern id: {unknown_ids[0]}")

            if ids == ["unquantified"]:
                if unquantified_seen and allow_unquantified_fallback:
                    continue
                unquantified_seen = True
                finding["count"] = None
                finding["percentage"] = None

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
            normalized_findings.append(finding)
        result[group_name] = normalized_findings

    # Collapse model-created splits where several physical templates belong to
    # the same deterministic cause family. The first finding keeps its
    # semantic label/detail; all member pattern IDs and their exact counts are
    # retained. Prefer a Key Find when one exists for the same family.
    finding_items = [
        (group_name, finding)
        for group_name in ("key_finds", "secondary_finds")
        for finding in result[group_name]
        if finding.get("pattern_ids") != ["unquantified"]
    ]
    family_to_item: dict[str, tuple[str, dict]] = {}
    parent: dict[int, int] = {index: index for index in range(len(finding_items))}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for index, (_group_name, finding) in enumerate(finding_items):
        families = {
            family_by_pattern[pattern_id]
            for pattern_id in finding.get("pattern_ids", [])
            if pattern_id in family_by_pattern
        }
        for family_id in families:
            previous = family_to_item.get(family_id)
            if previous is not None:
                union(index, previous[1])
            else:
                family_to_item[family_id] = ("", index)

    components: dict[int, list[tuple[str, dict]]] = defaultdict(list)
    for index, item in enumerate(finding_items):
        components[find(index)].append(item)
    if any(len(items) > 1 for items in components.values()):
        rebuilt = {"key_finds": [], "secondary_finds": []}
        for items in components.values():
            primary_group, primary = next(
                ((group, finding) for group, finding in items if group == "key_finds"),
                items[0],
            )
            merged_ids = list(dict.fromkeys(
                pattern_id
                for _group, finding in items
                for pattern_id in finding.get("pattern_ids", [])
            ))
            primary["pattern_ids"] = merged_ids
            rebuilt[primary_group].append(primary)
        unquantified = {
            group_name: [finding for finding in result[group_name] if finding.get("pattern_ids") == ["unquantified"]]
            for group_name in ("key_finds", "secondary_finds")
        }
        result = {
            group_name: rebuilt[group_name] + unquantified[group_name]
            for group_name in ("key_finds", "secondary_finds")
        } | {key: value for key, value in result.items() if key not in {"key_finds", "secondary_finds"}}

    if allow_unquantified_fallback:
        # A provider may describe the same grouped evidence with duplicated
        # bilingual details even when it selected different physical IDs.
        # Merge those entries during the terminal repair path so validation
        # does not force a safe, count-preserving analysis into the DLQ.
        detail_owner: dict[str, dict] = {}
        detail_items: list[tuple[str, dict]] = []
        duplicate_of: dict[int, dict] = {}
        for group_name in ("key_finds", "secondary_finds"):
            for finding in result[group_name]:
                if finding.get("pattern_ids") == ["unquantified"]:
                    continue
                detail_items.append((group_name, finding))
                for field in ("detail_en", "detail_zh"):
                    value = finding.get(field)
                    signature = _compact_narrative_key(value) if isinstance(value, str) else ""
                    if len(signature) >= 32:
                        owner = detail_owner.get(signature)
                        if owner is not None and owner is not finding:
                            duplicate_of[id(finding)] = owner
                            break
                        detail_owner[signature] = finding
        if duplicate_of:
            for _group_name, finding in detail_items:
                owner = duplicate_of.get(id(finding))
                if owner is not None:
                    owner["pattern_ids"] = list(dict.fromkeys(
                        [*owner.get("pattern_ids", []), *finding.get("pattern_ids", [])]
                    ))
            result = {
                group_name: [
                    finding for finding in result[group_name] if id(finding) not in duplicate_of
                ]
                for group_name in ("key_finds", "secondary_finds")
            } | {key: value for key, value in result.items() if key not in {"key_finds", "secondary_finds"}}

    used = set()
    for group_name in ("key_finds", "secondary_finds"):
        for finding in result[group_name]:
            ids = finding.get("pattern_ids") or []
            if ids == ["unquantified"]:
                continue
            for pattern_id in ids:
                used.add(pattern_id)
            count = sum(pattern_by_id[pattern_id]["count"] for pattern_id in ids)
            finding["count"] = count
            finding["percentage"] = round((count / statistics["total_entries"]) * 100, 2) if statistics["total_entries"] else 0.0

    if allow_unquantified_fallback:
        # If the model understood a cause but failed to copy its opaque IDs,
        # attach that unquantified finding to the best deterministic family
        # instead of rendering the same cause twice (once unquantified and
        # once as an appended family). Only distinctive ASCII hints are used;
        # generic prose is deliberately not enough to claim a match.
        family_hints: dict[str, set[str]] = defaultdict(set)
        for item in statistics["pattern_manifest"]:
            if item["id"] == "other":
                continue
            family_id = item.get("family_id", "template:" + item["id"])
            source = " ".join((family_labels.get(family_id, ""), item.get("template", ""))).casefold()
            family_hints[family_id].update(
                token for token in re.findall(r"[a-z][a-z0-9_]{3,}|\b\d{3}\b", source)
                if token not in {
                    "related", "failure", "failures", "error", "entries", "pattern",
                    "timeout", "exception", "database", "query", "service", "logged",
                    "json", "http", "response", "return", "warning", "failed", "parse",
                    "callback", "external", "third", "api", "request", "line", "trace",
                }
            )
        family_used = {
            family_by_pattern[pattern_id]
            for pattern_id in used
            if pattern_id in family_by_pattern
        }
        remove_unquantified: set[int] = set()
        for group_name in ("key_finds", "secondary_finds"):
            for finding in result[group_name]:
                if finding.get("pattern_ids") != ["unquantified"]:
                    continue
                text = " ".join(
                    str(finding.get(field, ""))
                    for field in ("label_en", "detail_en", "label_zh", "detail_zh")
                ).casefold()
                preferred_families = {
                    family_id
                    for family_id, marker in (
                        ("ndrp-availability", "ndrp"),
                        ("elasticsearch-timeout", "elasticsearch"),
                        ("clickhouse-sql", "clickhouse"),
                        ("face-compare-timeout", "facecompare"),
                        ("payment-order-missing", "payment"),
                    )
                    if family_id in family_hints and marker in text
                }
                if len(preferred_families) == 1:
                    matches = list(preferred_families)
                else:
                    matches = [
                        family_id
                        for family_id, hints in family_hints.items()
                        if any(hint in text for hint in hints if len(hint) >= 4)
                    ]
                if len(matches) != 1:
                    continue
                family_id = matches[0]
                if family_id in family_used:
                    remove_unquantified.add(id(finding))
                    continue
                member_ids = [
                    item["id"]
                    for item in statistics["pattern_manifest"]
                    if item.get("family_id") == family_id and item["id"] != "other"
                ]
                if not member_ids:
                    continue
                finding["pattern_ids"] = member_ids
                count = sum(pattern_by_id[pattern_id]["count"] for pattern_id in member_ids)
                finding["count"] = count
                finding["percentage"] = round((count / statistics["total_entries"]) * 100, 2) if statistics["total_entries"] else 0.0
                used.update(member_ids)
                family_used.add(family_id)

        # If an unquantified finding matched a family already represented by
        # another finding, it was a duplicate semantic description. Remove it
        # rather than leaving a null-count duplicate in the operator report.
        for group_name in ("key_finds", "secondary_finds"):
            result[group_name] = [
                finding for finding in result[group_name]
                if id(finding) not in remove_unquantified
            ]

    omitted = [item for item in statistics["pattern_manifest"] if item["id"] not in used and item["id"] != "other"]
    omitted_by_family: dict[str, list[dict]] = defaultdict(list)
    for item in omitted:
        omitted_by_family[item.get("family_id", "template:" + item["id"])].append(item)
    existing_by_family: dict[str, dict] = {}
    for group_name in ("key_finds", "secondary_finds"):
        for finding in result[group_name]:
            for pattern_id in finding.get("pattern_ids", []):
                family_id = family_by_pattern.get(pattern_id)
                if family_id:
                    existing_by_family[family_id] = finding

    for family_id, family_items in omitted_by_family.items():
        target = existing_by_family.get(family_id)
        if target is not None:
            target["pattern_ids"] = list(dict.fromkeys(
                [*target.get("pattern_ids", []), *(item["id"] for item in family_items)]
            ))
            count = sum(pattern_by_id[pattern_id]["count"] for pattern_id in target["pattern_ids"])
            target["count"] = count
            target["percentage"] = round((count / statistics["total_entries"]) * 100, 2) if statistics["total_entries"] else 0.0
            used.update(item["id"] for item in family_items)
            continue
        item = family_items[0]
        family_pattern_ids = [member["id"] for member in family_items]
        count = sum(member["count"] for member in family_items)
        descriptor = re.sub(r"[.!?。！？]+", " ", family_labels.get(family_id, item["template"]))
        descriptor = _WHITESPACE.sub(" ", descriptor).strip()[:180].rstrip()
        result.setdefault("secondary_finds", []).append({
            "id": f"unlabeled-{family_id}",
            "label_en": family_labels.get(family_id, item["template"]),
            "label_zh": family_labels.get(family_id, item["template"]),
            "count": count,
            "percentage": round((count / statistics["total_entries"]) * 100, 2) if statistics["total_entries"] else 0.0,
            "pattern_ids": family_pattern_ids,
            "detail_en": f"Observed related log entries matching: {descriptor}.",
            "detail_zh": f"相关日志条目匹配以下模式：{descriptor}。",
        })
        used.update(family_pattern_ids)
    if allow_unquantified_fallback and not result.get("key_finds"):
        # The schema requires at least one primary finding. A provider can
        # return only an unquantified/duplicate secondary narrative after the
        # family repair; promote the first deterministic family so the result
        # remains count-preserving and operator-readable.
        if result.get("secondary_finds"):
            result["key_finds"].append(result["secondary_finds"].pop(0))
        elif statistics["pattern_manifest"]:
            item = next(
                (candidate for candidate in statistics["pattern_manifest"] if candidate["id"] != "other"),
                None,
            )
            if item is not None:
                family_id = item.get("family_id", "template:" + item["id"])
                member_ids = [
                    candidate["id"]
                    for candidate in statistics["pattern_manifest"]
                    if candidate.get("family_id") == family_id and candidate["id"] != "other"
                ]
                count = sum(pattern_by_id[pattern_id]["count"] for pattern_id in member_ids)
                result["key_finds"].append({
                    "id": f"fallback-{family_id}",
                    "label_en": family_labels.get(family_id, item["template"]),
                    "label_zh": family_labels.get(family_id, item["template"]),
                    "count": count,
                    "percentage": round((count / statistics["total_entries"]) * 100, 2) if statistics["total_entries"] else 0.0,
                    "pattern_ids": member_ids,
                    "detail_en": f"Observed the dominant deterministic family: {family_labels.get(family_id, item['template'])}.",
                    "detail_zh": f"检测到主要确定性模式族：{family_labels.get(family_id, item['template'])}。",
                })
    result["total_entries"] = statistics["total_entries"]
    return result


def _compact_narrative_key(value: str) -> str:
    """Normalize a detail only for duplicate-detection in fallback repair."""
    return re.sub(r"\W+", "", value.casefold(), flags=re.UNICODE)
