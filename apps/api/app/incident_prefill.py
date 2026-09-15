"""Evidence-backed Incident Prefill domain module.

OCR extracts text. This module normalizes it, applies deterministic rules,
optionally asks one local mapper about unresolved supported fields, and
rejects any model value that cannot be traced back to the OCR evidence.
"""
from __future__ import annotations

import difflib
import functools
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.local_inference import LocalInference, OllamaLocalInference

FieldQuality = Literal["HIGH", "REVIEW", "UNRESOLVED"]
FieldMethod = Literal["deterministic", "deterministic_fuzzy", "local_ai", "unresolved"]


class PrefillField(BaseModel):
    value: str | None = None
    source_text: str | None = None
    quality: FieldQuality = "UNRESOLVED"
    method: FieldMethod = "unresolved"
    ocr_confidence: float | None = None


class IncidentPrefill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: PrefillField
    service: PrefillField
    notes: PrefillField
    status: PrefillField
    triggered_at: PrefillField
    recovered_at: PrefillField
    trigger_value: PrefillField
    environment: PrefillField
    mapper_status: Literal["DETERMINISTIC", "LOCAL_AI", "FALLBACK"]
    mapper_model: str | None = None
    mapper_error: str | None = None


def normalize_ocr_text(raw_text: str | None) -> str:
    return "\n".join(" ".join(line.split()) for line in (raw_text or "").splitlines() if line.strip())


_LABEL_RE = re.compile(
    r"^\s*(?P<label>alert\s*title|title|alert|incident|service|app(?:lication)?|component|environment|env|description|details|notes|triggered|recovered|trigger\s*value|value|status|告警|标题|服务|应用|环境|描述|备注|触发|恢复)\s*[:：=\-]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_FULL_TIMESTAMP_RE = re.compile(
    r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)\b"
)
_TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\b")
_TRIGGER_VALUE_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|ms|s|K|M|G|MB|GB)\b", re.IGNORECASE)


def _lines(extraction: dict) -> list[dict]:
    raw_lines = extraction.get("lines") or []
    if raw_lines:
        return [
            {
                "text": str(line.get("text") or ""),
                "confidence": float(line.get("confidence")) if line.get("confidence") is not None else None,
                "index": index,
            }
            for index, line in enumerate(raw_lines)
            if str(line.get("text") or "").strip()
        ]
    return [
        {"text": line, "confidence": None, "index": index}
        for index, line in enumerate(str(extraction.get("raw_text") or "").splitlines())
        if line.strip()
    ]


def _field(
    value: str | None,
    source: dict | None,
    *,
    method: FieldMethod = "deterministic",
    quality: FieldQuality | None = None,
) -> PrefillField:
    if not value or source is None:
        return PrefillField()
    confidence = source.get("confidence")
    resolved_quality = quality or ("HIGH" if method == "deterministic" and (confidence is None or confidence >= 0.85) else "REVIEW")
    return PrefillField(
        value=value.strip(),
        source_text=str(source["text"]),
        quality=resolved_quality,
        method=method,
        ocr_confidence=confidence,
    )


def _unresolved(source: dict | None = None) -> PrefillField:
    return PrefillField(source_text=str(source["text"]) if source else None, ocr_confidence=source.get("confidence") if source else None)


def _labelled(lines: list[dict], labels: set[str]) -> tuple[str, dict] | None:
    for line in lines:
        match = _LABEL_RE.match(line["text"])
        if match and match.group("label").casefold().replace(" ", "") in {label.replace(" ", "") for label in labels}:
            return match.group("value").strip(), line
    return None


def _normalize_environment(value: str) -> str | None:
    normalized = re.sub(r"[^a-z]", "", value.casefold())
    return {"prod": "production", "production": "production", "staging": "staging", "stage": "staging"}.get(normalized)


def _known_service(value: str, source: dict, known_services: tuple[str, ...]) -> PrefillField:
    if not known_services:
        return _field(value, source)
    folded = value.casefold()
    for candidate in known_services:
        if folded == candidate.casefold():
            return _field(candidate, source)
    compact = re.sub(r"[^a-z0-9]", "", folded)
    for candidate in known_services:
        if compact == re.sub(r"[^a-z0-9]", "", candidate.casefold()):
            return _field(candidate, source, method="deterministic_fuzzy", quality="REVIEW")
    ranked = max(
        ((difflib.SequenceMatcher(None, folded, candidate.casefold()).ratio(), candidate) for candidate in known_services),
        default=(0.0, ""),
    )
    if ranked[0] >= 0.9:
        return _field(ranked[1], source, method="deterministic_fuzzy", quality="REVIEW")
    return PrefillField(source_text=str(source["text"]), ocr_confidence=source.get("confidence"))


def deterministic_prefill(extraction: dict, *, known_services: tuple[str, ...] = ()) -> IncidentPrefill:
    lines = _lines(extraction)
    title_match = _labelled(lines, {"alert", "alert title", "title", "incident", "告警", "标题"})
    if title_match is None:
        title_match = next(
            ((line["text"], line) for line in lines if len(line["text"].strip()) > 3 and not _LABEL_RE.match(line["text"])),
            None,
        )
    service_match = _labelled(lines, {"service", "app", "application", "component", "服务", "应用"})
    env_match = _labelled(lines, {"environment", "env", "环境"})
    notes_match = _labelled(lines, {"description", "details", "notes", "描述", "备注"})

    status: PrefillField = PrefillField()
    status_source = next((line for line in lines if re.search(r"\b(recovered|triggered|investigating)\b|恢复|触发", line["text"], re.I)), None)
    if status_source:
        lowered = status_source["text"].casefold()
        status_value = "recovered" if "recovered" in lowered or "恢复" in status_source["text"] else "investigating" if "investigating" in lowered else "open"
        status = _field(status_value, status_source)

    triggered_timestamp_lines = [
        line for line in lines if re.search(r"triggered|触发", line["text"], re.I)
    ]
    timestamp_match = next(
        ((match.group(1), line) for line in triggered_timestamp_lines if (match := _FULL_TIMESTAMP_RE.search(line["text"]))),
        None,
    )
    if timestamp_match is None:
        timestamp_match = next(
            (
                (match.group(1), line)
                for line in lines
                if not re.search(r"recovered|恢复", line["text"], re.I)
                and (match := _FULL_TIMESTAMP_RE.search(line["text"]))
            ),
            None,
        )
    triggered = PrefillField()
    if timestamp_match:
        value = timestamp_match[0].replace("/", "-")
        try:
            triggered = _field(datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(), timestamp_match[1])
        except ValueError:
            triggered = _unresolved(timestamp_match[1])
    else:
        triggered_source = next((line for line in lines if re.search(r"triggered|触发", line["text"], re.I)), None)
        if triggered_source and _TIME_RE.search(triggered_source["text"]):
            triggered = _unresolved(triggered_source)

    recovered_source = next((line for line in lines if re.search(r"recovered|恢复", line["text"], re.I)), None)
    recovered = PrefillField()
    if recovered_source:
        recovered_match = _FULL_TIMESTAMP_RE.search(recovered_source["text"])
        if recovered_match:
            recovered = _field(datetime.fromisoformat(recovered_match.group(1).replace("Z", "+00:00")).isoformat(), recovered_source)
        else:
            recovered = _unresolved(recovered_source)

    trigger_match = next(((match.group(0), line) for line in lines if (match := _TRIGGER_VALUE_RE.search(line["text"]))), None)
    environment = _field(_normalize_environment(env_match[0]), env_match[1]) if env_match and _normalize_environment(env_match[0]) else PrefillField()

    return IncidentPrefill(
        title=_field(title_match[0], title_match[1], quality="REVIEW") if title_match else PrefillField(),
        service=_known_service(service_match[0], service_match[1], known_services) if service_match else PrefillField(),
        notes=_field(notes_match[0], notes_match[1]) if notes_match else PrefillField(),
        status=status,
        triggered_at=triggered,
        recovered_at=recovered,
        trigger_value=_field(trigger_match[0], trigger_match[1]) if trigger_match else PrefillField(),
        environment=environment,
        mapper_status="DETERMINISTIC",
    )


def _value_supported(value: str, source_text: str) -> bool:
    compact_value = re.sub(r"[^\w]+", "", value.casefold())
    compact_source = re.sub(r"[^\w]+", "", source_text.casefold())
    if compact_value and compact_value in compact_source:
        return True
    value_tokens = [token for token in re.findall(r"[\w]+", value.casefold()) if len(token) > 1]
    source_tokens = set(re.findall(r"[\w]+", source_text.casefold()))
    return bool(value_tokens) and all(token in source_tokens for token in value_tokens)


def _model_value_supported(field_name: str, value: str, source_text: str, known_services: tuple[str, ...]) -> bool:
    """Allow only normalization-backed semantic aliases in addition to text matches."""
    if field_name == "service" and known_services:
        service_line = re.match(
            r"^\s*(?:service|app(?:lication)?(?:\s+name)?|component|服务|应用)\s*[:：=\-]\s*(?P<value>.+?)\s*$",
            source_text,
            re.I,
        )
        source_value = service_line.group("value") if service_line else source_text.strip()
        resolved = _known_service(source_value, {"text": source_text, "confidence": None}, known_services)
        return resolved.value is not None and resolved.value.casefold() == value.casefold()
    if _value_supported(value, source_text):
        return True
    labelled = _labelled([{"text": source_text}], {field_name, "environment", "env", "status", "service", "app", "application", "component"})
    source_value = labelled[0] if labelled else source_text
    if field_name == "environment":
        return _normalize_environment(value) is not None and _normalize_environment(value) == _normalize_environment(source_value)
    if field_name == "status":
        aliases = {"triggered": "open", "open": "open", "investigating": "investigating", "recovered": "recovered"}
        source_status = "recovered" if "recovered" in source_value.casefold() or "恢复" in source_value else "investigating" if "investigating" in source_value.casefold() else "open" if "triggered" in source_value.casefold() or "触发" in source_value else None
        return aliases.get(value.casefold()) == source_status
    return False


def _apply_model_output(prefill: IncidentPrefill, model_output: dict, extraction: dict, known_services: tuple[str, ...]) -> IncidentPrefill:
    lines = _lines(extraction)
    line_texts = {line["text"] for line in lines}
    values = prefill.model_dump()
    for field_name in ("title", "service", "notes", "status", "triggered_at", "recovered_at", "trigger_value", "environment"):
        candidate = model_output.get(field_name)
        if not isinstance(candidate, dict):
            continue
        value = candidate.get("value")
        source_text = candidate.get("source_text")
        if not isinstance(value, str) or not value.strip() or not isinstance(source_text, str) or source_text not in line_texts:
            continue
        if not _model_value_supported(field_name, value, source_text, known_services):
            continue
        if field_name == "environment":
            value = _normalize_environment(value) or ""
            if not value:
                continue
        if field_name == "status":
            value = {"triggered": "open", "open": "open", "investigating": "investigating", "recovered": "recovered"}.get(value.casefold(), "")
            if not value:
                continue
        if field_name in {"triggered_at", "recovered_at"}:
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
            except ValueError:
                continue
        if field_name == "service" and known_services:
            resolved = _known_service(value, {"text": source_text, "confidence": None}, known_services)
            if resolved.value is None:
                continue
            value = resolved.value
        values[field_name] = PrefillField(value=value, source_text=source_text, quality="REVIEW", method="local_ai").model_dump()
    values["mapper_status"] = "LOCAL_AI"
    return IncidentPrefill.model_validate(values)


def build_mapper_prompt(
    extraction: dict,
    unresolved_fields: list[str],
    known_services: tuple[str, ...],
    candidates: IncidentPrefill | None = None,
) -> str:
    allowed = ", ".join(unresolved_fields)
    services = ", ".join(known_services) if known_services else "none configured"
    candidate_lines = []
    if candidates is not None:
        for field in unresolved_fields:
            candidate = getattr(candidates, field)
            if candidate.value:
                candidate_lines.append(f"{field}: {candidate.value} (source: {candidate.source_text})")
    candidate_text = "\n".join(candidate_lines) or "none"
    return (
        "Map only unresolved Incident fields from the OCR evidence below. "
        "Return one JSON object with each requested field as {value, source_text}; use null values when unsupported. "
        "source_text must equal one complete OCR line copied exactly, including its label. "
        "Do not use a substring as source_text and do not invent facts.\n"
        f"Fields: {allowed}\nKnown services: {services}\nDeterministic candidates:\n{candidate_text}\n"
        f"OCR:\n{normalize_ocr_text(extraction.get('raw_text'))}"
    )


def build_incident_prefill(
    extraction: dict,
    *,
    inference: LocalInference | None = None,
    known_services: tuple[str, ...] = (),
) -> IncidentPrefill:
    prefill = deterministic_prefill(extraction, known_services=known_services)
    if inference is None:
        return prefill
    unresolved = [
        name for name in ("title", "service", "notes", "status", "triggered_at", "recovered_at", "trigger_value", "environment")
        if getattr(prefill, name).value is None or getattr(prefill, name).quality != "HIGH"
    ]
    if not unresolved:
        return prefill
    try:
        output = inference.complete_json(build_mapper_prompt(extraction, unresolved, known_services, prefill))
        result = _apply_model_output(prefill, output, extraction, known_services)
        result.mapper_model = inference.model
        return result
    except Exception as exc:  # local inference is an optional suggestion path
        prefill.mapper_status = "FALLBACK"
        prefill.mapper_model = getattr(inference, "model", None)
        prefill.mapper_error = str(exc)[:500]
        return prefill


@functools.lru_cache(maxsize=1)
def configured_local_inference() -> LocalInference | None:
    if not settings.local_prefill_ai_enabled or settings.local_prefill_provider != "ollama":
        return None
    return OllamaLocalInference(
        base_url=settings.local_prefill_base_url,
        model=settings.local_prefill_model,
        context_size=settings.local_prefill_context_size,
        keep_alive=settings.local_prefill_keep_alive,
        timeout_seconds=settings.local_prefill_timeout_seconds,
        max_concurrency=settings.local_prefill_max_concurrency,
        max_queue=settings.local_prefill_max_queue,
    )
