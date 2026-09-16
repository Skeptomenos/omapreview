"""Scope of region redaction beyond page text (OMP-04).

An authorized redact rectangle covers extractable page text **and**
intersecting annotation/widget payloads. Known types are stripped in the
same op. Unsupported intersecting types fail closed so success never leaves
a recoverable secret. Objects outside the rectangle are left intact.

Does not apply unrelated pending PDF redaction annotations (R14 / OMP-05).
"""

from __future__ import annotations

import pymupdf

from .ops import OpError

# Sticky notes, replies (usually Text), FreeText, file attachments, popups
# (dependents of notes), and form widgets. Anything else intersecting the
# authorized rectangle is refused.
_KNOWN_ANNOT_TYPES = {
    pymupdf.PDF_ANNOT_TEXT,
    pymupdf.PDF_ANNOT_FREE_TEXT,
    pymupdf.PDF_ANNOT_FILE_ATTACHMENT,
    pymupdf.PDF_ANNOT_POPUP,
    pymupdf.PDF_ANNOT_WIDGET,
}


def _as_rect(rect) -> pymupdf.Rect:
    return rect if isinstance(rect, pymupdf.Rect) else pymupdf.Rect(rect)


def intersects_any(rect, rects: list) -> bool:
    r = _as_rect(rect)
    return any(r.intersects(_as_rect(other)) for other in rects)


def _type_id(annot) -> int:
    return int(annot.type[0])


def _type_name(annot) -> str:
    return str(annot.type[1])


def unsupported_intersecting(page: pymupdf.Page, rects: list) -> list[str]:
    """Type names of intersecting annots that region redact cannot strip."""
    names: list[str] = []
    seen: set[int] = set()
    for annot in page.annots() or []:
        if _type_id(annot) == pymupdf.PDF_ANNOT_WIDGET:
            continue
        if _type_id(annot) in _KNOWN_ANNOT_TYPES:
            continue
        if annot.xref in seen:
            continue
        if intersects_any(annot.rect, rects):
            seen.add(annot.xref)
            names.append(_type_name(annot))
    return names


def fail_closed_if_unsupported(page: pymupdf.Page, rects: list, *, page_no: int) -> None:
    names = unsupported_intersecting(page, rects)
    if not names:
        return
    listed = ", ".join(sorted(set(names)))
    raise OpError(
        f"redact refused: page {page_no} has intersecting {listed} "
        "annotation(s) that region redact cannot strip. Delete those "
        "annotations first, or shrink the rectangle so it does not cover them."
    )


def _popup_xrefs(annot) -> set[int]:
    xref = getattr(annot, "popup_xref", 0) or 0
    return {xref} if xref else set()


def _thread_xrefs(page: pymupdf.Page, seed: set[int]) -> set[int]:
    """Expand to replies (IRT) and popups of every xref in the set."""
    owned = set(seed)
    changed = True
    while changed:
        changed = False
        for annot in page.annots() or []:
            extra: set[int] = set()
            if annot.xref in owned:
                extra |= _popup_xrefs(annot)
            irt = getattr(annot, "irt_xref", 0) or 0
            if irt in owned:
                extra.add(annot.xref)
                extra |= _popup_xrefs(annot)
            if extra - owned:
                owned |= extra
                changed = True
    return owned


def xrefs_to_strip(page: pymupdf.Page, rects: list) -> set[int]:
    """Xrefs of known annots in *rects*, plus their replies and popups."""
    seed: set[int] = set()
    for annot in page.annots() or []:
        kind = _type_id(annot)
        if kind == pymupdf.PDF_ANNOT_WIDGET:
            continue
        if kind not in _KNOWN_ANNOT_TYPES:
            continue
        if intersects_any(annot.rect, rects):
            seed.add(annot.xref)
            seed |= _popup_xrefs(annot)
    return _thread_xrefs(page, seed)


def _delete_annots_xrefs(page: pymupdf.Page, xrefs: set[int]) -> int:
    deleted = 0
    remaining = set(xrefs)
    while remaining:
        victim = None
        for annot in page.annots() or []:
            if annot.xref in remaining:
                victim = annot
                break
        if victim is None:
            break
        remaining.discard(victim.xref)
        page.delete_annot(victim)
        deleted += 1
    return deleted


def _delete_widgets_in_rects(page: pymupdf.Page, rects: list) -> int:
    deleted = 0
    while True:
        victim = None
        for widget in page.widgets() or []:
            if intersects_any(widget.rect, rects):
                victim = widget
                break
        if victim is None:
            break
        page.delete_widget(victim)
        deleted += 1
    return deleted


def strip_known_in_rects(page: pymupdf.Page, rects: list) -> dict:
    """Remove known in-rect annots/widgets (and note reply threads)."""
    xrefs = xrefs_to_strip(page, rects)
    n_annots = _delete_annots_xrefs(page, xrefs)
    n_widgets = _delete_widgets_in_rects(page, rects)
    return {"annotations": n_annots, "widgets": n_widgets}


def _annot_payload(annot) -> str:
    content = (annot.info or {}).get("content") or ""
    if _type_id(annot) == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
        try:
            data = annot.get_file() or b""
        except Exception:
            data = b""
        extra = data.decode("utf-8", "replace") if data else ""
        info = {}
        try:
            info = annot.file_info or {}
        except Exception:
            pass
        name = info.get("filename") or ""
        return " ".join(p for p in (content, name, extra) if p)
    return content


def _widget_payload(widget) -> str:
    value = widget.field_value
    if value is None or value is False:
        return ""
    if value is True:
        return "true"
    return str(value).strip()


def intersecting_payloads(page: pymupdf.Page, rects: list) -> list[str]:
    """Human-readable leftovers still sitting in the authorized rectangle."""
    leftovers: list[str] = []
    for annot in page.annots() or []:
        if _type_id(annot) == pymupdf.PDF_ANNOT_WIDGET:
            continue
        if not intersects_any(annot.rect, rects):
            continue
        payload = _annot_payload(annot).strip()
        leftovers.append(
            f"{_type_name(annot)}"
            + (f" {payload!r}" if payload else "")
        )
    for widget in page.widgets() or []:
        if not intersects_any(widget.rect, rects):
            continue
        payload = _widget_payload(widget)
        name = widget.field_name or ""
        leftovers.append(
            f"Widget {name!r}"
            + (f" {payload!r}" if payload else "")
        )
    return leftovers


# Word-snap rects from get_text("words") often graze the next line's glyphs
# (Times descenders, tight leading). page.get_textbox(clip) then reports the
# neighbor as leftover even when apply_redactions left it intact on purpose.
# A glyph counts as inside the authorized rectangle only when this fraction
# of its bbox area is covered — neighbors that merely clip the edge do not.
_SUBSTANTIALLY_INSIDE = 0.5
_REMNANT_SNIPPET = 48
# OCR word boxes can end on the last fully-covered pixel while the scanned
# glyph has a faint antialiased fringe just outside that box. Keep this
# margin small, and cap it at half the gap to another glyph so a nearby
# control word is never swept into a text-snap redaction.
_IMAGE_EDGE_MARGIN = 0.75


def _iter_glyphs(page: pymupdf.Page):
    """Extractable characters (or whole spans) with their PDF bboxes."""
    data = page.get_text("rawdict")
    for block in data.get("blocks", []) or []:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []) or []:
            for span in line.get("spans", []) or []:
                chars = span.get("chars") or []
                if chars:
                    for ch in chars:
                        glyph = ch.get("c") or ""
                        bbox = ch.get("bbox")
                        if not glyph or bbox is None:
                            continue
                        rect = _as_rect(bbox)
                        if rect.is_empty or not rect.is_valid:
                            continue
                        yield glyph, rect
                    continue
                text = span.get("text") or ""
                bbox = span.get("bbox")
                if not text.strip() or bbox is None:
                    continue
                rect = _as_rect(bbox)
                if rect.is_empty or not rect.is_valid:
                    continue
                yield text, rect


def _inside_fraction(glyph, clip) -> float:
    g = _as_rect(glyph)
    c = _as_rect(clip)
    if g.is_empty or not g.is_valid:
        return 0.0
    area = g.get_area()
    if area <= 0:
        return 0.0
    inter = g & c
    if inter.is_empty:
        return 0.0
    return inter.get_area() / area


def expand_rects_to_glyphs(page: pymupdf.Page, rects: list) -> list[pymupdf.Rect]:
    """Grow each authorized rect to the ink of glyphs already inside it.

    Undersized word-snap boxes miss descenders; union with those glyph bboxes
    so apply_redactions covers the ink. Image-backed pages also get a small,
    gap-capped margin for antialiased scan fringes. Does not grow to neighbors
    that only graze the edge (coverage below ``_SUBSTANTIALLY_INSIDE``).
    Keeps the original rect when no glyphs qualify so image-only regions still
    apply.
    """
    glyphs = [(ch, rect) for ch, rect in _iter_glyphs(page) if ch.strip()]
    image_backed = bool(page.get_images(full=True))
    expanded: list[pymupdf.Rect] = []
    for rect in rects:
        clip = _as_rect(rect)
        union = pymupdf.Rect(clip)
        selected: list[pymupdf.Rect] = []
        for _ch, glyph in glyphs:
            if _inside_fraction(glyph, clip) >= _SUBSTANTIALLY_INSIDE:
                union |= glyph
                selected.append(glyph)
        if image_backed and selected:
            # Limit each edge independently by the nearest other glyph whose
            # bbox overlaps that edge's perpendicular span.
            margin = [_IMAGE_EDGE_MARGIN] * 4
            for _ch, glyph in glyphs:
                if glyph in selected:
                    continue
                if glyph.y1 > union.y0 and glyph.y0 < union.y1:
                    if glyph.x1 <= union.x0:
                        margin[0] = min(margin[0], (union.x0 - glyph.x1) / 2)
                    elif glyph.x0 >= union.x1:
                        margin[2] = min(margin[2], (glyph.x0 - union.x1) / 2)
                if glyph.x1 > union.x0 and glyph.x0 < union.x1:
                    if glyph.y1 <= union.y0:
                        margin[1] = min(margin[1], (union.y0 - glyph.y1) / 2)
                    elif glyph.y0 >= union.y1:
                        margin[3] = min(margin[3], (glyph.y0 - union.y1) / 2)
            union = pymupdf.Rect(
                union.x0 - margin[0],
                union.y0 - margin[1],
                union.x1 + margin[2],
                union.y1 + margin[3],
            )
        expanded.append(union)
    return expanded


def remnants_inside_rects(page: pymupdf.Page, rects: list) -> list[dict]:
    """Extractable glyphs whose bbox is substantially inside a redact rect."""
    glyphs = [(ch, rect) for ch, rect in _iter_glyphs(page) if ch.strip()]
    remnants: list[dict] = []
    for rect in rects:
        clip = _as_rect(rect)
        bits: list[str] = []
        boxes: list[pymupdf.Rect] = []
        for ch, glyph in glyphs:
            if _inside_fraction(glyph, clip) >= _SUBSTANTIALLY_INSIDE:
                bits.append(ch)
                boxes.append(glyph)
        if not bits:
            continue
        covered = boxes[0]
        for box in boxes[1:]:
            covered |= box
        snippet = "".join(bits).replace("\n", " ")
        if len(snippet) > _REMNANT_SNIPPET:
            snippet = snippet[:_REMNANT_SNIPPET] + "…"
        remnants.append(
            {
                "snippet": snippet,
                "bbox": [covered.x0, covered.y0, covered.x1, covered.y1],
                "rect": [clip.x0, clip.y0, clip.x1, clip.y1],
            }
        )
    return remnants


def _format_remnants(remnants: list[dict]) -> str:
    parts = []
    for rem in remnants:
        bbox = ", ".join(f"{v:.2f}" for v in rem["bbox"])
        parts.append(f"{rem['snippet']!r} at [{bbox}]")
    return "leftover " + "; ".join(parts) + " still extractable inside the redact rectangle"


def leftover_text_detail(
    page: pymupdf.Page, match: str | None, rects: list
) -> str | None:
    """Why rect/match redact still has extractable text, or None if clean.

    Match ops fail if the string remains anywhere on the page. Rect ops fail
    only for glyphs substantially inside the rectangle (word-snap neighbors
    that merely clip the edge are not leftovers). Clip text with no glyph
    explanation still fails closed.
    """
    if match is not None:
        if match in page.get_text():
            return f"match {match!r} still extractable"
        return None
    remnants = remnants_inside_rects(page, rects)
    if remnants:
        return _format_remnants(remnants)
    glyphs = [(ch, rect) for ch, rect in _iter_glyphs(page) if ch.strip()]
    for rect in rects:
        clip = _as_rect(rect)
        snippet = page.get_textbox(clip).strip()
        if not snippet:
            continue
        has_inside = False
        has_graze = False
        for _ch, glyph in glyphs:
            frac = _inside_fraction(glyph, clip)
            if frac >= _SUBSTANTIALLY_INSIDE:
                has_inside = True
                break
            if frac > 0:
                has_graze = True
        if has_inside:
            continue
        if has_graze:
            continue
        shown = snippet.replace("\n", " ")
        if len(shown) > _REMNANT_SNIPPET:
            shown = shown[:_REMNANT_SNIPPET] + "…"
        return (
            f"leftover {shown!r} still extractable inside the redact "
            "rectangle (unboxed clip text)"
        )
    return None


def text_still_present(page: pymupdf.Page, match: str | None, rects: list) -> bool:
    return leftover_text_detail(page, match, rects) is not None


def verify_serialized(doc: pymupdf.Document, op: dict) -> None:
    """Fail closed if the reopened document still holds in-rect secrets."""
    page_no = int(op["page"])
    if page_no < 1 or page_no > doc.page_count:
        raise OpError(
            f"redact verify failed: page {page_no} missing after serialization"
        )
    page = doc[page_no - 1]
    rects = [pymupdf.Rect(r) for r in op["rects"]]
    match = op.get("match")
    detail = leftover_text_detail(page, match, rects)
    if detail:
        raise OpError(f"redact verify failed on page {page_no}: {detail}")
    leftovers = intersecting_payloads(page, rects)
    if leftovers:
        raise OpError(
            f"redact verify failed on page {page_no}: payloads still present "
            f"in the region after serialization: {', '.join(leftovers)}"
        )
