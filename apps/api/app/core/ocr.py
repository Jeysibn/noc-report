"""
OCR engine wrapper — master plan §4.6 (PaddleOCR) and §12 (OCR Review
Experience). Milestone 10 scope: run the real PaddleOCR engine
synchronously against an evidence image and store structured results;
no async job queue yet (that's Milestone 11 — RabbitMQ), so `POST
/evidence/{id}/ocr` blocks for the ~1-2s a single screenshot takes.

`enable_mkldnn=False` works around a oneDNN/PIR executor bug in this
environment's Paddle build (NotImplementedError converting a PIR double
array attribute) — CPU inference without mkldnn is a few hundred ms
slower per image but otherwise correct; revisit if a real GPU/CPU target
is chosen for deployment.
"""

import io
import re
from dataclasses import dataclass, field

from PIL import Image

_ocr_engine = None


def _get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        import paddle
        from paddleocr import PaddleOCR

        paddle.set_flags({"FLAGS_use_mkldnn": False})
        _ocr_engine = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang="en",
            enable_mkldnn=False,
        )
    return _ocr_engine


@dataclass
class OcrLine:
    text: str
    confidence: float
    bbox: list


@dataclass
class OcrResult:
    raw_text: str
    lines: list[OcrLine] = field(default_factory=list)
    fields: dict = field(default_factory=dict)


# Heuristic field extraction for the fields the OCR Review Experience (§12)
# calls out by name — "Alert title", "Triggered", "Trigger value". These are
# best-effort regexes over whatever PaddleOCR reads off a Teams/Nightingale
# screenshot, not a layout-aware parser (no fixed template exists across
# alert sources) — every extracted value stays user-editable per §12's
# explicit requirement that OCR never silently overwrites a manual edit.
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d(:[0-5]\d)?\b")
_TRIGGER_VALUE_RE = re.compile(r"\b\d+(\.\d+)?\s?(%|ms|s|K|M|G|MB|GB)\b", re.IGNORECASE)


def _extract_fields(lines: list[OcrLine]) -> dict:
    fields: dict = {}

    if lines:
        title_line = max(lines, key=lambda ln: len(ln.text))
        fields["alert_title"] = {
            "value": title_line.text,
            "confidence": title_line.confidence,
        }

    for ln in lines:
        m = _TIME_RE.search(ln.text)
        if m and "triggered_at" not in fields:
            fields["triggered_at"] = {"value": m.group(0), "confidence": ln.confidence}
        m = _TRIGGER_VALUE_RE.search(ln.text)
        if m and "trigger_value" not in fields:
            fields["trigger_value"] = {"value": m.group(0), "confidence": ln.confidence}

    return fields


def run_ocr(image_bytes: bytes) -> OcrResult:
    # PaddleOCR's predict() takes a path or ndarray; decode via PIL then
    # hand it a numpy array to avoid a temp-file round trip.
    import numpy as np

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    array = np.array(image)

    engine = _get_engine()
    outputs = list(engine.predict(array))

    lines: list[OcrLine] = []
    for output in outputs:
        res = output.json["res"]
        texts = res.get("rec_texts", [])
        scores = res.get("rec_scores", [])
        polys = res.get("rec_polys", [])
        for text, score, poly in zip(texts, scores, polys):
            lines.append(OcrLine(text=text, confidence=float(score), bbox=poly))

    raw_text = "\n".join(ln.text for ln in lines)
    return OcrResult(raw_text=raw_text, lines=lines, fields=_extract_fields(lines))
