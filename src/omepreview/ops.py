"""The operation schema — the single vocabulary shared by every client.

An operation is a plain dict (JSON-friendly). A document edit is a list of
operations. The GUI, the CLI, and the MCP server all reduce to building one
of these lists and handing it to engine.apply().

Op reference (see docs/ops.md for the full spec):

  {"op": "highlight",  "page": 1, "match": "termination clause"}
  {"op": "highlight",  "page": 1, "rect": [x0, y0, x1, y1], "style": "underline"}
  {"op": "note",       "page": 2, "at": [x, y], "text": "Check this figure"}
  {"op": "text_box",   "page": 2, "rect": [x0, y0, x1, y1], "text": "N/A", "size": 11}
  {"op": "fill_field", "field": "tenant_name", "value": "Peter Bergin"}
  {"op": "place_signature", "page": 4, "at": [x, y], "width": 180,
   "signature": "default", "date": true}

Pages are 1-based everywhere a human or agent sees them. Coordinates are PDF
points (1/72 inch) with the origin at the TOP-LEFT of the page, matching what
`omepreview read` reports.
"""

from __future__ import annotations

import math

MARKUP_STYLES = ("highlight", "underline", "strikeout", "squiggly")
SHAPE_TYPES = ("line", "arrow", "rect", "oval")

OP_TYPES = (
    "highlight",
    "note",
    "text_box",
    "fill_field",
    "place_signature",
    "ink",
    "rotate_pages",
    "delete_pages",
    "move_pages",
    "insert_pages",
    "extract_pages",
    "redact",
    "delete_annotation",
    "shape",
    "crop_pages",
)

_ROTATE_DEGREES = (90, 180, 270, -90)

# Keep malformed or adversarial JSON from reaching PyMuPDF with values that
# can fail late, allocate unbounded resources, or create non-terminating
# geometry loops. These limits are intentionally generous for normal PDFs.
MAX_COORDINATE = 1_000_000.0
MAX_DIMENSION = 100_000.0
MAX_FONT_SIZE = 1_000.0
MAX_STROKE_WIDTH = 1_000.0
MAX_SIGNATURE_WIDTH = 10_000.0


class OpError(ValueError):
    """An operation failed validation or could not be applied."""


def _require(op: dict, key: str):
    if key not in op:
        raise OpError(f"op '{op.get('op')}' missing required key '{key}': {op}")
    return op[key]


def _number(
    value,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OpError(f"{label} must be a finite number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise OpError(f"{label} must be finite, got {value!r}")
    if minimum is not None and (
        number <= minimum if strict_minimum else number < minimum
    ):
        relation = "greater than" if strict_minimum else "at least"
        raise OpError(f"{label} must be {relation} {minimum}, got {value!r}")
    if maximum is not None and number > maximum:
        raise OpError(f"{label} must be at most {maximum}, got {value!r}")
    return number


def _boolean(value, label: str) -> bool:
    if not isinstance(value, bool):
        raise OpError(f"{label} must be a JSON boolean, got {value!r}")
    return value


def _rect(value) -> list[float]:
    if not (isinstance(value, (list, tuple)) and len(value) == 4):
        raise OpError(f"rect must be [x0, y0, x1, y1], got {value!r}")
    return [
        _number(v, f"rect coordinate {i}", minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE)
        for i, v in enumerate(value)
    ]


def _point(value) -> list[float]:
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        raise OpError(f"point must be [x, y], got {value!r}")
    return [
        _number(v, f"point coordinate {i}", minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE)
        for i, v in enumerate(value)
    ]


def _page_list(value, key: str = "pages") -> list[int]:
    if not (isinstance(value, list) and value):
        raise OpError(f"{key} must be a non-empty list of 1-based page numbers")
    out = []
    for p in value:
        if not (isinstance(p, int) and not isinstance(p, bool) and p >= 1):
            raise OpError(f"{key} entries must be 1-based integers, got {p!r}")
        out.append(p)
    return out


def validate(op: dict) -> dict:
    """Validate one op, returning a normalized copy. Raises OpError."""
    if not isinstance(op, dict):
        raise OpError(f"each op must be an object, got {type(op).__name__}")
    kind = op.get("op")
    if kind not in OP_TYPES:
        raise OpError(f"unknown op {kind!r}; valid ops: {', '.join(OP_TYPES)}")
    out = dict(op)

    if kind == "highlight":
        _require(op, "page")
        if ("match" in op) == ("rect" in op):
            raise OpError("highlight needs exactly one of 'match' or 'rect'")
        if "rect" in op:
            out["rect"] = _rect(op["rect"])
        style = op.get("style", "highlight")
        if style not in MARKUP_STYLES:
            raise OpError(f"style must be one of {MARKUP_STYLES}, got {style!r}")
        out["style"] = style

    elif kind == "note":
        _require(op, "page")
        out["at"] = _point(_require(op, "at"))
        _require(op, "text")

    elif kind == "text_box":
        _require(op, "page")
        out["rect"] = _rect(_require(op, "rect"))
        _require(op, "text")
        out["size"] = _number(
            op.get("size", 11),
            "size",
            minimum=0.1,
            maximum=MAX_FONT_SIZE,
            strict_minimum=True,
        )

    elif kind == "fill_field":
        _require(op, "field")
        _require(op, "value")

    elif kind == "ink":
        _require(op, "page")
        strokes = _require(op, "strokes")
        if not (isinstance(strokes, list) and strokes and all(
            isinstance(s, list) and len(s) >= 2 for s in strokes
        )):
            raise OpError("ink needs strokes: [[[x,y],...], ...], each with 2+ points")
        out["strokes"] = [[_point(p) for p in s] for s in strokes]
        color = op.get("color", [0, 0, 0])
        if not (isinstance(color, (list, tuple)) and len(color) == 3):
            raise OpError(f"color must be [r, g, b] in 0..1, got {color!r}")
        out["color"] = [
            _number(c, f"color component {i}", minimum=0, maximum=1)
            for i, c in enumerate(color)
        ]
        out["width"] = _number(
            op.get("width", 2),
            "width",
            minimum=0.0,
            maximum=MAX_STROKE_WIDTH,
            strict_minimum=True,
        )

    elif kind == "place_signature":
        _require(op, "page")
        out["at"] = _point(_require(op, "at"))
        out["width"] = _number(
            op.get("width", 180),
            "width",
            minimum=0.0,
            maximum=MAX_SIGNATURE_WIDTH,
            strict_minimum=True,
        )
        out["signature"] = op.get("signature", "default")
        out["date"] = _boolean(op.get("date", False), "date")

    elif kind == "rotate_pages":
        out["pages"] = _page_list(_require(op, "pages"))
        degrees = _require(op, "degrees")
        if isinstance(degrees, bool) or degrees not in _ROTATE_DEGREES:
            raise OpError(f"degrees must be one of {_ROTATE_DEGREES}, got {degrees!r}")
        out["degrees"] = degrees

    elif kind == "delete_pages":
        out["pages"] = _page_list(_require(op, "pages"))

    elif kind == "move_pages":
        out["pages"] = _page_list(_require(op, "pages"))
        after = _require(op, "after")
        if not (isinstance(after, int) and not isinstance(after, bool) and after >= 0):
            raise OpError(f"after must be a non-negative integer (0 = beginning), got {after!r}")
        out["after"] = after

    elif kind == "insert_pages":
        after = _require(op, "after")
        if not (isinstance(after, int) and not isinstance(after, bool) and after >= 0):
            raise OpError(f"after must be a non-negative integer (0 = beginning), got {after!r}")
        out["after"] = after
        has_source = "source" in op
        has_blank = "blank" in op
        has_image = "image" in op
        variants = sum((has_source, has_blank, has_image))
        if variants != 1:
            raise OpError("insert_pages needs exactly one of 'source', 'blank', or 'image'")
        if has_source:
            out["source"] = str(_require(op, "source"))
            if "source_pages" in op:
                out["source_pages"] = _page_list(op["source_pages"], "source_pages")
        if has_blank:
            blank = _require(op, "blank")
            if not isinstance(blank, dict):
                raise OpError("blank must be an object with count, width, height")
            count = blank.get("count", 1)
            if not (isinstance(count, int) and not isinstance(count, bool) and count >= 1):
                raise OpError(f"blank.count must be a positive integer, got {count!r}")
            out["blank"] = {
                "count": count,
                "width": _number(
                    blank.get("width", 595),
                    "blank.width",
                    minimum=0.0,
                    maximum=MAX_DIMENSION,
                    strict_minimum=True,
                ),
                "height": _number(
                    blank.get("height", 842),
                    "blank.height",
                    minimum=0.0,
                    maximum=MAX_DIMENSION,
                    strict_minimum=True,
                ),
            }
        if has_image:
            out["image"] = str(_require(op, "image"))

    elif kind == "extract_pages":
        out["pages"] = _page_list(_require(op, "pages"))
        out["to"] = str(_require(op, "to"))

    elif kind == "redact":
        _require(op, "page")
        if ("match" in op) == ("rect" in op):
            raise OpError("redact needs exactly one of 'match' or 'rect'")
        if "rect" in op:
            out["rect"] = _rect(op["rect"])
        fill = op.get("fill", [0, 0, 0])
        if not (isinstance(fill, (list, tuple)) and len(fill) == 3):
            raise OpError(f"fill must be [r, g, b] in 0..1, got {fill!r}")
        out["fill"] = [
            _number(c, f"fill component {i}", minimum=0, maximum=1)
            for i, c in enumerate(fill)
        ]
        if "apply_now" in op:
            out["apply_now"] = _boolean(op["apply_now"], "apply_now")

    elif kind == "delete_annotation":
        _require(op, "page")
        index = _require(op, "index")
        if not (isinstance(index, int) and not isinstance(index, bool) and index >= 0):
            raise OpError(f"index must be a non-negative integer, got {index!r}")
        out["index"] = index

    elif kind == "crop_pages":
        out["pages"] = _page_list(_require(op, "pages"))
        out["rect"] = _rect(_require(op, "rect"))
        x0, y0, x1, y1 = out["rect"]
        if x1 - x0 < 1 or y1 - y0 < 1:
            raise OpError("crop_pages rect must have positive width and height")

    elif kind == "shape":
        _require(op, "page")
        shape = _require(op, "shape")
        if shape not in SHAPE_TYPES:
            raise OpError(
                f"unknown shape {shape!r}; valid shapes: {', '.join(SHAPE_TYPES)}"
            )
        out["shape"] = shape
        if shape in ("line", "arrow"):
            out["from"] = _point(_require(op, "from"))
            out["to"] = _point(_require(op, "to"))
        else:
            out["rect"] = _rect(_require(op, "rect"))
        color = op.get("color", [0, 0, 0])
        if not (isinstance(color, (list, tuple)) and len(color) == 3):
            raise OpError(f"color must be [r, g, b] in 0..1, got {color!r}")
        out["color"] = [
            _number(c, f"color component {i}", minimum=0, maximum=1)
            for i, c in enumerate(color)
        ]
        out["width"] = _number(
            op.get("width", 2),
            "width",
            minimum=0.0,
            maximum=MAX_STROKE_WIDTH,
            strict_minimum=True,
        )

    if "page" in out:
        page = out["page"]
        if not (isinstance(page, int) and not isinstance(page, bool) and page >= 1):
            raise OpError(f"page must be a 1-based integer, got {page!r}")

    return out


def validate_all(ops: list) -> list[dict]:
    if not isinstance(ops, list):
        raise OpError("ops payload must be a JSON array of operation objects")
    return [validate(op) for op in ops]
