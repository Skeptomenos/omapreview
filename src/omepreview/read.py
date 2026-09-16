"""Read a PDF into an agent-friendly structure.

Everything an agent needs to decide *where* to act: page sizes, text blocks
with bounding boxes, form fields, and existing annotations. Coordinates are
PDF points, origin top-left — the same system the ops take, so a bbox from
here can be passed straight back as an op target.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf

_FIELD_TYPES = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: "text",
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "combobox",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "listbox",
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "signature",
    pymupdf.PDF_WIDGET_TYPE_BUTTON: "button",
}


def _fingerprint(blob: bytes) -> dict[str, str | int]:
    return {
        "algorithm": "sha256",
        "value": hashlib.sha256(blob).hexdigest(),
        "bytes": len(blob),
    }


def _fields(page: pymupdf.Page) -> list[dict]:
    fields = []
    for w in page.widgets():
        if not w.field_name:
            continue
        fields.append(
            {
                "name": w.field_name,
                "type": _FIELD_TYPES.get(w.field_type, "other"),
                "value": w.field_value,
                "rect": list(w.rect),
            }
        )
    return fields


def _annotations(page: pymupdf.Page) -> list[dict]:
    annots = []
    for index, a in enumerate(page.annots() or []):
        annots.append(
            {
                "index": index,
                "type": a.type[1],
                "rect": list(a.rect),
                "content": a.info.get("content", ""),
            }
        )
    return annots


def extract(
    pdf: str | Path,
    pages: list[int] | None = None,
    text_only: bool = False,
) -> dict:
    """Structured document read. `pages` filters to 1-based page numbers."""
    pdf = Path(pdf)
    if not pdf.is_file():
        raise FileNotFoundError(f"no such PDF: {pdf}")
    blob = pdf.read_bytes()
    doc = pymupdf.open(stream=blob, filetype="pdf")
    try:
        if doc.needs_pass:
            return {
                "path": str(pdf),
                "error": "password-protected",
                "source_fingerprint": _fingerprint(blob),
            }
        result = {
            "path": str(pdf),
            "source_fingerprint": _fingerprint(blob),
            "page_count": doc.page_count,
            "title": doc.metadata.get("title") or "",
            "has_form": bool(doc.is_form_pdf),
            "pages": [],
        }
        wanted = set(pages) if pages else None
        for page in doc:
            number = page.number + 1
            if wanted and number not in wanted:
                continue
            entry = {
                "number": number,
                "size": [page.rect.width, page.rect.height],
            }
            if text_only:
                entry["text"] = page.get_text("text")
            else:
                entry["text_blocks"] = [
                    {"bbox": [x0, y0, x1, y1], "text": text.strip()}
                    for x0, y0, x1, y1, text, *_ in page.get_text("blocks")
                    if text.strip()
                ]
                entry["form_fields"] = _fields(page)
                entry["annotations"] = _annotations(page)
            result["pages"].append(entry)
        return result
    finally:
        doc.close()


def form_fields(pdf: str | Path) -> list[dict]:
    """Flat field list with page and widget-rectangle selectors.

    The ``(name, page, rect)`` values can be passed to ``fill_field`` when a
    PDF repeats a field name. A name-only fill is accepted only for one match.
    """
    doc = pymupdf.open(str(Path(pdf)))
    try:
        out = []
        for page in doc:
            for f in _fields(page):
                out.append({**f, "page": page.number + 1})
        return out
    finally:
        doc.close()
