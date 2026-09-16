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

import copy
import math
import re

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
    "ocr",
)

_ROTATE_DEGREES = (90, 180, 270, -90)

MAX_COORDINATE = 1_000_000.0
MAX_DIMENSION = 100_000.0
MAX_FONT_SIZE = 1_000.0
MAX_STROKE_WIDTH = 1_000.0
MAX_SIGNATURE_WIDTH = 10_000.0

_NUMBER = {"type": "number", "finite": True}
_COORDINATE = {
    "type": "number",
    "finite": True,
    "minimum": -MAX_COORDINATE,
    "maximum": MAX_COORDINATE,
}
_PAGE = {"type": "integer", "minimum": 1}
_PAGES = {"type": "array", "items": _PAGE, "minItems": 1}
_POINT = {"type": "array", "items": _COORDINATE, "minItems": 2, "maxItems": 2}
_RECT = {"type": "array", "items": _COORDINATE, "minItems": 4, "maxItems": 4}
_CROP_RECT = {
    **_RECT,
    "constraints": {"width_at_least": 1, "height_at_least": 1},
}
_COLOR = {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1}, "minItems": 3, "maxItems": 3}
_POSITIVE_DIMENSION = {
    "type": "number",
    "finite": True,
    "exclusiveMinimum": 0,
    "maximum": MAX_DIMENSION,
}
_FONT_SIZE = {
    "type": "number",
    "finite": True,
    "exclusiveMinimum": 0.1,
    "maximum": MAX_FONT_SIZE,
}
_STROKE_WIDTH = {
    "type": "number",
    "finite": True,
    "exclusiveMinimum": 0,
    "maximum": MAX_STROKE_WIDTH,
}
_SIGNATURE_WIDTH = {
    "type": "number",
    "finite": True,
    "exclusiveMinimum": 0,
    "maximum": MAX_SIGNATURE_WIDTH,
}

# This is deliberately data, not a second validator. ``validate`` remains the
# executable contract; this catalog gives agents the complete public shape,
# variants, defaults and examples without advertising fields the engine drops.
OPERATION_CATALOG = {
    "highlight": {
        "required": ["op", "page"],
        "variants": [
            {"name": "text-match", "required": ["match"]},
            {"name": "rectangle", "required": ["rect"]},
        ],
        "fields": {"op": {"const": "highlight"}, "page": _PAGE, "match": {"type": "string"}, "rect": _RECT, "style": {"type": "string", "enum": list(MARKUP_STYLES)}},
        "defaults": {"style": "highlight"},
        "examples": [
            {"op": "highlight", "page": 1, "match": "termination clause"},
            {"op": "highlight", "page": 1, "rect": [72, 130, 300, 148], "style": "underline"},
        ],
    },
    "note": {
        "required": ["op", "page", "at", "text"],
        "fields": {"op": {"const": "note"}, "page": _PAGE, "at": _POINT, "text": {"type": "string"}},
        "examples": [{"op": "note", "page": 2, "at": [450, 200], "text": "Check this figure"}],
    },
    "text_box": {
        "required": ["op", "page", "rect", "text"],
        "fields": {"op": {"const": "text_box"}, "page": _PAGE, "rect": _RECT, "text": {"type": "string"}, "size": _FONT_SIZE},
        "defaults": {"size": 11},
        "examples": [{"op": "text_box", "page": 2, "rect": [100, 300, 300, 330], "text": "N/A"}],
    },
    "fill_field": {
        "required": ["op", "field", "value"],
        "fields": {
            "op": {"const": "fill_field"},
            "field": {"type": "string"},
            "value": {"type": "string"},
            "page": _PAGE,
            "rect": _RECT,
        },
        "examples": [
            {"op": "fill_field", "field": "tenant_name", "value": "Jane Doe"},
            {
                "op": "fill_field",
                "field": "tenant_name",
                "value": "Jane Doe",
                "page": 2,
                "rect": [160, 180, 400, 198],
            },
        ],
    },
    "place_signature": {
        "required": ["op", "page", "at"],
        "fields": {"op": {"const": "place_signature"}, "page": _PAGE, "at": _POINT, "width": _SIGNATURE_WIDTH, "signature": {"type": "string"}, "date": {"type": "boolean"}},
        "defaults": {"width": 180, "signature": "default", "date": False},
        "examples": [{"op": "place_signature", "page": 4, "at": [120, 540], "width": 180, "signature": "default", "date": True}],
    },
    "ink": {
        "required": ["op", "page", "strokes"],
        "fields": {"op": {"const": "ink"}, "page": _PAGE, "strokes": {"type": "array", "items": {"type": "array", "items": _POINT, "minItems": 2}, "minItems": 1}, "color": _COLOR, "width": _STROKE_WIDTH},
        "defaults": {"color": [0, 0, 0], "width": 2},
        "examples": [{"op": "ink", "page": 1, "strokes": [[[100, 200], [120, 220], [140, 200]]]}],
    },
    "rotate_pages": {
        "required": ["op", "pages", "degrees"],
        "fields": {"op": {"const": "rotate_pages"}, "pages": _PAGES, "degrees": {"type": "integer", "enum": list(_ROTATE_DEGREES)}},
        "examples": [{"op": "rotate_pages", "pages": [2, 3], "degrees": 90}],
    },
    "delete_pages": {
        "required": ["op", "pages"],
        "fields": {"op": {"const": "delete_pages"}, "pages": _PAGES},
        "examples": [{"op": "delete_pages", "pages": [1, 4]}],
    },
    "move_pages": {
        "required": ["op", "pages", "after"],
        "fields": {"op": {"const": "move_pages"}, "pages": _PAGES, "after": {"type": "integer", "minimum": 0}},
        "examples": [{"op": "move_pages", "pages": [5, 6], "after": 1}],
    },
    "insert_pages": {
        "required": ["op", "after"],
        "variants": [
            {"name": "source-pdf", "required": ["source"], "optional": ["source_pages"]},
            {"name": "blank", "required": ["blank"]},
            {"name": "image", "required": ["image"]},
        ],
        "fields": {"op": {"const": "insert_pages"}, "after": {"type": "integer", "minimum": 0}, "source": {"type": "string"}, "source_pages": _PAGES, "blank": {"type": "object", "properties": {"count": {"type": "integer", "minimum": 1}, "width": _POSITIVE_DIMENSION, "height": _POSITIVE_DIMENSION}}, "image": {"type": "string"}},
        "defaults": {"blank.count": 1, "blank.width": 595, "blank.height": 842},
        "examples": [
            {"op": "insert_pages", "after": 2, "source": "other.pdf", "source_pages": [1, 2]},
            {"op": "insert_pages", "after": 0, "blank": {"count": 1, "width": 595, "height": 842}},
            {"op": "insert_pages", "after": 1, "image": "scan.png"},
        ],
    },
    "extract_pages": {
        "required": ["op", "pages", "to"],
        "fields": {"op": {"const": "extract_pages"}, "pages": _PAGES, "to": {"type": "string"}},
        "examples": [{"op": "extract_pages", "pages": [2, 3], "to": "excerpt.pdf"}],
    },
    "redact": {
        "required": ["op", "page"],
        "variants": [
            {"name": "text-match", "required": ["match"]},
            {"name": "rectangle", "required": ["rect"]},
        ],
        "fields": {"op": {"const": "redact"}, "page": _PAGE, "match": {"type": "string"}, "rect": _RECT, "fill": _COLOR, "apply_now": {"type": "boolean"}},
        "defaults": {"fill": [0, 0, 0], "apply_now": True},
        "examples": [
            {"op": "redact", "page": 1, "match": "secret token"},
            {"op": "redact", "page": 1, "rect": [72, 400, 300, 430], "fill": [0, 0, 0]},
        ],
    },
    "delete_annotation": {
        "required": ["op", "page", "index"],
        "fields": {"op": {"const": "delete_annotation"}, "page": _PAGE, "index": {"type": "integer", "minimum": 0}},
        "examples": [{"op": "delete_annotation", "page": 1, "index": 0}],
    },
    "shape": {
        "required": ["op", "page", "shape"],
        "variants": [
            {"name": "line-or-arrow", "shape": ["line", "arrow"], "required": ["from", "to"]},
            {"name": "rectangle-or-oval", "shape": ["rect", "oval"], "required": ["rect"]},
        ],
        "fields": {"op": {"const": "shape"}, "page": _PAGE, "shape": {"type": "string", "enum": list(SHAPE_TYPES)}, "from": _POINT, "to": _POINT, "rect": _RECT, "color": _COLOR, "width": _STROKE_WIDTH},
        "defaults": {"color": [0, 0, 0], "width": 2},
        "examples": [
            {"op": "shape", "page": 1, "shape": "line", "from": [72, 100], "to": [300, 200]},
            {"op": "shape", "page": 1, "shape": "rect", "rect": [80, 80, 220, 160]},
        ],
    },
    "crop_pages": {
        "required": ["op", "pages", "rect"],
        "fields": {"op": {"const": "crop_pages"}, "pages": _PAGES, "rect": _CROP_RECT},
        "examples": [{"op": "crop_pages", "pages": [1], "rect": [72, 80, 500, 750]}],
    },
}


OPERATION_CATALOG["ocr"] = {'required': ['op'],
 'fields': {'op': {'const': 'ocr'},
            'pages': {'type': 'array',
                      'items': {'type': 'integer', 'minimum': 1},
                      'minItems': 1,
                      'uniqueItems': True},
            'languages': {'type': 'array',
                          'items': {'type': 'string',
                                    'pattern': '^[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?$'},
                          'minItems': 1,
                          'uniqueItems': True},
            'expected_source_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
            'timeout_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 3600}},
 'defaults': {'languages': ['eng'], 'timeout_seconds': 300},
 'examples': [{'op': 'ocr', 'pages': [1, 3], 'languages': ['eng']}],
 'constraints': ['Exactly one op per batch',
                 'Explicit new output via apply(output=...)',
                 'Unknown keys rejected',
                 'Omitted pages means all; preserve document order',
                 'expected_source_sha256 required for execution, returned by preflight']}


def operation_catalog() -> dict:
    """Return a detached, JSON-safe catalog of every supported operation."""
    return {
        "version": 1,
        "coordinate_system": "unrotated CropBox-local PDF points, top-left origin; pages are 1-based",
        "mutation_policy": {
            "generic_apply_ops": "dry_run defaults to true; pass dry_run=false after approval",
            "dedicated_consequential": {
                "confirm_field": "confirm",
                "signature_confirm_field": "confirmed",
                "operations": [
                    "place_signature",
                    "delete_pages",
                    "redact",
                    "delete_annotation",
                ],
            },
            "dedicated_other": "writes immediately; use generic apply_ops with dry_run=true to propose",
        },
        "verification_routes": ["read_pdf", "list_pages", "render_page"],
        "operations": [
            {"name": name, **copy.deepcopy(OPERATION_CATALOG[name])}
            for name in OP_TYPES
        ],
    }

class OpError(ValueError):
    """An operation failed validation or could not be applied."""


class OCRError(OpError):
    """An OCR failure with a stable machine-readable code and optional report."""

    def __init__(self, code: str, message: str, ocr: dict | None = None):
        super().__init__(message)
        self.code = code
        self.ocr = ocr


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

    if kind == "ocr":
        try:
            unknown = set(op) - set(OPERATION_CATALOG["ocr"]["fields"])
            if unknown:
                raise OpError(f"Unsupported OCR options: {', '.join(map(str, sorted(unknown, key=str)))}; use the operation catalog")
            if "pages" in op:
                out["pages"] = sorted(_page_list(op["pages"]))
                if len(set(out["pages"])) != len(out["pages"]):
                    raise OpError("OCR pages must not contain duplicates")
            languages = op.get("languages", ["eng"])
            if (not isinstance(languages, list) or not languages
                    or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?", v) for v in languages)
                    or len(set(languages)) != len(languages) or languages == ["osd"]):
                raise OpError("OCR languages must be unique installed language identifiers; osd alone cannot recognize text")
            out["languages"] = list(languages)
            timeout = op.get("timeout_seconds", 300)
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
                raise OpError("OCR timeout_seconds must be an integer from 1 to 3600")
            out["timeout_seconds"] = timeout
            if "expected_source_sha256" in op and (not isinstance(op["expected_source_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", op["expected_source_sha256"])):
                raise OpError("OCR expected_source_sha256 must be the lowercase SHA-256 returned by preflight")
        except OpError as exc:
            raise OCRError("invalid_request", str(exc)) from exc

    elif kind == "highlight":
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
        field = _require(op, "field")
        if not isinstance(field, str) or not field:
            raise OpError(f"field must be a non-empty string, got {field!r}")
        _require(op, "value")
        if "page" in op:
            page = op["page"]
            if not (isinstance(page, int) and not isinstance(page, bool) and page >= 1):
                raise OpError(f"page must be a 1-based integer, got {page!r}")
            out["page"] = page
        if "rect" in op:
            out["rect"] = _rect(op["rect"])

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
