"""Redaction save/share helpers (no GTK).

GUI Save and Share go through these so a copy-export cannot attach the
unredacted original, cannot clobber an existing ``*_redacted.pdf``, and
commits the displayed rectangle rather than re-searching the page.
"""

from __future__ import annotations

from pathlib import Path


def unused_sibling(path: str | Path, extra: str) -> Path:
    """Return ``stem{extra}.pdf`` that does not yet exist.

    If ``doc_redacted.pdf`` is taken, try ``doc_redacted-2.pdf``, then
    ``-3``, and so on. Never returns a path that already exists.
    """
    src = Path(path)
    suffix = src.suffix or ".pdf"
    dest = src.with_name(f"{src.stem}{extra}{suffix}")
    if not dest.exists():
        return dest
    n = 2
    while True:
        cand = src.with_name(f"{src.stem}{extra}-{n}{suffix}")
        if not cand.exists():
            return cand
        n += 1


def share_target_path(
    active_path: str | Path,
    *,
    flatten: bool,
    flatten_fn=None,
) -> Path:
    """File Share should attach: the active document, or a unique flatten copy.

    *active_path* is whatever the editor currently has open (the redacted
    copy after a save-as-copy). Flatten writes ``stem-final.pdf`` only if
    that name is free; otherwise ``stem-final-2.pdf``, etc.
    """
    src = Path(active_path)
    if not flatten:
        return src
    if flatten_fn is None:
        raise TypeError("flatten_fn is required when flatten=True")
    dest = unused_sibling(src, "-final")
    flatten_fn(str(src), output=str(dest))
    return dest


def redact_item_to_op(item: dict) -> dict:
    """Commit the ghost's displayed rectangle — never a fresh ``search_for``.

    A ``match`` key on the ghost is metadata only (the word that was
    snapped). Re-running search would redact every occurrence.
    """
    x0 = min(float(item["x0"]), float(item["x1"]))
    y0 = min(float(item["y0"]), float(item["y1"]))
    x1 = max(float(item["x0"]), float(item["x1"]))
    y1 = max(float(item["y0"]), float(item["y1"]))
    return {
        "op": "redact",
        "page": int(item["page"]) + 1,
        "rect": [x0, y0, x1, y1],
        "fill": list(item.get("fill", [0, 0, 0])),
        **(
            {"apply_now": item["apply_now"]}
            if "apply_now" in item
            else {}
        ),
    }


def match_redact_ghosts(
    page_index: int,
    _match: str,
    rects,
    *,
    fill: list[float] | None = None,
    apply_now: bool | None = None,
) -> list[dict]:
    """One ghost per resolved rectangle so an all-occurrences proposal is reviewable."""
    items = []
    for r in rects:
        item = {
            "kind": "redact",
            "page": page_index,
            "x0": float(r.x0),
            "y0": float(r.y0),
            "x1": float(r.x1),
            "y1": float(r.y1),
        }
        if fill is not None:
            item["fill"] = list(fill)
        if apply_now is not None:
            item["apply_now"] = apply_now
        items.append(item)
    return items
