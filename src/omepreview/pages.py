"""Page-list helpers and page-range parsing for CLI/MCP."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf

from .ops import OpError

_PAGE_RANGE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_page_ranges(spec: str) -> list[int]:
    """Parse '1,4-6,9' into sorted unique 1-based page numbers."""
    if not spec or not spec.strip():
        raise OpError("page list is empty")
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = _PAGE_RANGE.match(part)
        if not m:
            raise OpError(f"invalid page range {part!r}; use forms like 1,4-6")
        start = int(m.group(1))
        end = int(m.group(2) or start)
        if end < start:
            raise OpError(f"invalid page range {part!r}: end < start")
        pages.update(range(start, end + 1))
    if not pages:
        raise OpError("page list is empty")
    return sorted(pages)


def list_pages(pdf: str | Path) -> dict:
    """Return page_count and per-page size/rotation metadata."""
    pdf = Path(pdf)
    if not pdf.is_file():
        raise FileNotFoundError(f"no such PDF: {pdf}")
    blob = pdf.read_bytes()
    doc = pymupdf.open(stream=blob, filetype="pdf")
    try:
        if doc.needs_pass:
            raise OpError(f"{pdf} is password-protected; decrypt it first")
        pages = []
        for page in doc:
            pages.append(
                {
                    "number": page.number + 1,
                    "size": [page.rect.width, page.rect.height],
                    "rotation": page.rotation,
                }
            )
        return {
            "path": str(pdf),
            "source_fingerprint": {
                "algorithm": "sha256",
                "value": hashlib.sha256(blob).hexdigest(),
                "bytes": len(blob),
            },
            "page_count": doc.page_count,
            "pages": pages,
        }
    finally:
        doc.close()
