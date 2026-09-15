"""Live pinch zoom + signature drag-and-drop helpers (no GTK).

Pinch must not re-rasterize the PDF on every scale-changed event — that
freezes the GTK main loop. These helpers compute a cheap cairo scale for
the current paper pixmap and a clamped zoom percent to commit on gesture
end. Signature DND uses a private text payload so a popover drag can drop
onto the page as a ``place_signature`` ghost.
"""

from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import unquote, urlparse

SIG_DND_PREFIX = "omepreview-sig:"
ZOOM_MIN_PCT = 25.0
ZOOM_MAX_PCT = 400.0
SIG_GHOST_WIDTH = 160.0
SIG_MIN_WIDTH = 24.0
# Selection chrome is drawn in PDF points (then scaled by zoom). Pad matches
# the blue rect around a selected ghost; visual handles sit on its corners.
SELECT_HANDLE_PAD = 4.0
HANDLE_VISUAL_HALF = 3.2
HANDLE_HIT_RADIUS = 10.0
HANDLE_HIT_SCREEN_PX = 14.0
RESIZE_HANDLES = ("nw", "ne", "sw", "se")


def current_zoom_pct(zoom_pct: float | None, zoom: float) -> float:
    """Committed zoom percent; fit-page uses the live ``zoom`` scale."""
    if zoom_pct is not None:
        return zoom_pct
    return zoom / (96 / 72) * 100


def clamp_zoom_pct(pct: float) -> float:
    return max(ZOOM_MIN_PCT, min(ZOOM_MAX_PCT, pct))


def pinch_live_pct(start_pct: float, scale: float) -> float:
    """Zoom percent implied by Gtk.GestureZoom's total scale since begin."""
    return clamp_zoom_pct(start_pct * scale)


def pinch_pixmap_scale(committed_pct: float, live_pct: float) -> float:
    """Cairo extra scale for the already-rasterized paper pixmap."""
    if committed_pct <= 0:
        return 1.0
    return live_pct / committed_pct


def page_y_at_focus(
    *,
    page_origin_y: float,
    zoom: float,
    vscroll: float,
    viewport_y: float,
) -> float:
    """PDF-space Y (top-left origin) under a scroller-relative viewport Y."""
    if zoom <= 0:
        return 0.0
    return (vscroll + viewport_y - page_origin_y) / zoom


def scroll_to_keep_focus(
    *,
    page_origin_y: float,
    zoom: float,
    page_y: float,
    viewport_y: float,
    vmax: float,
) -> float:
    """vadjustment so ``page_y`` stays at the same screen height ``viewport_y``."""
    target = page_origin_y + page_y * zoom - viewport_y
    if vmax < 0:
        vmax = 0.0
    return max(0.0, min(vmax, target))


def map_translate_coordinates(mapped) -> tuple[bool, float, float]:
    """Normalize ``Widget.translate_coordinates()`` across GTK / PyGObject.

    GTK 4 GIR usually returns ``(ok, x, y)``. Omarchy GTK 4.22 / pygobject
    3.56 returned a 2-tuple ``(x, y)``; unpacking that as three values raised
    ``ValueError`` in the pinch handler. ``None`` / empty means failure.
    """
    if mapped is None:
        return False, 0.0, 0.0
    if not isinstance(mapped, (tuple, list)):
        return False, 0.0, 0.0
    n = len(mapped)
    if n == 3:
        tok, ax, ay = mapped
        return bool(tok), float(ax), float(ay)
    if n == 2:
        ax, ay = mapped
        return True, float(ax), float(ay)
    return False, 0.0, 0.0


def mapped_point(mapped) -> tuple[float, float] | None:
    """Return dest ``(x, y)`` when ``translate_coordinates`` succeeded."""
    ok, ax, ay = map_translate_coordinates(mapped)
    if not ok:
        return None
    return ax, ay


def matrix_point(matrix, x: float, y: float) -> tuple[float, float]:
    """Apply a PyMuPDF-style affine matrix to a point.

    PyMuPDF page matrices use the same top-left, y-down coordinate system as
    the editor's drawing area. Keeping this small helper duck-typed makes the
    page transform tests independent of GTK.
    """
    return (
        matrix.a * x + matrix.c * y + matrix.e,
        matrix.b * x + matrix.d * y + matrix.f,
    )


def matrix_delta(matrix, dx: float, dy: float) -> tuple[float, float]:
    """Apply only the linear part of *matrix* to a drag delta."""
    return (
        matrix.a * dx + matrix.c * dy,
        matrix.b * dx + matrix.d * dy,
    )


def matrix_rect(matrix, rect) -> tuple[float, float, float, float]:
    """Return the axis-aligned bounds of a transformed rectangle."""
    x0, y0, x1, y1 = rect
    points = (
        matrix_point(matrix, x0, y0),
        matrix_point(matrix, x1, y0),
        matrix_point(matrix, x0, y1),
        matrix_point(matrix, x1, y1),
    )
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def compute_pinch_focus(gesture, scroller, area) -> tuple[float, tuple[float, float]]:
    """Scroller-relative Y and drawing-area XY of a pinch (else viewport center).

    Duck-typed: ``gesture.get_bounding_box_center()``, scroller adjustments /
    ``translate_coordinates``, ``get_width`` / ``get_height``. Never raises on
    a 2-tuple map result. Callers must not set ``pinch_state`` until this
    returns.
    """
    vadj = scroller.get_vadjustment()
    hadj = scroller.get_hadjustment()
    vw = float(hadj.get_page_size() or scroller.get_width() or 1.0)
    vh = float(vadj.get_page_size() or scroller.get_height() or 1.0)
    try:
        ok, cx, cy = gesture.get_bounding_box_center()
    except Exception:
        ok, cx, cy = False, 0.0, 0.0
    if not ok:
        cx, cy = vw / 2.0, vh / 2.0
    mapped = None
    try:
        mapped = scroller.translate_coordinates(area, cx, cy)
    except Exception:
        mapped = None
    tok, ax, ay = map_translate_coordinates(mapped)
    if tok:
        return float(cy), (ax, ay)
    return float(cy), (
        float(hadj.get_value()) + float(cx),
        float(vadj.get_value()) + float(cy),
    )


def _capsule_address(capsule) -> int | None:
    """C pointer stored in a PyCapsule, or None if unreadable.

    PyGObject unnamed capsules stringify as ``<capsule object NULL at 0x…>``.
    That ``NULL`` is the capsule *name*, not a NULL GObject. Using
    ``"NULL" in str(capsule)`` made every live popover look dead, so Sign
    never called ``popup()``.
    """
    try:
        import ctypes

        getter = ctypes.pythonapi.PyCapsule_GetPointer
        getter.restype = ctypes.c_void_p
        getter.argtypes = [ctypes.py_object, ctypes.c_char_p]
        addr = getter(capsule, None)
    except Exception:
        return None
    if addr is None:
        return 0
    return int(addr)


def gi_pointer_ok(widget) -> bool:
    """True if a PyGObject wrapper still holds a non-NULL C pointer.

    A destroyed Gtk widget keeps a Python wrapper whose instance pointer is
    0. Calling GTK methods on that SIGSEGVs. Duck-typed test doubles without
    ``__gpointer__`` are treated as live.
    """
    if widget is None:
        return False
    if not hasattr(widget, "__gpointer__"):
        return True
    ptr = widget.__gpointer__
    if ptr is None:
        return False
    if isinstance(ptr, (str, bytes)):
        text = ptr.decode() if isinstance(ptr, bytes) else ptr
        return "NULL" not in text.upper()
    addr = _capsule_address(ptr)
    if addr is None:
        return True
    return addr != 0


def popover_native_surface(widget) -> tuple[object | None, object | None]:
    """``(native, surface)`` via GTK accessors if they exist.

    Gtk.Popover is itself a Gtk.Native, so ``get_native()`` can return the
    popover even when it has no GdkSurface. Missing accessors return None
    (skip that clause) rather than guessing names.
    """
    native = None
    surface = None
    get_native = getattr(widget, "get_native", None)
    if callable(get_native):
        try:
            native = get_native()
        except Exception:
            native = None
    get_surface = getattr(widget, "get_surface", None)
    if callable(get_surface):
        try:
            surface = get_surface()
        except Exception:
            surface = None
    if surface is None and native is not None:
        ns = getattr(native, "get_surface", None)
        if callable(ns):
            try:
                surface = ns()
            except Exception:
                surface = None
    return native, surface


def popover_allows(widget, action: str) -> bool:
    """Whether ``popup`` / ``popdown`` / ``autohide`` is safe on *widget*.

    ``popup`` needs a non-NULL pointer and a parent. If already realized,
    it also needs a GdkSurface (realized-but-nativeless is the SEGV state).

    ``popdown`` and ``autohide`` need parent + realized + a live GdkSurface.
    ``gtk_popover_set_autohide`` unrealizes; without a surface that
    dereference is SEGV_MAPERR. Check-then-act still races — callers must
    not toggle autohide on a mapped popover.
    """
    if action not in {"popup", "popdown", "autohide"}:
        raise ValueError(f"unknown popover action {action!r}")
    if not gi_pointer_ok(widget):
        return False
    try:
        if widget.get_parent() is None:
            return False
    except Exception:
        return False
    realized = None
    get_realized = getattr(widget, "get_realized", None)
    if callable(get_realized):
        try:
            realized = bool(get_realized())
        except Exception:
            return False
    native, surface = popover_native_surface(widget)
    # Skip the surface clause when no accessor exists (fakes / older GIR).
    # On GTK 4.22 Popover is Native, so get_native() returns self even
    # with no GdkSurface — surface is the real liveness check.
    has_surface_accessor = callable(getattr(widget, "get_surface", None)) or (
        native is not None and callable(getattr(native, "get_surface", None))
    )
    surface_dead = has_surface_accessor and surface is None
    if action == "popup":
        if realized is True and surface_dead:
            return False
        return True
    if realized is False:
        return False
    if surface_dead:
        return False
    return True


def popover_is_alive(widget) -> bool:
    """True when a popover is safe to ``popdown`` / ``set_autohide``."""
    return popover_allows(widget, "popdown")


def popover_can_popup(widget) -> bool:
    """True when ``popup()`` is safe: parented, and not realized-without-surface."""
    return popover_allows(widget, "popup")


def delete_selected_ghost(pending: list, selected: dict | None) -> dict | None:
    """Remove *selected* from *pending* if it is that ghost. Returns new selection."""
    if selected is None:
        return None
    if selected in pending:
        pending.remove(selected)
        return None
    return selected


def signature_dnd_payload(name: str) -> str:
    return f"{SIG_DND_PREFIX}{name}"


def parse_signature_dnd(value: object) -> str | None:
    """Return a stored signature name from a drag payload, or None."""
    if value is None:
        return None
    getter = getattr(value, "get_path", None)
    if callable(getter):
        path = getter()
        if path:
            return _name_from_signature_file(Path(path))
    text = str(value).strip()
    if not text:
        return None
    if text.startswith(SIG_DND_PREFIX):
        name = text[len(SIG_DND_PREFIX) :].strip()
        return name or None
    if text.startswith("file:"):
        path = Path(unquote(urlparse(text).path))
        return _name_from_signature_file(path)
    return _name_from_signature_file(Path(text))


def _name_from_signature_file(path: Path) -> str | None:
    if path.suffix.lower() not in {".svg", ".png"}:
        return None
    if path.parent.name not in {"signature", "signatures"}:
        return None
    name = path.stem
    if not name or name.startswith(".") or "/" in name:
        return None
    return name


def signature_ghost(
    page_no: int,
    name: str,
    px: float,
    py: float,
    aspect: float,
    width: float = SIG_GHOST_WIDTH,
) -> dict:
    """Pending ``sig`` item centered on a page point (top-left origin)."""
    return {
        "kind": "sig",
        "page": page_no,
        "x": px - width / 2,
        "y": py - width * aspect / 2,
        "w": width,
        "h": width * aspect,
        "date": False,
        "signature": name,
    }


def handle_hit_radius(zoom: float) -> float:
    """Page-space radius so handles stay hittable at low zoom (~14 CSS px)."""
    z = zoom if zoom > 0 else 1.0
    return max(HANDLE_HIT_RADIUS, HANDLE_HIT_SCREEN_PX / z)


def selection_handle_centers(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    pad: float = SELECT_HANDLE_PAD,
) -> dict[str, tuple[float, float]]:
    """Corner-handle centers matching the selected-ghost chrome in the editor."""
    return {
        "nw": (x0 - pad, y0 - pad),
        "ne": (x1 + pad, y0 - pad),
        "sw": (x0 - pad, y1 + pad),
        "se": (x1 + pad, y1 + pad),
    }


def hit_resize_handle(
    px: float,
    py: float,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    radius: float = HANDLE_HIT_RADIUS,
    pad: float = SELECT_HANDLE_PAD,
) -> str | None:
    """Return ``nw``/``ne``/``sw``/``se`` if ``(px, py)`` hits a corner handle."""
    best: str | None = None
    best_d = radius
    for name, (hx, hy) in selection_handle_centers(x0, y0, x1, y1, pad).items():
        d = math.hypot(px - hx, py - hy)
        if d <= best_d:
            best_d = d
            best = name
    return best


def resize_signature_keep_aspect(
    x: float,
    y: float,
    w: float,
    h: float,
    handle: str,
    px: float,
    py: float,
    *,
    aspect: float | None = None,
    min_width: float = SIG_MIN_WIDTH,
) -> tuple[float, float, float, float]:
    """New ``(x, y, w, h)`` after dragging ``handle`` to ``(px, py)``.

    The opposite corner stays fixed. Width/height keep ``aspect`` (default
    ``h / w``). Dragging past the anchor clamps to ``min_width`` rather than
    flipping the rect.
    """
    if handle not in RESIZE_HANDLES:
        raise ValueError(f"unknown resize handle {handle!r}")
    if w <= 0:
        w = min_width
    if aspect is None:
        aspect = h / w if w else 1.0
    if aspect <= 0:
        aspect = 1.0
    x1, y1 = x + w, y + h
    if handle == "se":
        ax, ay = x, y
        raw_w, raw_h = px - ax, py - ay
    elif handle == "nw":
        ax, ay = x1, y1
        raw_w, raw_h = ax - px, ay - py
    elif handle == "ne":
        ax, ay = x, y1
        raw_w, raw_h = px - ax, ay - py
    else:  # sw
        ax, ay = x1, y
        raw_w, raw_h = ax - px, py - ay
    new_w = max(raw_w, raw_h / aspect, min_width)
    new_h = new_w * aspect
    if handle == "se":
        return ax, ay, new_w, new_h
    if handle == "nw":
        return ax - new_w, ay - new_h, new_w, new_h
    if handle == "ne":
        return ax, ay - new_h, new_w, new_h
    return ax - new_w, ay, new_w, new_h
