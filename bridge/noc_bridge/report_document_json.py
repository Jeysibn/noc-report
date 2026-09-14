"""ReportDocument -> JSON, for the web Report Builder preview.

The web app and the DOCX adapter must consume the *same* semantic
`ReportDocument` produced by `report_composition.compose_report` — this
module is that second adapter (Phase: web/DOCX consistency). It never
duplicates section-ordering or evidence-resolution logic; it only walks
the already-composed, already-trusted `ReportDocument` and turns it into
plain JSON.

Screenshots are handled specially: the browser must never receive a MinIO
bucket/object_key directly (that would let a compromised/renamed image
turn into an arbitrary-object read from client-controlled JSON, and it
leaks internal storage layout for no reason). So a `Screenshot` block
serializes to `{"type": "screenshot", "index": N, "filename": ...}` in the
browser-safe payload, while the real `(bucket, object_key)` pairs are
collected, in the same index order, into a *second*, private list that
only the API server reads to proxy the actual image bytes back to an
`<img>` tag. Never send that second list to a browser.
"""
from __future__ import annotations

from typing import Any

from noc_bridge.report_document import (
    AnalysisReference,
    BilingualFindList,
    BilingualText,
    Divider,
    Find,
    Heading,
    IncidentEvidence,
    Link,
    LogFileReference,
    Metadata,
    PageBreak,
    Paragraph,
    ReportDocument,
    Screenshot,
)


def _find_to_json(find: Find) -> dict:
    return {"label": find.label, "detail": find.detail, "stat": find.stat}


def _block_to_json(block: Any, screenshots: list[dict]) -> dict:
    if isinstance(block, Heading):
        return {"type": "heading", "text": block.text, "level": block.level}
    if isinstance(block, Paragraph):
        return {"type": "paragraph", "text": block.text, "style": block.style}
    if isinstance(block, Divider):
        return {"type": "divider"}
    if isinstance(block, PageBreak):
        return {"type": "page_break"}
    if isinstance(block, Metadata):
        return {"type": "metadata", "label": block.label, "value": block.value}
    if isinstance(block, Link):
        return {"type": "link", "label": block.label, "url": block.url}
    if isinstance(block, LogFileReference):
        return {"type": "log_file_reference", "filename": block.filename}
    if isinstance(block, Screenshot):
        index = len(screenshots)
        screenshots.append({"bucket": block.bucket, "object_key": block.object_key, "filename": block.filename})
        return {"type": "screenshot", "index": index, "filename": block.filename}
    if isinstance(block, BilingualText):
        return {
            "type": "bilingual_text",
            "text_zh": block.text_zh,
            "text_en": block.text_en,
            "heading_zh": block.heading_zh,
            "heading_en": block.heading_en,
        }
    if isinstance(block, BilingualFindList):
        return {
            "type": "bilingual_find_list",
            "heading_zh": block.heading_zh,
            "heading_en": block.heading_en,
            "finds_zh": [_find_to_json(f) for f in block.finds_zh],
            "finds_en": [_find_to_json(f) for f in block.finds_en],
        }
    if isinstance(block, IncidentEvidence):
        links = list(block.links)
        if block.link:
            links = [block.link, *links]
        return {
            "type": "incident_evidence",
            "heading": block.heading,
            "incident_id": block.incident_id,
            "metadata": [_block_to_json(m, screenshots) for m in block.metadata],
            "links": [_block_to_json(item, screenshots) for item in links],
            "log_file": _block_to_json(block.log_file, screenshots) if block.log_file else None,
            "screenshots": [_block_to_json(s, screenshots) for s in block.screenshots],
        }
    if isinstance(block, AnalysisReference):
        return {
            "type": "analysis_reference",
            "heading": block.heading,
            "available": block.available,
            "analysis_run_id": block.analysis_run_id,
            "unavailable_text": block.unavailable_text,
            "metadata": [_block_to_json(m, screenshots) for m in block.metadata],
            "children": [_block_to_json(c, screenshots) for c in block.children],
            "summary": _block_to_json(block.summary, screenshots) if block.summary else None,
            "key_finds": _block_to_json(block.key_finds, screenshots) if block.key_finds else None,
            "secondary_finds": _block_to_json(block.secondary_finds, screenshots) if block.secondary_finds else None,
            "likely_cause": _block_to_json(block.likely_cause, screenshots) if block.likely_cause else None,
            "recommended_action": (
                _block_to_json(block.recommended_action, screenshots) if block.recommended_action else None
            ),
        }
    raise ValueError(f"report_document_json: unknown block type {type(block)!r}")


def document_to_preview_json(document: ReportDocument) -> tuple[dict, list[dict]]:
    """Return (browser_safe_payload, screenshot_index).

    `browser_safe_payload` is plain JSON mirroring the same section
    structure the DOCX adapter renders (title/metadata/blocks, each block
    tagged with a "type"). `screenshot_index` is a private, ordered list of
    `{bucket, object_key, filename}` — index N in that list is the object
    referenced by `{"type": "screenshot", "index": N, ...}` in the payload.
    """
    screenshots: list[dict] = []
    blocks = [_block_to_json(block, screenshots) for block in document.blocks]
    metadata = [_block_to_json(item, screenshots) for item in document.metadata]
    payload = {"title": document.title, "metadata": metadata, "blocks": blocks}
    return payload, screenshots
