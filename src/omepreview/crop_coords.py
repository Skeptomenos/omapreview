"""Rebase markup coordinates when a page CropBox changes."""

from __future__ import annotations


def _norm_rect(x0, y0, x1, y1) -> tuple[float, float, float, float]:
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def transform_pending_for_crop(
    pending: list[dict],
    page_no: int,
    crop_rect: list[float],
) -> None:
    """Shift pending markup on ``page_no`` into cropped page space."""
    ox, oy, _, _ = _norm_rect(*crop_rect)

    def shift_xy(x: float, y: float) -> tuple[float, float]:
        return x - ox, y - oy

    for it in pending:
        if it.get("page") != page_no:
            continue
        kind = it.get("kind")
        if kind in ("highlight", "redact", "shape"):
            it["x0"], it["y0"] = shift_xy(it["x0"], it["y0"])
            it["x1"], it["y1"] = shift_xy(it["x1"], it["y1"])
        elif kind in ("sig", "text", "note"):
            it["x"], it["y"] = shift_xy(it["x"], it["y"])
            if kind == "text" and "rect" in it:
                r = it["rect"]
                it["rect"] = [r[0] - ox, r[1] - oy, r[2] - ox, r[3] - oy]
        elif kind == "ink":
            it["strokes"] = [
                [shift_xy(px, py) for px, py in stroke] for stroke in it["strokes"]
            ]
        elif kind in ("field_fill", "delete_annot"):
            r = it["rect"]
            it["rect"] = [
                r[0] - ox, r[1] - oy, r[2] - ox, r[3] - oy,
            ]
