"""omepreview edit — the GTK4 editor.

A thin client over the op engine, like everything else: tools build *pending
items* (ghosts) that are drawn over the rendered page; Save converts them to
ops and hands them to engine.apply(). Until Save, everything is draggable —
including proposals an agent supplies via --ops, which load as ghosts for the
human to nudge and confirm.

Toolbar lineage: PDFfiller's edit bar (Select/Text/Sign/Check/Cross/Undo) ×
omasnap's annotation bar (pen, shapes, minimal chrome).
"""

from __future__ import annotations

import copy
import io
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
try:
    gi.require_foreign("cairo")
except (ImportError, ValueError) as exc:
    raise SystemExit(
        "omepreview edit needs PyGObject cairo integration — install python3-gi-cairo "
        "(Debian/Ubuntu) or ensure python-gobject is built with cairo support."
    ) from exc
import cairo
import pymupdf
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo

from . import engine
from . import gui_pages
from . import theme as chrome_theme
from .crop_coords import transform_pending_for_crop
from .export_guard import unsaved_export_reason
from .fs_privacy import FILE_MODE, atomic_write_private
from . import signature as sig_store
from .ops import OpError, validate_all
from .page_preview import (
    PagePreviewState,
    rebind_items_to_identities,
    remap_page_index,
)
from .render import raster_page
from .redact_io import (
    match_redact_ghosts,
    redact_item_to_op,
    share_target_path,
    unused_sibling,
)
from .view_gestures import (
    HANDLE_VISUAL_HALF,
    SELECT_HANDLE_PAD,
    compute_pinch_focus,
    current_zoom_pct,
    delete_selected_ghost,
    handle_hit_radius,
    hit_resize_handle,
    matrix_delta,
    matrix_point,
    matrix_rect,
    mapped_point,
    page_y_at_focus,
    pinch_live_pct,
    pinch_pixmap_scale,
    resize_signature_keep_aspect,
    scroll_to_keep_focus,
    signature_ghost,
)
from .popover_safe import (
    popover_busy,
    popover_try_popdown,
    popover_try_popup,
    popover_try_set_autohide,
)
from .rail_icons import paint_redo as paint_redo_glyph
from .rail_icons import paint_undo as paint_undo_glyph
from .window_controls import window_controls_enabled

CHECK = [[(0.0, 7.0), (4.5, 12.0), (14.0, 0.0)]]
CROSS = [[(0.0, 0.0), (12.0, 12.0)], [(12.0, 0.0), (0.0, 12.0)]]
STAMP_SIZE = 16.0  # points
CHECK_COLOR = (0.18, 0.62, 0.31)
CROSS_COLOR = (0.84, 0.27, 0.27)
PEN_WIDTH = 2.0
PEN_COLORS = [
    ("Black", (0.1, 0.1, 0.1)),
    ("Red", (0.75, 0.1, 0.1)),
    ("Blue", (0.13, 0.35, 0.85)),
    ("Green", (0.15, 0.55, 0.3)),
    ("Orange", (0.95, 0.55, 0.05)),
]
NOTE_SIZE = 20.0  # points, drawn sticky-note glyph
SELECT_COLOR = (0.15, 0.45, 0.95)
PAGE_MARGIN_PX = 48
SHADOWS_LIGHT = ((16, 22, 0.12), (3, 5, 0.08), (1, 1.2, 0.14))
SHADOWS_DARK = ((18, 26, 0.55), (4, 7, 0.35), (1, 1.5, 0.5))
PAPER_EDGE_ALPHA = 0.08
SUPPORTED_PROPOSAL_OPS = frozenset(
    {
        "highlight",
        "note",
        "text_box",
        "fill_field",
        "place_signature",
        "ink",
        "redact",
        "delete_annotation",
        "shape",
    }
)


def _norm_rect(it: dict) -> tuple[float, float, float, float]:
    return (
        min(it["x0"], it["x1"]),
        min(it["y0"], it["y1"]),
        max(it["x0"], it["x1"]),
        max(it["y0"], it["y1"]),
    )


def _draw_shape(
    ctx,
    shape: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    color: tuple[float, ...],
    width: float,
    alpha: float = 1.0,
):
    ctx.set_source_rgba(color[0], color[1], color[2], alpha)
    ctx.set_line_width(width)
    if shape in ("line", "arrow"):
        ctx.move_to(x0, y0)
        ctx.line_to(x1, y1)
        ctx.stroke()
        if shape == "arrow":
            ang = math.atan2(y1 - y0, x1 - x0)
            ah = max(8.0, width * 4)
            for da in (2.4, -2.4):
                ctx.move_to(x1, y1)
                ctx.line_to(
                    x1 - ah * math.cos(ang + da),
                    y1 - ah * math.sin(ang + da),
                )
                ctx.stroke()
    elif shape == "rect":
        rx0, ry0, rx1, ry1 = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        ctx.rectangle(rx0, ry0, rx1 - rx0, ry1 - ry0)
        ctx.stroke()
    else:
        rx0, ry0, rx1, ry1 = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        mx, my = (rx0 + rx1) / 2, (ry0 + ry1) / 2
        rw, rh = max((rx1 - rx0) / 2, 1), max((ry1 - ry0) / 2, 1)
        ctx.save()
        ctx.translate(mx, my)
        ctx.scale(rw, rh)
        ctx.arc(0, 0, 1, 0, 2 * math.pi)
        ctx.stroke()
        ctx.restore()


def _luminance(r: float, g: float, b: float) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _color_scheme_is_dark() -> bool:
    """Honor Omarchy/GNOME + freedesktop appearance color-scheme."""
    return chrome_theme.color_scheme_is_dark()


def _sync_color_scheme() -> None:
    chrome_theme.sync_gtk_appearance()


def _theme_colors(widget: Gtk.Widget) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    style = widget.get_style_context()
    found_bg, bg = style.lookup_color("theme_bg_color")
    found_fg, fg = style.lookup_color("theme_fg_color")
    if found_bg and bg is not None:
        bg_rgb = (bg.red, bg.green, bg.blue)
    else:
        dark = _color_scheme_is_dark()
        bg_rgb = (0.208, 0.208, 0.208) if dark else (0.965, 0.961, 0.957)
    if found_fg and fg is not None:
        fg_rgb = (fg.red, fg.green, fg.blue)
    else:
        dark = _color_scheme_is_dark()
        fg_rgb = (0.933, 0.933, 0.925) if dark else (0.180, 0.204, 0.212)
    return bg_rgb, fg_rgb


def _desk_rgb(bg: tuple[float, float, float], light: bool) -> tuple[float, float, float]:
    factor = 0.95 if light else 0.62
    return tuple(max(0.0, min(1.0, c * factor)) for c in bg)


def _draw_paper_shadow(
    ctx: cairo.Context,
    ox: float,
    oy: float,
    pw: float,
    ph: float,
    layers: tuple[tuple[float, float, float], ...],
) -> None:
    for dy, blur, alpha in layers:
        spread = blur * 0.45
        ctx.set_source_rgba(0, 0, 0, alpha)
        ctx.rectangle(
            ox - spread * 0.5,
            oy + dy - spread * 0.3,
            pw + spread,
            ph + spread * 0.6,
        )
        ctx.fill()


def _draw_folio(
    ctx: cairo.Context,
    text: str,
    x: float,
    y: float,
    fg: tuple[float, float, float],
    alpha: float,
) -> None:
    layout = PangoCairo.create_layout(ctx)
    layout.set_font_description(Pango.FontDescription("monospace 10.5"))
    layout.set_text(text, -1)
    _, th = layout.get_pixel_size()
    ctx.move_to(x, y - th)
    ctx.set_source_rgba(fg[0], fg[1], fg[2], alpha)
    PangoCairo.update_layout(ctx, layout)
    PangoCairo.show_layout(ctx, layout)


def _editorial_css(light: bool, *, shade_desk: bool = True) -> bytes:
    desk_factor = "0.95" if light else "0.62"
    thumb_muted = "0.6" if light else "0.72"
    if shade_desk:
        desk_line = f"@define-color omapdf_desk shade(@theme_bg_color, {desk_factor});"
    else:
        desk_line = "@define-color omapdf_desk @theme_bg_color;"
    return f"""
{desk_line}

window.omapdf-editor {{
  background: @omapdf_desk;
  background-color: @omapdf_desk;
  color: @theme_fg_color;
  border: none;
  outline: none;
  box-shadow: none;
}}
window.omapdf-editor, scrolledwindow.omapdf-thumb-rail, listbox.omapdf-thumbs {{
  background: @omapdf_desk;
  background-color: @omapdf_desk;
  color: @theme_fg_color;
}}
scrolledwindow.omapdf-thumb-rail > viewport,
scrolledwindow.omapdf-thumb-rail viewport,
scrolledwindow.omapdf-thumb-rail overlay,
overlay.omapdf-thumbs-overlay,
list.omapdf-thumbs,
revealer.omapdf-thumb-revealer,
scrolledwindow.omapdf-page-canvas,
scrolledwindow.omapdf-page-canvas > viewport,
scrolledwindow.omapdf-page-canvas viewport,
listbox.omapdf-thumbs > row {{
  background: @omapdf_desk;
  background-color: @omapdf_desk;
  color: @theme_fg_color;
}}

popover.background,
popover.background > contents,
popover.menu,
popover.menu > contents,
popover contents {{
  background: @theme_bg_color;
  background-color: @theme_bg_color;
  color: @theme_fg_color;
}}

box.omapdf-overlay-toolbar {{
  color: @theme_fg_color;
  background-color: transparent;
  background-image: linear-gradient(to right,
    alpha(@omapdf_desk, 0), alpha(@omapdf_desk, 0.94) 10px, alpha(@omapdf_desk, 0.94));
  border: none;
  padding: 14px 0;
  margin: 0;
  min-width: 44px;
}}
box.omapdf-overlay-toolbar separator {{
  background: transparent;
  min-height: 14px;
}}
box.omapdf-overlay-toolbar .page-indicator,
box.omapdf-overlay-toolbar .zoom-indicator {{
  font-family: monospace;
  font-size: 10.5px;
  font-weight: normal;
  font-feature-settings: "tnum";
  letter-spacing: 0.02em;
  opacity: 0.62;
}}
button.omapdf-sig-card,
box.omapdf-sig-card {{
  padding: 8px 10px;
  border-radius: 0;
  min-width: 168px;
}}
button.omapdf-sig-card label,
box.omapdf-sig-card label {{
  font-family: monospace;
  font-size: 10.5px;
  opacity: 0.62;
}}
box.omapdf-sig-actions {{
  min-width: 280px;
}}
label.toast-banner {{
  background: @theme_fg_color;
  color: @theme_bg_color;
  font-family: monospace;
  font-size: 10.5px;
  padding: 7px 14px;
  border-radius: 0;
  margin-bottom: 24px;
}}
listbox.omapdf-thumbs row {{
  background: transparent;
  padding: 0;
}}
picture.omapdf-thumb {{
  box-shadow: 0 1px 2px alpha(black, 0.10), 0 1px 3px alpha(black, 0.08);
  outline: 1px solid alpha(currentColor, 0.08);
}}
listbox.omapdf-thumbs row:not(.omapdf-thumb-selected) picture.omapdf-thumb {{
  opacity: {thumb_muted};
}}
listbox.omapdf-thumbs row.omapdf-thumb-selected picture.omapdf-thumb {{
  opacity: 1;
  box-shadow: 0 3px 5px alpha(black, 0.08), 0 1px 1px alpha(black, 0.14);
  outline: none;
}}
listbox.omapdf-thumbs row label {{
  font-family: monospace;
  font-size: 10px;
  opacity: 0.42;
}}
listbox.omapdf-thumbs row.omapdf-thumb-selected label {{
  opacity: 0.9;
}}
listbox.omapdf-thumbs row.omapdf-thumb-inserted label {{
  opacity: 0.62;
}}
listbox.omapdf-thumbs row.omapdf-thumb-dragging {{
  opacity: 0.28;
}}
box.omapdf-drop-slot {{
  background-color: @theme_selected_bg_color;
  min-height: 4px;
}}
""".encode()


def _overlay_rail_css() -> bytes:
    """Editorial rail glyphs — loaded last so HeaderBar/Adwaita never fills tool buttons."""
    return b"""
box.omapdf-overlay-toolbar button,
box.omapdf-overlay-toolbar menubutton > button {
  background: transparent;
  background-image: none;
  border: none;
  box-shadow: none;
  outline: none;
  -gtk-icon-shadow: none;
  border-radius: 0;
}
box.omapdf-overlay-toolbar button.suggested-action,
box.omapdf-overlay-toolbar button.destructive-action,
box.omapdf-overlay-toolbar button.success,
box.omapdf-overlay-toolbar button.flat,
box.omapdf-overlay-toolbar button.toggle {
  background: transparent;
  background-color: transparent;
  background-image: none;
  box-shadow: none;
  border: none;
  color: inherit;
}
box.omapdf-overlay-toolbar button.tool-slim,
box.omapdf-overlay-toolbar menubutton.tool-slim > button {
  min-width: 28px;
  min-height: 28px;
  padding: 0;
  margin: 2px 8px;
  opacity: 0.62;
  transition: opacity 120ms ease;
}
box.omapdf-overlay-toolbar button.tool-slim:hover,
box.omapdf-overlay-toolbar menubutton.tool-slim > button:hover {
  opacity: 1;
  background: transparent;
  background-image: none;
}
box.omapdf-overlay-toolbar button.tool-slim:active,
box.omapdf-overlay-toolbar menubutton.tool-slim > button:active {
  background: alpha(currentColor, 0.08);
  background-image: none;
  border-radius: 3px;
}
box.omapdf-overlay-toolbar button.tool-slim:checked,
box.omapdf-overlay-toolbar menubutton.tool-slim > button:checked {
  opacity: 1;
  background-color: transparent;
  background-image: linear-gradient(currentColor, currentColor);
  background-size: 12px 1.5px;
  background-repeat: no-repeat;
  background-position: 50% calc(100% - 3px);
  box-shadow: none;
}
box.omapdf-overlay-toolbar button.tool-slim:disabled,
box.omapdf-overlay-toolbar menubutton.tool-slim > button:disabled {
  opacity: 0.25;
}
box.omapdf-overlay-toolbar button.tool-icon,
box.omapdf-overlay-toolbar menubutton.tool-icon > button {
  padding: 5px;
  min-width: 28px;
  min-height: 28px;
}
box.omapdf-overlay-toolbar box.omapdf-rail-row {
  margin: 0;
}
box.omapdf-overlay-toolbar box.omapdf-rail-row > button.tool-slim {
  margin: 1px 0;
  min-width: 20px;
  padding: 5px 2px;
}
box.omapdf-overlay-toolbar button.omapdf-ghost {
  background: transparent;
  background-image: none;
  border: 1px solid alpha(currentColor, 0.28);
  color: alpha(currentColor, 0.38);
  border-radius: 0;
  min-width: 36px;
  min-height: 24px;
  font-family: monospace;
  font-size: 10.5px;
  font-weight: 500;
  margin: 2px 8px;
  box-shadow: none;
}
box.omapdf-overlay-toolbar button.omapdf-ink {
  background: @theme_fg_color;
  background-image: none;
  color: @theme_bg_color;
  border: none;
  border-radius: 0;
  min-width: 36px;
  min-height: 24px;
  font-family: monospace;
  font-size: 10.5px;
  font-weight: 500;
  margin: 2px 8px;
  box-shadow: none;
}
"""


WINDOW_CONTROLS_CSS = b"""
headerbar.omapdf-window-controls {
  min-height: 28px;
  padding: 0 4px;
  border: none;
  box-shadow: none;
  background: @theme_bg_color;
}
headerbar.omapdf-window-controls button.titlebutton {
  border-radius: 6px;
  min-width: 26px;
  min-height: 22px;
  margin: 2px;
  padding: 2px 4px;
}
"""


def _signature_surface(path: str) -> cairo.ImageSurface:
    pix = sig_store.rasterize(path)
    return cairo.ImageSurface.create_from_png(io.BytesIO(pix.tobytes("png")))


def _unused_archive(path: str | Path) -> Path:
    """Choose a free sibling archive path without replacing an existing file."""
    source = Path(path)
    candidate = source.with_suffix(".zip")
    if candidate != source and not candidate.exists() and not candidate.is_symlink():
        return candidate
    n = 2
    while True:
        candidate = source.with_name(f"{source.stem}-{n}.zip")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        n += 1


def _publish_private_new(path: str | Path, data: bytes) -> None:
    """Create private bytes without replacing any existing directory entry."""
    destination = Path(path)
    fd, staged_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    staged = Path(staged_name)
    try:
        os.fchmod(fd, FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(bytes(data))
            handle.flush()
            os.fsync(handle.fileno())
        # link(2) publishes the complete staged inode without replacing any
        # existing directory entry.  EEXIST is retried by zip_file_private.
        os.link(staged, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if fd is not None:
            os.close(fd)
        staged.unlink(missing_ok=True)


def zip_file_private(path: str | Path) -> Path:
    """Create a private, collision-safe ZIP containing one file."""
    import zipfile

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"no such file to zip: {source}")
    destination = _unused_archive(source)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(source, source.name)
    data = archive.getvalue()
    while True:
        try:
            _publish_private_new(destination, data)
            return destination
        except FileExistsError:
            # Another exporter claimed the candidate after the free-name scan.
            destination = _unused_archive(source)


class Editor:
    def __init__(self, pdf: str | None, ops_file: str | None):
        pdf = (pdf or "").strip() or None
        if pdf:
            self.path = str(Path(pdf).expanduser().resolve())
            self.doc = pymupdf.open(self.path)
            self.page_preview = PagePreviewState(self.path)
        else:
            self.path = ""
            self.doc = None
            self.page_preview = PagePreviewState(None)
        self.page_no = 0
        self.zoom = 1.0
        self.pending: list[dict] = []
        self.undo_stack: list[list[dict]] = []
        self.redo_stack: list[list[dict]] = []
        self.selected: dict | None = None
        self.tool = "select"
        self.page_surface: cairo.ImageSurface | None = None
        self.sig_name = "default"
        self.sig_surfaces: dict[str, cairo.ImageSurface | None] = {}
        self.live_stroke: list[tuple[float, float]] | None = None
        self.rubber: tuple[float, float, float, float] | None = None
        self.drag_base: tuple[float, float] | None = None
        self.drag_resize: dict | None = None
        self.pen_color = PEN_COLORS[1][1]
        self.shape_kind = "rect"
        self.zoom_pct: float | None = None  # None = fit page in viewport
        self.pinch_live_scale = 1.0  # cairo extra scale while pinching
        self.pinch_focus_area: tuple[float, float] | None = None
        self.page_origin = (0.0, 0.0)  # paper top-left in view pixels
        self.paper_px = (0, 0)  # paper width/height in view pixels
        self.search_term = ""
        self.search_hits: list[tuple[int, pymupdf.Rect]] = []
        self.search_pos = -1
        self.page_preview.on_identities_changed = self._rebind_pending_pages
        self.last_dropped_pending = 0
        self.window: Gtk.Window | None = None
        self._view_doc: pymupdf.Document | None = None
        self.redact_free_rect = False
        self.redact_save_as_copy = True
        self.redact_modal_shown = False
        if ops_file and self.has_document():
            self._load_proposals(ops_file)

    def has_document(self) -> bool:
        return bool(self.path) and self.doc is not None and not self.doc.is_closed

    def _rebind_pending_pages(self, old_ids: list[int], new_ids: list[int]) -> None:
        """Keep ghosts and search hits on the same logical page after surgery."""
        dropped = rebind_items_to_identities(self.pending, old_ids, new_ids)
        self.last_dropped_pending += dropped
        if self.selected is not None and self.selected not in self.pending:
            self.selected = None
        remapped_hits: list[tuple[int, pymupdf.Rect]] = []
        for pno, rect in self.search_hits:
            new_page = remap_page_index(pno, old_ids, new_ids)
            if new_page is not None:
                remapped_hits.append((new_page, rect))
        self.search_hits = remapped_hits
        if self.search_hits:
            self.search_pos = min(max(self.search_pos, 0), len(self.search_hits) - 1)
        else:
            self.search_pos = -1
        if self.page_no < len(old_ids) and new_ids:
            sid = old_ids[self.page_no]
            try:
                self.page_no = new_ids.index(sid)
            except ValueError:
                self.page_no = min(self.page_no, len(new_ids) - 1)

    # ---- model ----------------------------------------------------------

    def invalidate_view(self):
        if self._view_doc is not None:
            self._view_doc.close()
            self._view_doc = None

    def viewing_doc(self) -> pymupdf.Document:
        if not self.has_document():
            raise OpError("no PDF open — Open a file from the editor (Ctrl+O)")
        if self._view_doc is None:
            self._view_doc = self.page_preview.open_view()
        return self._view_doc

    def page_doc(self) -> pymupdf.Document:
        """Scratch PDF when page ops are pending; otherwise the live file handle."""
        if not self.has_document():
            raise OpError("no PDF open — Open a file from the editor (Ctrl+O)")
        if self.page_preview.has_changes():
            return self.viewing_doc()
        return self.doc

    def page(self) -> pymupdf.Page:
        return self.page_doc()[self.page_no]

    def page_count(self) -> int:
        if not self.has_document():
            return 0
        return self.page_doc().page_count

    def checkpoint(self):
        self.undo_stack.append({
            "kind": "pending",
            "pending": copy.deepcopy(self.pending),
            "page_ops": copy.deepcopy(self.page_preview.page_ops),
        })
        self.redo_stack.clear()
        self._prune_page_sources()

    def _prune_page_sources(self) -> None:
        operations = list(self.page_preview.page_ops)
        for entry in self.undo_stack + self.redo_stack:
            operations.extend(entry.get("page_ops", []))
            operations.extend(entry.get("page_ops_before", []))
        self.page_preview.prune_temp_sources({
            str(op["source"]) for op in operations if "source" in op
        })

    def close(self) -> None:
        """Release the document and session-owned scratch inputs."""
        self.invalidate_view()
        if self.doc is not None and not self.doc.is_closed:
            self.doc.close()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.page_preview.clear()

    def record_save(self, file_before: bytes, pending_before: list[dict], page_ops_before: list[dict]):
        """A save is an undoable step too: undoing it reverts the file and
        resurrects the saved items as editable ghosts."""
        self.undo_stack.append({
            "kind": "save",
            "file_before": file_before,
            "file_after": Path(self.path).read_bytes(),
            "pending_before": pending_before,
            "page_ops_before": page_ops_before,
        })
        self.redo_stack.clear()
        self._prune_page_sources()

    def _restore_file(self, data: bytes):
        """Restore a saved byte snapshot without truncating the live file."""
        if not self.path:
            raise OpError("no PDF open — Open a file from the editor (Ctrl+O)")
        try:
            probe = pymupdf.open(stream=data, filetype="pdf")
            probe.close()
        except Exception as exc:
            raise OpError(f"cannot restore invalid PDF bytes: {exc}") from exc

        path = Path(self.path)
        before = path.read_bytes()
        if self.doc is not None and not self.doc.is_closed:
            self.doc.close()
        try:
            atomic_write_private(path, data)
            restored = pymupdf.open(self.path)
        except BaseException:
            # A staged write normally fails before publication.  If a
            # post-rename observation fails, put the original bytes back
            # before reopening the editor so history and the document agree.
            try:
                if path.read_bytes() != before:
                    atomic_write_private(path, before)
                self.doc = pymupdf.open(self.path)
            except BaseException:
                self.doc = None
            self.invalidate_view()
            raise
        self.doc = restored
        self.invalidate_view()

    def _restore_editor_state(self, pending: list[dict], page_ops: list[dict]) -> None:
        self.pending = copy.deepcopy(pending)
        self.page_preview.page_ops = copy.deepcopy(page_ops)
        preview = self.page_preview.rebuild()
        preview.close()
        self.invalidate_view()
        self.selected = None

    def _restore_model_after_history_failure(
        self, pending: list[dict], page_ops: list[dict]
    ) -> None:
        self.pending = copy.deepcopy(pending)
        self.page_preview.page_ops = copy.deepcopy(page_ops)
        try:
            preview = self.page_preview.rebuild()
            preview.close()
        except BaseException:
            pass
        self.invalidate_view()

    def undo(self) -> bool:
        """Returns True when the file itself changed (a save was reverted)."""
        if not self.undo_stack:
            return False
        entry = self.undo_stack[-1]
        if entry["kind"] == "pending":
            pending_before = copy.deepcopy(self.pending)
            page_ops_before = copy.deepcopy(self.page_preview.page_ops)
            try:
                self._restore_editor_state(
                    entry["pending"], entry.get("page_ops", [])
                )
            except BaseException:
                self._restore_model_after_history_failure(
                    pending_before, page_ops_before
                )
                raise
            self.undo_stack.pop()
            self.redo_stack.append({
                "kind": "pending",
                "pending": pending_before,
                "page_ops": page_ops_before,
            })
            self._prune_page_sources()
            return False
        file_before = Path(self.path).read_bytes()
        pending_before = copy.deepcopy(self.pending)
        page_ops_before = copy.deepcopy(self.page_preview.page_ops)
        try:
            self._restore_file(entry["file_before"])
            self._restore_editor_state(
                entry["pending_before"], entry.get("page_ops_before", [])
            )
        except BaseException:
            try:
                if Path(self.path).read_bytes() != file_before:
                    self._restore_file(file_before)
            except BaseException:
                pass
            self._restore_model_after_history_failure(
                pending_before, page_ops_before
            )
            raise
        self.undo_stack.pop()
        self.redo_stack.append(entry)
        self._prune_page_sources()
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        entry = self.redo_stack[-1]
        if entry["kind"] == "pending":
            pending_before = copy.deepcopy(self.pending)
            page_ops_before = copy.deepcopy(self.page_preview.page_ops)
            try:
                self._restore_editor_state(
                    entry["pending"], entry.get("page_ops", [])
                )
            except BaseException:
                self._restore_model_after_history_failure(
                    pending_before, page_ops_before
                )
                raise
            self.redo_stack.pop()
            self.undo_stack.append({
                "kind": "pending",
                "pending": pending_before,
                "page_ops": page_ops_before,
            })
            self._prune_page_sources()
            return False
        file_before = Path(self.path).read_bytes()
        pending_before = copy.deepcopy(self.pending)
        page_ops_before = copy.deepcopy(self.page_preview.page_ops)
        try:
            self._restore_file(entry["file_after"])
            self.pending = []
            self.page_preview.clear(preserve_temp_sources=True)
            preview = self.page_preview.rebuild()
            preview.close()
            self.invalidate_view()
            self.selected = None
        except BaseException:
            try:
                if Path(self.path).read_bytes() != file_before:
                    self._restore_file(file_before)
            except BaseException:
                pass
            self._restore_model_after_history_failure(
                pending_before, page_ops_before
            )
            raise
        self.redo_stack.pop()
        self.undo_stack.append(entry)
        self._prune_page_sources()
        return True

    def adopt_document(self, path: str) -> None:
        """Open *path* as the active document (watcher/title follow ``self.path``)."""
        new_path = str(Path(path).resolve())
        if self.doc is not None and not self.doc.is_closed:
            self.doc.close()
        self.invalidate_view()
        self.path = new_path
        self.doc = pymupdf.open(self.path)
        self.page_preview.retarget(self.path)

    def open_path(self, path: str) -> None:
        """Replace the session with a newly opened file (in-app Open)."""
        self.adopt_document(path)
        self.page_no = 0
        self.pending.clear()
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.selected = None
        self.search_hits = []
        self.search_pos = -1
        self.search_term = ""
        self.page_surface = None
        self.live_stroke = None
        self.rubber = None
        self.drag_base = None
        self.drag_resize = None

    def save_pending(self) -> dict:
        """Apply ghosts through the engine. Returns the active path after save.

        Redaction copy-save writes a unique ``*_redacted.pdf`` (never clobbers
        an existing file) and switches the editor onto that copy so Share
        attaches the redacted bytes.
        """
        if not self.has_document():
            raise OpError("no PDF open — Open a file from the editor (Ctrl+O)")
        markup_ops = self.to_ops()
        page_ops = copy.deepcopy(self.page_preview.page_ops)
        if not markup_ops and not page_ops:
            raise OpError("nothing to save")
        redact_ops = [op for op in markup_ops if op["op"] == "redact"]
        operations = page_ops + markup_ops
        source = Path(self.path)
        file_before = source.read_bytes()
        pending_before = copy.deepcopy(self.pending)
        page_ops_before = copy.deepcopy(self.page_preview.page_ops)
        created_copy: Path | None = None
        target = source
        try:
            if redact_ops and self.redact_save_as_copy:
                created_copy = unused_sibling(source, "_redacted")
                target = created_copy
            # The engine stages the complete sequence and publishes once.  A
            # failed operation therefore leaves both the source and any
            # existing output untouched, while pending ghosts remain usable
            # for a retry.
            engine.apply(source, operations, output=target)
        except BaseException:
            # The engine publishes the target only after the complete
            # operation sequence succeeds.  Leave a path that appeared here
            # untouched: it may belong to a concurrent actor rather than to
            # this failed save attempt.
            raise
        new_doc = None
        try:
            # Keep the live editor and its scratch inputs intact until every
            # fallible part of the handoff has succeeded.
            new_doc = pymupdf.open(str(target))
            new_preview = PagePreviewState(target)
            new_preview.on_identities_changed = self._rebind_pending_pages
            history = self.undo_stack + [{
                "kind": "save",
                "file_before": file_before,
                "file_after": target.read_bytes(),
                "pending_before": pending_before,
                "page_ops_before": page_ops_before,
            }]
            page_no = min(self.page_no, new_doc.page_count - 1)
        except BaseException:
            if new_doc is not None:
                new_doc.close()
            if created_copy is None:
                atomic_write_private(source, file_before)
            else:
                created_copy.unlink(missing_ok=True)
            raise

        old_doc, old_preview = self.doc, self.page_preview
        new_preview.take_temp_sources_from(old_preview)
        self.doc = new_doc
        self.path = str(target)
        # The sidebar captures this object for its lifetime. Transfer the
        # prepared state without replacing that shared object; keep retired
        # scratch resources on the temporary object for cleanup below.
        old_preview.__dict__, new_preview.__dict__ = (
            new_preview.__dict__, old_preview.__dict__
        )
        self.undo_stack = history
        self.redo_stack.clear()
        self.pending.clear()
        self.selected = None
        self.page_no = page_no
        # Cleanup cannot turn a committed Save into a retryable failure.
        try:
            self.invalidate_view()
            old_doc.close()
            new_preview.clear()
            self._prune_page_sources()
        except Exception:
            pass
        return {
            "path": self.path,
            "redacted_copy": str(created_copy) if created_copy else None,
            "saved": len(page_ops) + len(markup_ops),
            "original": str(source),
        }

    def sig_aspect(self, name: str | None = None) -> float:
        surface = self._ensure_sig(name)
        return surface.get_height() / surface.get_width() if surface else 0.4

    def _ensure_sig(self, name: str | None = None):
        name = name or self.sig_name
        if name not in self.sig_surfaces:
            try:
                self.sig_surfaces[name] = _signature_surface(str(sig_store.get(name)))
            except FileNotFoundError:
                self.sig_surfaces[name] = None
        return self.sig_surfaces[name]

    def invalidate_sigs(self) -> None:
        self.sig_surfaces.clear()

    def _load_proposals(self, ops_file: str):
        payload = json.loads(Path(ops_file).read_text())
        validated = validate_all(payload)
        unsupported = [
            (index + 1, op["op"])
            for index, op in enumerate(validated)
            if op["op"] not in SUPPORTED_PROPOSAL_OPS
        ]
        if unsupported:
            details = ", ".join(f"#{index} {kind}" for index, kind in unsupported)
            raise OpError(
                f"proposal rejected: {details} cannot be represented as editable "
                "ghosts; no proposed operations were loaded"
            )
        if self.doc is None or self.doc.is_closed:
            raise OpError("proposal rejected: no PDF is open")

        def proposal_page(op: dict, index: int) -> int:
            page = op["page"] - 1
            if page < 0 or page >= self.doc.page_count:
                raise OpError(
                    f"proposal rejected: operation #{index} targets page "
                    f"{op['page']}, but the document has {self.doc.page_count} pages"
                )
            return page

        proposed: list[dict] = []
        for index, op in enumerate(validated, 1):
            kind = op.get("op")
            page = proposal_page(op, index) if "page" in op else None
            if kind == "place_signature":
                w = op["width"]
                name = op.get("signature", "default")
                proposed.append({
                    "kind": "sig", "page": page, "x": op["at"][0], "y": op["at"][1],
                    "w": w, "h": w * self.sig_aspect(name), "date": op["date"],
                    "signature": name,
                })
            elif kind == "text_box":
                r = op["rect"]
                proposed.append({
                    "kind": "text", "page": page, "x": r[0], "y": r[1],
                    "rect": list(r), "text": op["text"], "size": op["size"],
                })
            elif kind == "highlight":
                rects = [pymupdf.Rect(op["rect"])] if "rect" in op else self.doc[page].search_for(op["match"])
                if not rects:
                    raise OpError(
                        f"proposal rejected: highlight text {op['match']!r} was "
                        f"not found on page {op['page']}"
                    )
                for r in rects:
                    proposed.append({
                        "kind": "highlight", "page": page,
                        "x0": float(r.x0), "y0": float(r.y0),
                        "x1": float(r.x1), "y1": float(r.y1),
                        "style": op["style"],
                    })
            elif kind == "note":
                proposed.append({
                    "kind": "note", "page": page,
                    "x": op["at"][0], "y": op["at"][1], "text": op["text"],
                })
            elif kind == "ink":
                proposed.append({
                    "kind": "ink", "page": page, "strokes": op["strokes"],
                    "color": op["color"], "width": op["width"],
                })
            elif kind == "shape":
                shape = op["shape"]
                if shape in ("line", "arrow"):
                    item = {
                        "kind": "shape", "page": page, "shape": shape,
                        "x0": op["from"][0], "y0": op["from"][1],
                        "x1": op["to"][0], "y1": op["to"][1],
                        "color": op["color"], "width": op["width"],
                    }
                else:
                    r = op["rect"]
                    item = {
                        "kind": "shape", "page": page, "shape": shape,
                        "x0": r[0], "y0": r[1], "x1": r[2], "y1": r[3],
                        "color": op["color"], "width": op["width"],
                    }
                proposed.append(item)
            elif kind == "redact":
                if "rect" in op:
                    r = op["rect"]
                    proposed.append({
                        "kind": "redact", "page": page,
                        "x0": r[0], "y0": r[1], "x1": r[2], "y1": r[3],
                        "fill": list(op["fill"]),
                        "apply_now": op.get("apply_now", True),
                    })
                else:
                    rects = self.doc[page].search_for(op["match"])
                    if not rects:
                        raise OpError(
                            f"proposal rejected: redact text {op['match']!r} was "
                            f"not found on page {op['page']}"
                        )
                    proposed.extend(
                        match_redact_ghosts(
                            page,
                            op["match"],
                            rects,
                            fill=op["fill"],
                            apply_now=op.get("apply_now", True),
                        )
                    )
            elif kind == "fill_field":
                field_matches = []
                for page_no in range(self.doc.page_count):
                    if page is not None and page_no != page:
                        continue
                    pdf_page = self.doc[page_no]
                    for widget in pdf_page.widgets() or []:
                        if widget.field_name != op["field"]:
                            continue
                        if "rect" in op and not engine._same_rect(
                            widget.rect, pymupdf.Rect(op["rect"])
                        ):
                            continue
                        field_matches.append((page_no, list(widget.rect)))
                if not field_matches:
                    selector = []
                    if page is not None:
                        selector.append(f"page {page + 1}")
                    if "rect" in op:
                        selector.append(f"rect {op['rect']}")
                    qualifier = f" matching {' and '.join(selector)}" if selector else ""
                    raise OpError(
                        f"proposal rejected: no form field named {op['field']!r}"
                        f"{qualifier} was found in the document"
                    )
                if len(field_matches) > 1:
                    details = ", ".join(
                        f"page {field_page + 1} rect {field_rect}"
                        for field_page, field_rect in field_matches
                    )
                    raise OpError(
                        f"proposal rejected: form field {op['field']!r} is ambiguous; "
                        "specify a unique page and/or rect selector. "
                        f"Matches: {details}"
                    )
                field_page, field_rect = field_matches[0]
                proposed.append({
                    "kind": "field_fill", "page": field_page,
                    "field": op["field"], "value": op["value"],
                    "rect": field_rect,
                })
            elif kind == "delete_annotation":
                pdf_page = self.doc[page]
                annotation_match = None
                for annotation_index, annot in enumerate(pdf_page.annots() or []):
                    if annotation_index == op["index"]:
                        annotation_match = (list(annot.rect), annot.type[1])
                        break
                if annotation_match is None:
                    raise OpError(
                        f"proposal rejected: annotation index {op['index']} is "
                        f"out of range on page {op['page']}"
                    )
                annot_rect, annot_type = annotation_match
                proposed.append({
                    "kind": "delete_annot", "page": page, "index": op["index"],
                    "rect": annot_rect, "annot_type": annot_type,
                })
        self.pending.extend(proposed)
        if proposed:
            self.selected = proposed[0]
            self.page_no = proposed[0]["page"]

    def to_ops(self) -> list[dict]:
        ops = []
        for it in self.pending:
            page = it["page"] + 1
            if it["kind"] == "sig":
                ops.append({"op": "place_signature", "page": page,
                            "at": [it["x"], it["y"]], "width": it["w"],
                            "signature": it.get("signature", "default"),
                            "date": it.get("date", False)})
            elif it["kind"] == "text":
                rect = it.get("rect")
                if rect is None:
                    w = max(40.0, len(it["text"]) * it["size"] * 0.6)
                    rect = [it["x"], it["y"], it["x"] + w, it["y"] + it["size"] * 1.6]
                ops.append({"op": "text_box", "page": page, "text": it["text"],
                            "rect": list(rect),
                            "size": it["size"]})
            elif it["kind"] == "note":
                ops.append({"op": "note", "page": page,
                            "at": [it["x"], it["y"]], "text": it["text"]})
            elif it["kind"] == "highlight":
                ops.append({"op": "highlight", "page": page,
                            "rect": [min(it["x0"], it["x1"]), min(it["y0"], it["y1"]),
                                     max(it["x0"], it["x1"]), max(it["y0"], it["y1"])],
                            "style": it.get("style", "highlight")})
            elif it["kind"] == "ink":
                ops.append({"op": "ink", "page": page, "strokes": it["strokes"],
                            "color": it["color"], "width": it["width"]})
            elif it["kind"] == "shape":
                base = {
                    "op": "shape",
                    "page": page,
                    "shape": it["shape"],
                    "color": it["color"],
                    "width": it["width"],
                }
                if it["shape"] in ("line", "arrow"):
                    ops.append({**base, "from": [it["x0"], it["y0"]], "to": [it["x1"], it["y1"]]})
                else:
                    x0, y0, x1, y1 = _norm_rect(it)
                    ops.append({**base, "rect": [x0, y0, x1, y1]})
            elif it["kind"] == "redact":
                ops.append(redact_item_to_op(it))
            elif it["kind"] == "field_fill":
                ops.append({
                    "op": "fill_field",
                    "field": it["field"],
                    "value": it["value"],
                    "page": page,
                    "rect": list(it["rect"]),
                })
            elif it["kind"] == "delete_annot":
                ops.append({"op": "delete_annotation", "page": page, "index": it["index"]})
        return engine._order_ops(ops)

    # ---- geometry -------------------------------------------------------

    def item_rect(self, it) -> tuple[float, float, float, float]:
        if it["kind"] == "sig":
            return it["x"], it["y"], it["x"] + it["w"], it["y"] + it["h"]
        if it["kind"] == "text":
            if "rect" in it:
                return tuple(it["rect"])
            w = max(40.0, len(it["text"]) * it["size"] * 0.6)
            return it["x"], it["y"], it["x"] + w, it["y"] + it["size"] * 1.6
        if it["kind"] == "note":
            return it["x"], it["y"], it["x"] + NOTE_SIZE, it["y"] + NOTE_SIZE
        if it["kind"] == "highlight":
            return _norm_rect(it)
        if it["kind"] == "shape":
            return _norm_rect(it)
        if it["kind"] == "redact":
            return _norm_rect(it)
        if it["kind"] == "field_fill":
            r = it["rect"]
            return r[0], r[1], r[2], r[3]
        if it["kind"] == "delete_annot":
            r = it["rect"]
            return r[0], r[1], r[2], r[3]
        xs = [p[0] for s in it["strokes"] for p in s]
        ys = [p[1] for s in it["strokes"] for p in s]
        return min(xs) - 4, min(ys) - 4, max(xs) + 4, max(ys) + 4

    def hit(self, x, y):
        for it in reversed([p for p in self.pending if p["page"] == self.page_no]):
            x0, y0, x1, y1 = self.item_rect(it)
            if x0 - 4 <= x <= x1 + 4 and y0 - 4 <= y <= y1 + 4:
                return it
            if it["kind"] == "sig" and hit_resize_handle(
                x, y, x0, y0, x1, y1, radius=handle_hit_radius(self.zoom)
            ):
                return it
        return None

    def hit_sig_handle(self, x, y, *, zoom: float = 1.0) -> str | None:
        """Corner handle under ``(x, y)`` on the selected signature ghost."""
        it = self.selected
        if it is None or it not in self.pending or it.get("kind") != "sig":
            return None
        x0, y0, x1, y1 = self.item_rect(it)
        return hit_resize_handle(
            x, y, x0, y0, x1, y1, radius=handle_hit_radius(zoom)
        )

    def delete_selected(self) -> bool:
        """Delete the selected pending ghost (or mark a saved annot deleted)."""
        if self.selected is None:
            return False
        if self.selected.get("kind") == "saved_annot":
            self.checkpoint()
            if not self._annot_marked_deleted(self.selected["page"], self.selected["index"]):
                self.pending.append({
                    "kind": "delete_annot",
                    "page": self.selected["page"],
                    "index": self.selected["index"],
                    "rect": self.selected["rect"],
                    "annot_type": self.selected["annot_type"],
                })
            self.selected = None
            return True
        if self.selected in self.pending:
            self.checkpoint()
            self.selected = delete_selected_ghost(self.pending, self.selected)
            return True
        return False

    def hit_widget(self, x, y):
        """Return an empty AcroForm text widget at (x, y), if any."""
        if not self.has_document():
            return None
        point = pymupdf.Point(x, y)
        for widget in self.page().widgets():
            if not widget.field_name or not widget.rect.contains(point):
                continue
            if widget.field_type != pymupdf.PDF_WIDGET_TYPE_TEXT:
                continue
            if (widget.field_value or "").strip():
                continue
            return widget
        return None

    def hit_saved_annot(self, x, y):
        """Return the smallest saved annotation under (x, y), if any."""
        if not self.has_document():
            return None
        best = None
        for index, annot in enumerate(self.page().annots() or []):
            if self._annot_marked_deleted(self.page_no, index):
                continue
            rect = annot.rect
            pad = 4
            if rect.x0 - pad <= x <= rect.x1 + pad and rect.y0 - pad <= y <= rect.y1 + pad:
                area = max(1.0, rect.width * rect.height)
                if best is None or area < best["area"]:
                    best = {
                        "kind": "saved_annot",
                        "page": self.page_no,
                        "index": index,
                        "annot_type": annot.type[1],
                        "rect": list(rect),
                        "area": area,
                    }
        return best

    def _annot_marked_deleted(self, page_no: int, index: int) -> bool:
        for it in self.pending:
            if it.get("kind") == "delete_annot" and it["page"] == page_no and it["index"] == index:
                return True
        return False

    def move_item(self, it, dx, dy):
        if it["kind"] in ("sig", "text", "note"):
            it["x"] += dx
            it["y"] += dy
            if it["kind"] == "text" and "rect" in it:
                it["rect"] = [
                    it["rect"][0] + dx,
                    it["rect"][1] + dy,
                    it["rect"][2] + dx,
                    it["rect"][3] + dy,
                ]
        elif it["kind"] in ("highlight", "redact", "shape"):
            for k in ("x0", "x1"):
                it[k] += dx
            for k in ("y0", "y1"):
                it[k] += dy
        elif it["kind"] in ("field_fill", "delete_annot"):
            # These ghosts identify an existing document target. Moving their
            # rectangle would suggest that Save also moves the widget or
            # annotation, but their operations intentionally carry identity,
            # not geometry. Keep the visual target anchored and make drag a
            # safe no-op.
            return False
        else:
            it["strokes"] = [[(px + dx, py + dy) for px, py in s] for s in it["strokes"]]
        return True


def pdf_open_dialog() -> Gtk.FileDialog:
    """In-window file picker for Open (Ctrl+O). Not used by the desktop Exec."""
    dialog = Gtk.FileDialog()
    dialog.set_title("Open PDF")
    filters = Gio.ListStore.new(Gtk.FileFilter)
    f_pdf = Gtk.FileFilter()
    f_pdf.set_name("PDF")
    f_pdf.add_mime_type("application/pdf")
    filters.append(f_pdf)
    dialog.set_filters(filters)
    dialog.set_default_filter(f_pdf)
    return dialog


def run(pdf: str | None = None, ops_file: str | None = None) -> int:
    Gtk.Window.set_default_icon_name("omapreview")
    _sync_color_scheme()
    ed = Editor(pdf, ops_file)
    app = Gtk.Application(
        application_id="org.omepreview.Editor", flags=Gio.ApplicationFlags.NON_UNIQUE
    )

    def on_activate(app):
        win = Gtk.ApplicationWindow(application=app)
        win.set_default_size(980, 900)
        show_window_controls = window_controls_enabled()
        if show_window_controls:
            win.add_css_class("omapdf-window-controls-on")
        else:
            win.set_decorated(False)
        win.add_css_class("omapdf-editor")
        ed.window = win

        def on_close(_win):
            # Async page paste callbacks use this identity to reject results
            # that arrive after the window has been closed or replaced.
            if ed.window is win:
                ed.window = None
            return False

        win.connect("close-request", on_close)
        chrome = {
            "desk": (0.92, 0.91, 0.91),
            "fg": (0.18, 0.2, 0.21),
            "light": True,
            "shadows": SHADOWS_LIGHT,
        }

        area = Gtk.DrawingArea()
        area.set_can_target(True)
        sidebar_api = {
            "refresh": lambda: None,
            "highlight_current": lambda _n: None,
            "select_row": lambda _n: None,
            "sidebar_focus": {"active": False},
            "selected_1based": lambda: [],
            "has_page_selection": lambda: False,
            "delete_selected_pages": lambda: None,
            "rotate_selected": lambda _d: None,
            "insert_blank_after_current": lambda: None,
            "copy_selected_pages": lambda: False,
            "paste_pages": lambda: False,
            "cut_selected_pages": lambda: False,
        }

        save_style_hook = {"fn": lambda: None}
        file_watch = {"retarget": lambda: None}
        empty_hook = {"fn": lambda: None}
        open_hook = {"fn": lambda: None}

        def refresh_title():
            dirty = ed.pending or ed.page_preview.has_changes()
            dot = " •" if dirty else ""
            if ed.has_document():
                win.set_title(f"{Path(ed.path).name}{dot} — omepreview")
            else:
                win.set_title("omapreview")
            save_style_hook["fn"]()
            empty_hook["fn"]()

        def viewport_width() -> float:
            # Prefer the scroller's allocated width — hadjustment page-size can
            # report the content width or 0 before the first layout pass.
            avail = float(scroller.get_width() or 0)
            if avail < 50:
                avail = float(scroller.get_allocated_width())
            if avail < 50:
                avail = float(scroller.get_hadjustment().get_page_size())
            if avail < 50:
                avail = 900.0
            return avail

        def viewport_height() -> float:
            avail = float(scroller.get_height() or 0)
            if avail < 50:
                avail = float(scroller.get_allocated_height())
            if avail < 50:
                avail = float(scroller.get_vadjustment().get_page_size())
            if avail < 50:
                avail = 700.0
            return avail

        def fit_page_zoom() -> float:
            if not ed.has_document():
                return 1.0
            page = ed.page()
            pw = page.rect.width or 595.0
            ph = page.rect.height or 842.0
            margin = PAGE_MARGIN_PX * 2
            zw = (viewport_width() - margin) / pw
            zh = (viewport_height() - margin) / ph
            return max(0.05, min(zw, zh))

        def to_page_point(cx: float, cy: float) -> tuple[float, float]:
            ox, oy = ed.page_origin
            page = ed.page()
            return matrix_point(
                page.derotation_matrix,
                (cx - ox) / ed.zoom,
                (cy - oy) / ed.zoom,
            )

        def to_view_point(px: float, py: float) -> tuple[float, float]:
            ox, oy = ed.page_origin
            page = ed.page()
            vx, vy = matrix_point(page.rotation_matrix, px, py)
            return (ox + vx * ed.zoom, oy + vy * ed.zoom)

        def to_view_rect(rect) -> tuple[float, float, float, float]:
            return matrix_rect(ed.page().rotation_matrix, rect)

        def capture_v_anchor(viewport_y: float | None = None) -> dict:
            vadj = scroller.get_vadjustment()
            vh = float(vadj.get_page_size() or scroller.get_height() or 1.0)
            if viewport_y is None:
                viewport_y = vh / 2.0
            return {
                "page_y": page_y_at_focus(
                    page_origin_y=ed.page_origin[1],
                    zoom=ed.zoom,
                    vscroll=float(vadj.get_value()),
                    viewport_y=float(viewport_y),
                ),
                "viewport_y": float(viewport_y),
            }

        def restore_v_anchor(anchor: dict | None) -> None:
            if not anchor:
                return
            vadj = scroller.get_vadjustment()
            vmax = max(0.0, float(vadj.get_upper() - vadj.get_page_size()))
            vadj.set_value(
                scroll_to_keep_focus(
                    page_origin_y=ed.page_origin[1],
                    zoom=ed.zoom,
                    page_y=anchor["page_y"],
                    viewport_y=anchor["viewport_y"],
                    vmax=vmax,
                )
            )

        sig_drag = {"active": False, "dragging": False, "just_dropped": False}
        pinch_state = {
            "start_pct": None,
            "live_pct": None,
            "commit_id": 0,
            "anchor": None,
            "pending_commit": False,
            "deferred_render": False,
            "deferred_anchor": None,
        }

        def render_page(*, v_anchor: dict | None = None):
            # Resizing the drawing area during a live signature-popover drag
            # can unrealize the popover; GTK then SIGSEGVs on set_autohide.
            if sig_drag.get("active") or popover_busy():
                pinch_state["deferred_render"] = True
                if v_anchor is not None:
                    pinch_state["deferred_anchor"] = v_anchor
                return
            ed.pinch_live_scale = 1.0
            ed.pinch_focus_area = None
            if not ed.has_document():
                ed.page_surface = None
                ed.paper_px = (0, 0)
                ed.page_origin = (0.0, 0.0)
                view_w = max(1, int(viewport_width()))
                view_h = max(1, int(viewport_height()))
                area.set_content_width(view_w)
                area.set_content_height(view_h)
                page_label.set_text("—")
                update_nav()
                area.queue_draw()
                refresh_title()
                return
            if ed.zoom_pct is None:
                z = fit_page_zoom()
            else:
                z = max(0.05, ed.zoom_pct / 100 * (96 / 72))
            ed.zoom = z
            zoom_dot.set_text("Fit" if ed.zoom_pct is None else f"{int(ed.zoom_pct)}%")
            page = ed.page()
            pix = raster_page(page, z, alpha=False)
            if pix.width < 1 or pix.height < 1:
                return
            ed.page_surface = cairo.ImageSurface.create_from_png(
                io.BytesIO(pix.tobytes("png"))
            )
            paper_w, paper_h = pix.width, pix.height
            ed.paper_px = (paper_w, paper_h)
            view_w = viewport_width()
            view_h = viewport_height()
            content_w = max(view_w, paper_w + 2 * PAGE_MARGIN_PX)
            content_h = max(view_h, paper_h + 2 * PAGE_MARGIN_PX)
            page_x = (content_w - paper_w) / 2
            page_y = (content_h - paper_h) / 2
            ed.page_origin = (page_x, page_y)
            area.set_content_width(int(content_w))
            area.set_content_height(int(content_h))
            page_label.set_text(f"{ed.page_no + 1} / {ed.page_count()}")
            update_nav()
            sidebar_api["highlight_current"](ed.page_no)
            if v_anchor is not None:
                restore_v_anchor(v_anchor)
                GLib.idle_add(lambda: (restore_v_anchor(v_anchor), False)[1])
            area.queue_draw()
            refresh_title()

        # -- drawing ------------------------------------------------------

        def draw(_a, ctx, w, h):
            ctx.save()
            desk = chrome["desk"]
            fg = chrome["fg"]
            ctx.set_source_rgb(*desk)
            ctx.rectangle(0, 0, w, h)
            ctx.fill()
            ox, oy = ed.page_origin
            pw, ph = ed.paper_px
            if pw > 0 and ph > 0:
                folio = f"{Path(ed.path).name} · {ed.page_count()} pages"
                _draw_folio(ctx, folio, ox, oy - 13, fg, 0.5)
                live = ed.pinch_live_scale
                if live != 1.0:
                    if ed.pinch_focus_area is not None:
                        fx, fy = ed.pinch_focus_area
                    else:
                        fx = ox + pw / 2
                        fy = oy + ph / 2
                    ctx.translate(fx, fy)
                    ctx.scale(live, live)
                    ctx.translate(-fx, -fy)
                _draw_paper_shadow(ctx, ox, oy, pw, ph, chrome["shadows"])
                ctx.set_source_rgb(1, 1, 1)
                ctx.rectangle(ox, oy, pw, ph)
                ctx.fill()
                if ed.page_surface:
                    ctx.set_source_surface(ed.page_surface, ox, oy)
                    ctx.paint()
                if chrome["light"]:
                    ctx.set_source_rgba(fg[0], fg[1], fg[2], PAPER_EDGE_ALPHA)
                    ctx.set_line_width(1.0)
                    ctx.rectangle(ox, oy, pw, ph)
                    ctx.stroke()
            else:
                ctx.restore()
                return
            ctx.translate(ox, oy)
            ctx.scale(ed.zoom, ed.zoom)
            rotation = ed.page().rotation_matrix
            ctx.transform(
                cairo.Matrix(
                    rotation.a,
                    rotation.b,
                    rotation.c,
                    rotation.d,
                    rotation.e,
                    rotation.f,
                )
            )
            for it in ed.pending:
                if it["page"] == ed.page_no:
                    draw_item(ctx, it)
            if (
                ed.selected
                and ed.selected.get("kind") == "saved_annot"
                and ed.selected["page"] == ed.page_no
            ):
                x0, y0, x1, y1 = ed.selected["rect"]
                w, h = x1 - x0 + 8, y1 - y0 + 8
                ctx.set_source_rgba(*SELECT_COLOR, 0.10)
                ctx.rectangle(x0 - 4, y0 - 4, w, h)
                ctx.fill()
                ctx.set_source_rgba(*SELECT_COLOR, 0.95)
                ctx.set_line_width(1.6)
                ctx.rectangle(x0 - 4, y0 - 4, w, h)
                ctx.stroke()
            if ed.live_stroke and len(ed.live_stroke) > 1:
                _stroke_path(ctx, [ed.live_stroke], ed.pen_color, PEN_WIDTH)
            if ed.rubber:
                x0, y0, x1, y1 = ed.rubber
                if ed.tool == "crop":
                    rx0, ry0, rx1, ry1 = (
                        min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1),
                    )
                    pw, ph = ed.page().rect.width, ed.page().rect.height
                    ctx.set_source_rgba(0, 0, 0, 0.42)
                    for band in (
                        (0, 0, pw, ry0),
                        (0, ry1, pw, ph - ry1),
                        (0, ry0, rx0, ry1 - ry0),
                        (rx1, ry0, pw - rx1, ry1 - ry0),
                    ):
                        bx, by, bw, bh = band
                        if bw > 0 and bh > 0:
                            ctx.rectangle(bx, by, bw, bh)
                            ctx.fill()
                    ctx.set_source_rgba(0.2, 0.55, 0.95, 0.95)
                    ctx.set_line_width(1.6)
                    ctx.set_dash([6, 4])
                    ctx.rectangle(rx0, ry0, rx1 - rx0, ry1 - ry0)
                    ctx.stroke()
                    ctx.set_dash([])
                elif ed.tool == "shape":
                    _draw_shape(
                        ctx, ed.shape_kind, x0, y0, x1, y1,
                        ed.pen_color, PEN_WIDTH, alpha=0.85,
                    )
                elif ed.tool == "redact":
                    ctx.set_source_rgba(0, 0, 0, 0.35)
                    ctx.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
                    ctx.fill()
                else:
                    ctx.set_source_rgba(1, 0.85, 0.1, 0.35)
                    ctx.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
                    ctx.fill()
            for i, (pno, rect) in enumerate(ed.search_hits):
                if pno != ed.page_no:
                    continue
                current = i == ed.search_pos
                ctx.set_source_rgba(1, 0.55, 0.05, 0.45 if current else 0.22)
                ctx.rectangle(rect.x0 - 1, rect.y0 - 1, rect.width + 2, rect.height + 2)
                ctx.fill()
                if current:
                    ctx.set_source_rgba(0.9, 0.4, 0, 0.9)
                    ctx.set_line_width(1.4)
                    ctx.rectangle(rect.x0 - 1, rect.y0 - 1, rect.width + 2, rect.height + 2)
                    ctx.stroke()
            ctx.restore()

        def draw_item(ctx, it):
            if it["kind"] == "sig":
                surface = ed._ensure_sig(it.get("signature") or ed.sig_name)
                if surface:
                    ctx.save()
                    ctx.translate(it["x"], it["y"])
                    s = it["w"] / surface.get_width()
                    ctx.scale(s, s)
                    ctx.set_source_surface(surface, 0, 0)
                    ctx.paint_with_alpha(0.92)
                    ctx.restore()
            elif it["kind"] == "text":
                ctx.set_source_rgb(0.05, 0.05, 0.05)
                ctx.select_font_face("sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                ctx.set_font_size(it["size"])
                ctx.move_to(it["x"], it["y"] + it["size"])
                ctx.show_text(it["text"])
            elif it["kind"] == "note":
                x, y = it["x"], it["y"]
                s = NOTE_SIZE
                fold = s * 0.3
                # Yellow sticky with a folded corner and text lines.
                ctx.move_to(x, y)
                ctx.line_to(x + s, y)
                ctx.line_to(x + s, y + s - fold)
                ctx.line_to(x + s - fold, y + s)
                ctx.line_to(x, y + s)
                ctx.close_path()
                ctx.set_source_rgb(1.0, 0.87, 0.35)
                ctx.fill_preserve()
                ctx.set_source_rgb(0.7, 0.58, 0.1)
                ctx.set_line_width(0.8)
                ctx.stroke()
                ctx.move_to(x + s - fold, y + s)
                ctx.line_to(x + s - fold, y + s - fold)
                ctx.line_to(x + s, y + s - fold)
                ctx.stroke()
                ctx.set_source_rgb(0.55, 0.45, 0.08)
                for i in (0.3, 0.5, 0.7):
                    ctx.move_to(x + s * 0.15, y + s * i)
                    ctx.line_to(x + s * 0.72, y + s * i)
                    ctx.stroke()
            elif it["kind"] == "highlight":
                x0, y0, x1, y1 = ed.item_rect(it)
                ctx.set_source_rgba(1, 0.85, 0.1, 0.35)
                ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
                ctx.fill()
            elif it["kind"] == "shape":
                _draw_shape(
                    ctx, it["shape"], it["x0"], it["y0"], it["x1"], it["y1"],
                    tuple(it["color"]), it["width"], alpha=0.92,
                )
            elif it["kind"] == "redact":
                x0, y0, x1, y1 = ed.item_rect(it)
                ctx.set_source_rgba(0, 0, 0, 0.45)
                ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
                ctx.fill()
                ctx.set_source_rgba(0.9, 0.2, 0.2, 0.85)
                ctx.set_line_width(1.2)
                ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
                ctx.stroke()
            elif it["kind"] == "field_fill":
                x0, y0, x1, y1 = ed.item_rect(it)
                ctx.set_source_rgba(0.2, 0.45, 0.95, 0.12)
                ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
                ctx.fill()
                ctx.set_source_rgba(0.15, 0.45, 0.95, 0.9)
                ctx.set_line_width(1.2)
                ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
                ctx.stroke()
                ctx.set_source_rgb(0.05, 0.05, 0.05)
                ctx.select_font_face("sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                size = min(12.0, max(9.0, (y1 - y0) * 0.7))
                ctx.set_font_size(size)
                ctx.move_to(x0 + 3, y0 + size + 1)
                ctx.show_text(it["value"])
            elif it["kind"] == "delete_annot":
                x0, y0, x1, y1 = ed.item_rect(it)
                ctx.set_source_rgba(0.9, 0.15, 0.15, 0.22)
                ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
                ctx.fill()
                ctx.set_source_rgba(0.9, 0.15, 0.15, 0.9)
                ctx.set_line_width(1.4)
                ctx.set_dash([4, 3])
                ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
                ctx.stroke()
                ctx.set_dash([])
            else:
                _stroke_path(ctx, it["strokes"], it["color"], it["width"])
            selected = ed.selected if ed.selected in ed.pending else None
            if it is selected:
                x0, y0, x1, y1 = ed.item_rect(it)
                pad = SELECT_HANDLE_PAD
                w, h = x1 - x0 + pad * 2, y1 - y0 + pad * 2
                # Tinted fill + solid border + corner handles: unmistakable.
                ctx.set_source_rgba(*SELECT_COLOR, 0.10)
                ctx.rectangle(x0 - pad, y0 - pad, w, h)
                ctx.fill()
                ctx.set_source_rgba(*SELECT_COLOR, 0.95)
                ctx.set_line_width(1.6)
                ctx.rectangle(x0 - pad, y0 - pad, w, h)
                ctx.stroke()
                hs = HANDLE_VISUAL_HALF
                for hx in (x0 - pad, x1 + pad):
                    for hy in (y0 - pad, y1 + pad):
                        ctx.set_source_rgb(1, 1, 1)
                        ctx.rectangle(hx - hs, hy - hs, hs * 2, hs * 2)
                        ctx.fill_preserve()
                        ctx.set_source_rgba(*SELECT_COLOR, 0.95)
                        ctx.set_line_width(1.1)
                        ctx.stroke()

        def _stroke_path(ctx, strokes, color, width):
            ctx.set_source_rgb(*color)
            ctx.set_line_width(width)
            ctx.set_line_cap(cairo.LINE_CAP_ROUND)
            ctx.set_line_join(cairo.LINE_JOIN_ROUND)
            for s in strokes:
                if len(s) < 2:
                    continue
                ctx.move_to(*s[0])
                for p in s[1:]:
                    ctx.line_to(*p)
                ctx.stroke()

        area.set_draw_func(draw)

        # -- text entry popover -------------------------------------------

        def prompt_entry(px, py, placeholder, on_text, initial=""):
            pop = Gtk.Popover()
            pop.set_parent(area)
            rect = Gdk.Rectangle()
            vx, vy = to_view_point(px, py)
            rect.x, rect.y, rect.width, rect.height = int(vx), int(vy), 1, 1
            pop.set_pointing_to(rect)
            entry = Gtk.Entry()
            entry.set_placeholder_text(placeholder)
            entry.set_width_chars(30)
            entry.set_text(initial)
            pop.set_child(entry)

            def commit(_e):
                text = entry.get_text().strip()
                popover_try_popdown(pop)
                if text:
                    ed.checkpoint()
                    item = on_text(text)
                    if not any(p is item for p in ed.pending):
                        ed.pending.append(item)
                    ed.selected = item
                    area.queue_draw()
                    refresh_title()

            entry.connect("activate", commit)
            GLib.idle_add(lambda: (popover_try_popup(pop), False)[1])
            entry.grab_focus()

        def prompt_text(px, py):
            prompt_entry(px, py, "Type text, then Enter", lambda t: {
                "kind": "text", "page": ed.page_no, "x": px, "y": py,
                "text": t, "size": 12.0,
            })

        def prompt_note(px, py):
            prompt_entry(px, py, "Sticky note comment, then Enter", lambda t: {
                "kind": "note", "page": ed.page_no, "x": px, "y": py, "text": t,
            })

        def prompt_field_fill(widget, px, py):
            name = widget.field_name

            def commit(value):
                ed.checkpoint()
                item = {
                    "kind": "field_fill",
                    "page": ed.page_no,
                    "field": name,
                    "value": value,
                    "rect": list(widget.rect),
                }
                ed.pending.append(item)
                ed.selected = item
                toast(f"Field {name!r} — not applied until Save")
                return item

            prompt_entry(px, py, f"Fill {name}, then Enter", commit)

        def edit_pending(item):
            """Re-open a pending note/text for editing, pre-filled."""

            def apply_text(t):
                item["text"] = t
                return item

            label = "Edit note — Enter to update" if item["kind"] == "note" \
                else "Edit text — Enter to update"
            prompt_entry(item["x"], item["y"], label, apply_text,
                         initial=item["text"])

        def show_saved_annot(px, py):
            """Click on an already-saved annotation: pop open its content."""
            best = None
            # Copy plain values inside the loop: PyMuPDF annot objects can go
            # stale once the generator advances.
            for a in ed.page().annots() or []:
                r = a.rect
                pad = 6
                if r.x0 - pad <= px <= r.x1 + pad and r.y0 - pad <= py <= r.y1 + pad:
                    area_sz = max(1.0, r.width * r.height)
                    if best is None or area_sz < best[2]:
                        best = (a.type[1], (a.info.get("content") or "").strip(), area_sz)
            # Clickability means "there is a comment to read": annotations
            # without text (bare highlights, ink) don't pop anything.
            if best is None or not best[1]:
                return False
            kind, content, _ = best
            titles = {"Text": "Comment", "FreeText": "Text box",
                      "Highlight": "Highlight comment"}
            pop = Gtk.Popover()
            pop.set_parent(area)
            rect = Gdk.Rectangle()
            vx, vy = to_view_point(px, py)
            rect.x, rect.y, rect.width, rect.height = int(vx), int(vy), 1, 1
            pop.set_pointing_to(rect)
            # Near the right edge, open leftward so the popover stays over
            # the page instead of spilling into the sidebar/window edge.
            if px > ed.page().rect.width * 0.72:
                pop.set_position(Gtk.PositionType.LEFT)
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            vbox.set_margin_top(8)
            vbox.set_margin_bottom(8)
            vbox.set_margin_start(10)
            vbox.set_margin_end(10)
            title = Gtk.Label()
            title.set_markup(f"<b>{GLib.markup_escape_text(titles.get(kind, kind))}</b>")
            title.set_halign(Gtk.Align.START)
            vbox.append(title)
            body = Gtk.Label(label=content)
            body.set_wrap(True)
            body.set_max_width_chars(44)
            body.set_halign(Gtk.Align.START)
            vbox.append(body)
            pop.set_child(vbox)
            # Defer past the in-flight click gesture: popping up during the
            # press can get the popover dismissed by its own click's release.
            GLib.idle_add(lambda: (popover_try_popup(pop), False)[1])
            return True

        # -- input --------------------------------------------------------

        def add_stamp(strokes_template, color, px, py):
            ed.checkpoint()
            scale = STAMP_SIZE / 14.0
            strokes = [[(px + sx * scale, py + sy * scale) for sx, sy in s]
                       for s in strokes_template]
            item = {"kind": "ink", "page": ed.page_no, "strokes": strokes,
                    "color": list(color), "width": 2.4}
            ed.pending.append(item)
            ed.selected = item
            set_tool("select")

        sig_ui = {"rebuild": lambda: None}

        def next_sig_name() -> str:
            names = set(sig_store.list_names())
            if "default" not in names:
                return "default"
            n = 2
            while f"signature-{n}" in names:
                n += 1
            return f"signature-{n}"

        def record_signature(name: str) -> bool:
            import subprocess

            toast("Record your signature on the trackpad…")
            proc = subprocess.run(
                [sys.executable, "-m", "omepreview.cli", "sig", "draw", "--name", name],
                env=os.environ.copy(),
            )
            ed.invalidate_sigs()
            if proc.returncode != 0:
                toast("Signature capture cancelled")
                return False
            ed.sig_name = name
            toast(f"Saved {name!r} — drag it onto the page")
            sig_ui["rebuild"]()
            return True

        def place_named_signature(name: str, px: float, py: float) -> bool:
            ed.sig_name = name
            if ed._ensure_sig(name) is None:
                return False
            ed.checkpoint()
            item = signature_ghost(
                ed.page_no, name, px, py, ed.sig_aspect(name),
            )
            ed.pending.append(item)
            ed.selected = item
            set_tool("select")
            area.queue_draw()
            refresh_title()
            return True

        click = Gtk.GestureClick()

        def on_click(_g, n_press, cx, cy):
            if not ed.has_document():
                open_hook["fn"]()
                return
            px, py = to_page_point(cx, cy)
            hit_item = ed.hit(px, py)
            if ed.tool == "text":
                if hit_item and hit_item["kind"] == "text":
                    edit_pending(hit_item)
                else:
                    prompt_text(px, py)
            elif ed.tool == "note":
                if hit_item and hit_item["kind"] == "note":
                    edit_pending(hit_item)
                else:
                    prompt_note(px, py)
            elif ed.tool == "sign":
                if sig_drag["active"] or sig_drag["just_dropped"]:
                    return
                name = ed.sig_name
                if ed._ensure_sig(name) is None:
                    names = sig_store.list_names()
                    if names:
                        name = names[0]
                        ed.sig_name = name
                    else:
                        name = "default"
                        if not record_signature(name):
                            return
                place_named_signature(name, px, py)
            elif ed.tool == "check":
                add_stamp(CHECK, CHECK_COLOR, px, py)
            elif ed.tool == "cross":
                add_stamp(CROSS, CROSS_COLOR, px, py)
            else:
                if hit_item:
                    ed.selected = hit_item
                else:
                    widget = ed.hit_widget(px, py)
                    if widget is not None:
                        prompt_field_fill(widget, px, py)
                    else:
                        saved = ed.hit_saved_annot(px, py)
                        if saved:
                            ed.selected = saved
                        else:
                            ed.selected = None
                            try:
                                show_saved_annot(px, py)
                            except Exception as exc:
                                toast(f"Couldn't open annotation: {exc}")
                if ed.selected and ed.selected in ed.pending and n_press >= 2 and ed.selected["kind"] in ("note", "text"):
                    edit_pending(ed.selected)
            area.queue_draw()
            refresh_title()

        click.connect("pressed", on_click)
        area.add_controller(click)

        def _pointer_in_widget(widget):
            """Pointer position in *widget* coordinates, or None."""
            native = widget.get_native()
            if native is None:
                return None
            surface = native.get_surface()
            if surface is None:
                return None
            display = widget.get_display()
            seat = display.get_default_seat() if display is not None else None
            device = seat.get_pointer() if seat is not None else None
            if device is None:
                return None
            ok, sx, sy, _mask = surface.get_device_position(device)
            if not ok:
                return None
            src = native if isinstance(native, Gtk.Widget) else win
            try:
                from gi.repository import Graphene

                ok, pt = src.compute_point(widget, Graphene.Point().init(sx, sy))
                if ok:
                    return float(pt.x), float(pt.y)
            except (TypeError, ValueError):
                pass
            try:
                mapped = src.translate_coordinates(widget, sx, sy)
            except Exception:
                return None
            return mapped_point(mapped)

        # Hovering a note/text (pending or saved) previews its content.
        area.set_has_tooltip(True)

        def on_tooltip(_w, tx, ty, _kb, tooltip):
            px, py = to_page_point(tx, ty)
            it = ed.hit(px, py)
            if it and it.get("kind") in ("note", "text") and it.get("text"):
                tooltip.set_text(it["text"])
                return True
            try:
                if not ed.has_document():
                    return False
                for a in ed.page().annots() or []:
                    r = a.rect
                    if r.x0 - 4 <= px <= r.x1 + 4 and r.y0 - 4 <= py <= r.y1 + 4:
                        content = (a.info.get("content") or "").strip()
                        if content:
                            tooltip.set_text(content)
                            return True
            except Exception:
                pass
            return False

        area.connect("query-tooltip", on_tooltip)

        drag = Gtk.GestureDrag()

        def on_drag_begin(g, sx, sy):
            if not ed.has_document():
                g.set_state(Gtk.EventSequenceState.DENIED)
                return
            if sig_drag["active"] or sig_drag["just_dropped"]:
                g.set_state(Gtk.EventSequenceState.DENIED)
                return
            px, py = to_page_point(sx, sy)
            if ed.tool == "pen":
                ed.live_stroke = [(px, py)]
            elif ed.tool == "highlight":
                ed.rubber = (px, py, px, py)
            elif ed.tool == "shape":
                ed.rubber = (px, py, px, py)
            elif ed.tool == "crop":
                ed.rubber = (px, py, px, py)
            elif ed.tool == "redact":
                ed.rubber = (px, py, px, py)
            elif ed.tool == "select":
                hit_item = ed.hit(px, py)
                handle = ed.hit_sig_handle(px, py, zoom=ed.zoom)
                target = ed.selected if handle else None
                if handle is None and hit_item and hit_item.get("kind") == "sig":
                    x0, y0, x1, y1 = ed.item_rect(hit_item)
                    handle = hit_resize_handle(
                        px, py, x0, y0, x1, y1, radius=handle_hit_radius(ed.zoom)
                    )
                    if handle:
                        target = hit_item
                if handle and target is not None and target in ed.pending:
                    ed.selected = target
                    ed.checkpoint()
                    ed.drag_resize = {
                        "handle": handle,
                        "aspect": (target["h"] / target["w"]) if target["w"] else 1.0,
                        "x": target["x"],
                        "y": target["y"],
                        "w": target["w"],
                        "h": target["h"],
                        "start": (px, py),
                    }
                    ed.drag_base = None
                elif hit_item:
                    ed.selected = hit_item
                    if hit_item.get("kind") in ("field_fill", "delete_annot"):
                        # Identity ghosts are anchored to their document
                        # target. A drag selects them but never moves a real
                        # widget/annotation or creates a misleading history
                        # entry.
                        ed.drag_base = None
                    else:
                        ed.checkpoint()
                        ed.drag_base = (0.0, 0.0)
                    ed.drag_resize = None
                else:
                    saved = ed.hit_saved_annot(px, py)
                    ed.selected = saved
                    ed.drag_base = None
                    ed.drag_resize = None
            area.queue_draw()

        def on_drag_update(_g, dx, dy):
            page = ed.page()
            pdx, pdy = matrix_delta(
                page.derotation_matrix,
                dx / ed.zoom,
                dy / ed.zoom,
            )
            if ed.tool == "pen" and ed.live_stroke is not None:
                sx, sy = ed.live_stroke[0]
                ed.live_stroke.append((sx + pdx, sy + pdy))
            elif ed.tool in ("highlight", "redact", "shape", "crop") and ed.rubber:
                x0, y0, _, _ = ed.rubber
                ed.rubber = (x0, y0, x0 + pdx, y0 + pdy)
            elif (
                ed.tool == "select"
                and ed.drag_resize
                and ed.selected in ed.pending
                and ed.selected.get("kind") == "sig"
            ):
                spec = ed.drag_resize
                cur_x = spec["start"][0] + pdx
                cur_y = spec["start"][1] + pdy
                nx, ny, nw, nh = resize_signature_keep_aspect(
                    spec["x"],
                    spec["y"],
                    spec["w"],
                    spec["h"],
                    spec["handle"],
                    cur_x,
                    cur_y,
                    aspect=spec["aspect"],
                )
                ed.selected["x"] = nx
                ed.selected["y"] = ny
                ed.selected["w"] = nw
                ed.selected["h"] = nh
            elif (
                ed.tool == "select"
                and ed.selected in ed.pending
                and ed.drag_base is not None
            ):
                lx, ly = ed.drag_base
                ed.move_item(ed.selected, pdx - lx, pdy - ly)
                ed.drag_base = (pdx, pdy)
            area.queue_draw()

        finish_redact_drag = {"fn": lambda: None}

        def on_drag_end(_g, _dx, _dy):
            if ed.tool == "pen" and ed.live_stroke and len(ed.live_stroke) > 1:
                ed.checkpoint()
                ed.pending.append({"kind": "ink", "page": ed.page_no,
                                   "strokes": [ed.live_stroke],
                                   "color": list(ed.pen_color), "width": PEN_WIDTH})
            ed.live_stroke = None
            if ed.tool == "highlight" and ed.rubber:
                x0, y0, x1, y1 = ed.rubber
                if abs(x1 - x0) > 3 and abs(y1 - y0) > 3:
                    ed.checkpoint()
                    ed.pending.append({"kind": "highlight", "page": ed.page_no,
                                       "x0": x0, "y0": y0, "x1": x1, "y1": y1})
            if ed.tool == "shape" and ed.rubber:
                x0, y0, x1, y1 = ed.rubber
                if max(abs(x1 - x0), abs(y1 - y0)) > 3:
                    ed.checkpoint()
                    ed.pending.append({
                        "kind": "shape", "page": ed.page_no,
                        "shape": ed.shape_kind,
                        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                        "color": list(ed.pen_color), "width": PEN_WIDTH,
                    })
            if ed.tool == "crop" and ed.rubber:
                x0, y0, x1, y1 = ed.rubber
                if abs(x1 - x0) > 3 and abs(y1 - y0) > 3:
                    crop = [
                        min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1),
                    ]
                    ed.checkpoint()
                    transform_pending_for_crop(ed.pending, ed.page_no, crop)
                    ed.page_preview.add_crop_pages([ed.page_no + 1], crop)
                    ed.invalidate_view()
                    on_sidebar_change()
                    toast("Crop not applied until Save")
            if ed.tool == "redact" and ed.rubber:
                x0, y0, x1, y1 = ed.rubber
                if abs(x1 - x0) > 3 and abs(y1 - y0) > 3:
                    ed.checkpoint()
                    band = pymupdf.Rect(
                        min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
                    )
                    added: list[dict] = []
                    if ed.redact_free_rect:
                        added.append({
                            "kind": "redact", "page": ed.page_no,
                            "x0": band.x0, "y0": band.y0, "x1": band.x1, "y1": band.y1,
                        })
                    else:
                        for w in ed.page().get_text("words"):
                            wr = pymupdf.Rect(w[:4])
                            if wr.intersects(band):
                                added.append({
                                    "kind": "redact", "page": ed.page_no,
                                    "x0": wr.x0, "y0": wr.y0, "x1": wr.x1, "y1": wr.y1,
                                    "match": w[4],
                                })
                        if not added:
                            added.append({
                                "kind": "redact", "page": ed.page_no,
                                "x0": band.x0, "y0": band.y0, "x1": band.x1, "y1": band.y1,
                            })
                    ed.pending.extend(added)
                    finish_redact_drag["fn"]()
            ed.rubber = None
            ed.drag_base = None
            ed.drag_resize = None
            area.queue_draw()
            refresh_title()

        drag.connect("drag-begin", on_drag_begin)
        drag.connect("drag-update", on_drag_update)
        drag.connect("drag-end", on_drag_end)
        area.add_controller(drag)

        # -- toolbar ------------------------------------------------------

        css = Gtk.CssProvider()
        rail_css = Gtk.CssProvider()
        applying = {"on": False}

        def apply_chrome(*_a):
            if applying["on"]:
                applying["again"] = True
                return
            applying["on"] = True
            try:
                pal = chrome_theme.load_omarchy_palette()
                if pal is not None:
                    chrome_theme.sync_gtk_appearance(dark=pal.is_dark)
                    _bg = chrome_theme.hex_to_rgb(pal.get("background"))
                    _fg = chrome_theme.hex_to_rgb(pal.get("foreground"))
                    prefix = pal.css_defines()
                    _light = chrome_theme.desk_is_light(_bg)
                    blob = prefix + _editorial_css(_light, shade_desk=False)
                    desk = _bg
                else:
                    dark = chrome_theme.sync_gtk_appearance()
                    _light = not dark
                    prefix = b""
                    blob = prefix + _editorial_css(_light)
                if show_window_controls:
                    blob += WINDOW_CONTROLS_CSS
                css.load_from_data(blob)
                if pal is None:
                    _bg, _fg = _theme_colors(win)
                    _light = chrome_theme.desk_is_light(_bg)
                    desk = _desk_rgb(_bg, _light)
                chrome["desk"] = desk
                chrome["fg"] = _fg
                chrome["light"] = _light
                chrome["shadows"] = SHADOWS_LIGHT if _light else SHADOWS_DARK
                area.queue_draw()
                win.queue_draw()
            finally:
                applying["on"] = False
                if applying.pop("again", False):
                    GLib.idle_add(apply_chrome)

        apply_chrome()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        rail_css.load_from_data(_overlay_rail_css())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            rail_css,
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
        )
        chrome_theme.watch_appearance(apply_chrome)
        chrome_theme.watch_theme_set(apply_chrome)
        win.connect("realize", apply_chrome)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        toolbar.add_css_class("omapdf-overlay-toolbar")
        toolbar.set_can_target(True)
        tools = {}
        first_btn = None

        def set_tool(name):
            ed.tool = name
            if not tools[name].get_active():
                tools[name].set_active(True)

        # -- vector icon set: one language, one stroke weight -------------

        def _path(ctx, pts):
            ctx.move_to(*pts[0])
            for p in pts[1:]:
                ctx.line_to(*p)

        def _ink(ctx, color, width):
            ctx.set_source_rgba(*color)
            ctx.set_line_width(width)
            ctx.set_line_cap(cairo.LINE_CAP_ROUND)
            ctx.set_line_join(cairo.LINE_JOIN_ROUND)

        def icon_widget(painter):
            da = Gtk.DrawingArea()
            da.set_content_width(18)
            da.set_content_height(18)
            da.set_halign(Gtk.Align.CENTER)
            da.set_valign(Gtk.Align.CENTER)

            def dr(widget, ctx, _w, _h):
                c = widget.get_color()
                painter(ctx, (c.red, c.green, c.blue, c.alpha))

            da.set_draw_func(dr)
            return da

        def paint_pointer(ctx, fg):
            ctx.set_source_rgba(*fg)
            _path(ctx, [(5, 2), (5, 14), (8.2, 11.4), (10, 15.4),
                        (12.1, 14.4), (10.3, 10.6), (14.6, 10.3)])
            ctx.close_path()
            ctx.fill()

        def paint_sidebar(ctx, fg):
            _ink(ctx, fg, 1.5)
            ctx.rectangle(2.5, 3.5, 13, 11)
            ctx.stroke()
            ctx.set_source_rgba(*fg)
            ctx.rectangle(2.5, 3.5, 4.5, 11)
            ctx.fill()

        def paint_pen(ctx, _fg):
            ink = (*ed.pen_color, 1.0)
            _ink(ctx, ink, 2.6)
            _path(ctx, [(5.6, 12.4), (13.0, 5.0)])
            ctx.stroke()
            _ink(ctx, ink, 1.4)
            _path(ctx, [(3.6, 14.4), (5.6, 12.4)])
            ctx.stroke()
            ctx.set_source_rgba(*ink)
            ctx.arc(5.6, 12.4, 2.0, 0, 2 * math.pi)
            ctx.fill()

        def paint_highlighter(ctx, fg):
            _ink(ctx, fg, 1.5)
            _path(ctx, [(6.2, 9.3), (10.6, 3.6), (13.6, 6.0), (9.2, 11.7)])
            ctx.close_path()
            ctx.stroke()
            _path(ctx, [(6.2, 9.3), (5.0, 12.2), (9.2, 11.7)])
            ctx.close_path()
            ctx.set_source_rgba(*fg)
            ctx.fill()
            ctx.set_source_rgb(0.97, 0.85, 0.30)
            ctx.rectangle(3.0, 14.4, 12.0, 2.4)
            ctx.fill()

        def paint_text(ctx, fg):
            _ink(ctx, fg, 1.6)
            _path(ctx, [(4.5, 4.2), (13.5, 4.2)])
            ctx.stroke()
            _path(ctx, [(4.5, 4.2), (4.5, 5.2)])
            ctx.stroke()
            _path(ctx, [(13.5, 4.2), (13.5, 5.2)])
            ctx.stroke()
            _path(ctx, [(9, 4.2), (9, 14.6)])
            ctx.stroke()

        def paint_note(ctx, fg):
            _ink(ctx, fg, 1.5)
            r = 2.5
            x0, y0, x1, y1 = 2.5, 3.0, 15.5, 11.5
            ctx.new_sub_path()
            ctx.arc(x1 - r, y0 + r, r, -math.pi / 2, 0)
            ctx.arc(x1 - r, y1 - r, r, 0, math.pi / 2)
            ctx.arc(x0 + r, y1 - r, r, math.pi / 2, math.pi)
            ctx.arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi)
            ctx.close_path()
            ctx.stroke()
            ctx.set_source_rgba(*fg)
            _path(ctx, [(6.2, 11.9), (5.4, 15.4), (9.6, 11.9)])
            ctx.close_path()
            ctx.fill()

        def paint_sign(ctx, fg):
            _ink(ctx, fg, 1.7)
            ctx.move_to(3.2, 12.0)
            ctx.curve_to(5.8, 3.6, 8.2, 4.4, 7.4, 8.8)
            ctx.curve_to(6.8, 12.2, 9.4, 12.0, 10.8, 9.2)
            ctx.stroke()
            _ink(ctx, fg, 1.3)
            _path(ctx, [(3.0, 15.2), (15.0, 15.2)])
            ctx.stroke()

        def paint_open(ctx, fg):
            _ink(ctx, fg, 1.5)
            ctx.move_to(2.5, 6.2)
            ctx.line_to(2.5, 14.6)
            ctx.line_to(15.5, 14.6)
            ctx.line_to(15.5, 7.2)
            ctx.line_to(8.6, 7.2)
            ctx.line_to(7.0, 5.0)
            ctx.line_to(2.5, 5.0)
            ctx.close_path()
            ctx.stroke()

        def paint_spark(ctx, fg):
            # Four-point spark: "ask the agent".
            ctx.set_source_rgba(*fg)
            _path(ctx, [(9, 2.2), (10.7, 7.3), (15.8, 9), (10.7, 10.7),
                        (9, 15.8), (7.3, 10.7), (2.2, 9), (7.3, 7.3)])
            ctx.close_path()
            ctx.fill()

        def paint_share(ctx, fg):
            # Arrow rising out of a tray — the universal "send it somewhere".
            _ink(ctx, fg, 1.6)
            _path(ctx, [(4.0, 9.5), (4.0, 14.5), (14.0, 14.5), (14.0, 9.5)])
            ctx.stroke()
            _path(ctx, [(9.0, 3.0), (9.0, 11.0)])
            ctx.stroke()
            _path(ctx, [(6.2, 5.6), (9.0, 2.8), (11.8, 5.6)])
            ctx.stroke()

        def paint_check(ctx, _fg):
            _ink(ctx, (*CHECK_COLOR, 1.0), 2.3)
            _path(ctx, [(3.8, 9.8), (7.4, 13.4), (14.2, 4.6)])
            ctx.stroke()

        def paint_cross(ctx, _fg):
            _ink(ctx, (*CROSS_COLOR, 1.0), 2.3)
            _path(ctx, [(4.8, 4.8), (13.2, 13.2)])
            ctx.stroke()
            _path(ctx, [(13.2, 4.8), (4.8, 13.2)])
            ctx.stroke()

        def paint_redact(ctx, fg):
            _ink(ctx, fg, 1.5)
            ctx.rectangle(3.5, 4.0, 11.0, 10.5)
            ctx.stroke()
            ctx.set_source_rgba(0, 0, 0, 0.75)
            ctx.rectangle(4.5, 5.0, 9.0, 8.5)
            ctx.fill()

        def paint_shapes(ctx, fg):
            _ink(ctx, fg, 1.5)
            ctx.rectangle(3.0, 4.0, 11.5, 9.5)
            ctx.stroke()
            ctx.move_to(4.5, 13.5)
            ctx.line_to(13.5, 4.5)
            ctx.stroke()

        def paint_crop(ctx, fg):
            _ink(ctx, fg, 1.6)
            ctx.rectangle(3.0, 3.5, 15.0, 12.5)
            ctx.stroke()
            for hx, hy in ((3, 3.5), (15, 3.5), (3, 12.5), (15, 12.5)):
                ctx.rectangle(hx - 1.2, hy - 1.2, 2.4, 2.4)
                ctx.stroke()

        def make_tool(name, tip, painter):
            nonlocal first_btn
            btn = Gtk.ToggleButton()
            btn.add_css_class("tool-slim")
            btn.add_css_class("tool-icon")
            btn.set_child(icon_widget(painter))
            btn.set_tooltip_text(tip)
            if first_btn is None:
                first_btn = btn
            else:
                btn.set_group(first_btn)
            btn.connect("toggled", lambda b: b.get_active() and setattr(ed, "tool", name))
            tools[name] = btn
            return btn

        def rail_sep():
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            toolbar.append(sep)

        side_toggle = Gtk.ToggleButton()
        side_toggle.add_css_class("tool-slim")
        side_toggle.add_css_class("tool-icon")
        side_toggle.set_child(icon_widget(paint_sidebar))
        side_toggle.set_tooltip_text("Thumbnails sidebar (F9)")

        open_btn = Gtk.Button()
        open_btn.add_css_class("tool-slim")
        open_btn.add_css_class("tool-icon")
        open_btn.set_child(icon_widget(paint_open))
        open_btn.set_tooltip_text("Open PDF (Ctrl+O)")

        make_tool("select", "Select — click an item, drag to move (Esc deselects, Del removes)",
                  paint_pointer)
        # Pen and its color share one button: the pen nib is drawn in the
        # current ink color; tapping the pen while it is ALREADY the active
        # tool opens the palette. One slot, no hover needed — touch-friendly.
        pen_btn = make_tool("pen", "Pen — freehand ink (tap again for colors)", paint_pen)
        pen_icon = pen_btn.get_child()

        def show_pen_color():
            pen_icon.queue_draw()

        color_pop = Gtk.Popover()
        color_pop.set_parent(pen_btn)
        color_box = Gtk.Box(spacing=2)
        for cname, rgb_t in PEN_COLORS:
            cb = Gtk.Button()
            clabel = Gtk.Label()
            hexc = "#%02x%02x%02x" % tuple(int(c * 255) for c in rgb_t)
            clabel.set_markup(f'<span foreground="{hexc}" size="x-large">●</span>')
            cb.set_child(clabel)
            cb.set_tooltip_text(cname)
            cb.add_css_class("flat")

            def pick(_b, chosen=rgb_t):
                ed.pen_color = chosen
                show_pen_color()
                popover_try_popdown(color_pop)
                set_tool("pen")

            cb.connect("clicked", pick)
            color_box.append(cb)
        color_pop.set_child(color_box)
        show_pen_color()

        pen_state = {"just_activated": False}
        pen_btn.connect(
            "toggled",
            lambda b: b.get_active() and pen_state.__setitem__("just_activated", True),
        )

        def on_pen_clicked(_b):
            # First tap arms the pen (toggled fired); a tap on the already-
            # active pen opens the palette instead.
            if pen_state["just_activated"]:
                pen_state["just_activated"] = False
            elif ed.tool == "pen":
                popover_try_popup(color_pop)

        pen_btn.connect("clicked", on_pen_clicked)

        make_tool("highlight", "Highlighter — drag across a region", paint_highlighter)
        make_tool("text", "Text — click to type onto the page", paint_text)
        make_tool("note", "Sticky note — click to leave a comment", paint_note)
        sign_btn = make_tool(
            "sign",
            "Sign — pick a saved signature and drag it onto the page",
            paint_sign,
        )
        sign_pop = Gtk.Popover()
        sign_pop.set_parent(sign_btn)
        sign_pop.set_position(Gtk.PositionType.LEFT)
        popover_try_set_autohide(sign_pop, True)
        sign_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sign_box.set_margin_top(10)
        sign_box.set_margin_bottom(10)
        sign_box.set_margin_start(10)
        sign_box.set_margin_end(10)
        sign_pop.set_child(sign_box)

        def _flush_deferred_render() -> None:
            if pinch_state["pending_commit"]:
                pinch_state["pending_commit"] = False
                if pinch_state["commit_id"]:
                    GLib.source_remove(pinch_state["commit_id"])
                pinch_state["commit_id"] = GLib.idle_add(commit_pinch_zoom)
                return
            if pinch_state["deferred_render"]:
                pinch_state["deferred_render"] = False
                anchor = pinch_state["deferred_anchor"]
                pinch_state["deferred_anchor"] = None
                GLib.idle_add(lambda: (render_page(v_anchor=anchor), False)[1])

        def _clear_box(box):
            child = box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                box.remove(child)
                child = nxt

        def _sig_thumb_size() -> tuple[int, int]:
            return (168, 72)

        def rebuild_sign_popover():
            _clear_box(sign_box)
            names = sig_store.list_names()
            if not names:
                empty = Gtk.Label(label="No signatures yet — record one")
                empty.add_css_class("dim-label")
                empty.set_wrap(True)
                empty.set_xalign(0)
                sign_box.append(empty)
            else:
                gallery = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                gallery.set_valign(Gtk.Align.START)
                tw, th = _sig_thumb_size()
                for name in names:
                    thumb = Gtk.DrawingArea()
                    thumb.set_content_width(tw)
                    thumb.set_content_height(th)
                    thumb.set_can_target(False)
                    surface = ed._ensure_sig(name)

                    def paint_thumb(_a, ctx, w, h, surf=surface, n=name):
                        ctx.set_source_rgb(1, 1, 1)
                        ctx.paint()
                        if n == ed.sig_name:
                            ctx.set_source_rgb(*SELECT_COLOR)
                            ctx.set_line_width(1.5)
                            ctx.rectangle(0.75, 0.75, w - 1.5, h - 1.5)
                            ctx.stroke()
                        if surf is None:
                            return
                        scale = min(
                            (w - 8) / max(surf.get_width(), 1),
                            (h - 8) / max(surf.get_height(), 1),
                        )
                        dw, dh = surf.get_width() * scale, surf.get_height() * scale
                        ctx.translate((w - dw) / 2, (h - dh) / 2)
                        ctx.scale(scale, scale)
                        ctx.set_source_surface(surf, 0, 0)
                        ctx.paint()

                    thumb.set_draw_func(paint_thumb)
                    label = Gtk.Label(label=name)
                    label.set_can_target(False)
                    label.set_wrap(True)
                    label.set_max_width_chars(16)
                    label.set_justify(Gtk.Justification.CENTER)
                    label.set_xalign(0.5)
                    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                    card.add_css_class("omapdf-sig-card")
                    card.set_tooltip_text(f"Drag {name} onto the page")
                    card.append(thumb)
                    card.append(label)

                    def on_pick(_g, _n, _x, _y, n=name):
                        if sig_drag["dragging"]:
                            return
                        ed.sig_name = n
                        set_tool("sign")
                        gallery_child = gallery.get_first_child()
                        while gallery_child is not None:
                            gallery_child.queue_draw()
                            gallery_child = gallery_child.get_next_sibling()

                    pick = Gtk.GestureClick()
                    pick.connect("released", on_pick)
                    card.add_controller(pick)

                    sig_move = Gtk.GestureDrag()

                    def on_sig_drag_begin(_g, _x, _y, n=name):
                        # Do not toggle autohide here: gtk_popover_set_autohide
                        # unrealizes, and a realized-but-nativeless popover
                        # then SIGSEGVs. Keep the gallery mapped; place on
                        # click or idle drop instead.
                        sig_drag["active"] = True
                        sig_drag["dragging"] = False
                        ed.sig_name = n
                        set_tool("sign")

                    def on_sig_drag_update(_g, dx, dy):
                        if math.hypot(dx, dy) > 8:
                            sig_drag["dragging"] = True

                    def on_sig_drag_end(_g, dx, dy, n=name):
                        dragging = sig_drag["dragging"] and math.hypot(dx, dy) > 8
                        sig_drag["active"] = False
                        sig_drag["dragging"] = False

                        def finish():
                            if dragging:
                                xy = _pointer_in_widget(area)
                                if xy is not None:
                                    ax, ay = xy
                                    aw = area.get_width() or 0
                                    ah = area.get_height() or 0
                                    if 0 <= ax <= aw and 0 <= ay <= ah:
                                        px, py = to_page_point(ax, ay)
                                        place_named_signature(n, px, py)
                                        sig_drag["just_dropped"] = True

                                        def clear_drop_flag():
                                            sig_drag["just_dropped"] = False
                                            return False

                                        GLib.timeout_add(80, clear_drop_flag)
                            popover_try_popdown(sign_pop)
                            _flush_deferred_render()
                            return False

                        GLib.idle_add(finish)

                    sig_move.connect("drag-begin", on_sig_drag_begin)
                    sig_move.connect("drag-update", on_sig_drag_update)
                    sig_move.connect("drag-end", on_sig_drag_end)
                    card.add_controller(sig_move)
                    gallery.append(card)
                gallery_scroll = Gtk.ScrolledWindow()
                gallery_scroll.set_policy(
                    Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER
                )
                gallery_scroll.set_propagate_natural_width(True)
                gallery_scroll.set_propagate_natural_height(True)
                gallery_scroll.set_max_content_width(520)
                gallery_scroll.set_child(gallery)
                sign_box.append(gallery_scroll)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.add_css_class("omapdf-sig-actions")
            rec = Gtk.Button(label="Record new")
            rec.add_css_class("suggested-action")

            def on_record(_b):
                popover_try_popdown(sign_pop)
                record_signature(next_sig_name())

            rec.connect("clicked", on_record)
            actions.append(rec)
            if names:
                rer = Gtk.Button(label=f"Re-record '{ed.sig_name}'")

                def on_rerecord(_b, n=ed.sig_name):
                    popover_try_popdown(sign_pop)
                    record_signature(n)

                rer.connect("clicked", on_rerecord)
                actions.append(rer)
            sign_box.append(actions)
            hint = Gtk.Label(
                label="Drag a signature onto the page, or click the page to place it.",
                wrap=True,
                xalign=0,
            )
            hint.add_css_class("dim-label")
            sign_box.append(hint)

        sig_ui["rebuild"] = rebuild_sign_popover
        sign_state = {"just_activated": False}
        sign_btn.connect(
            "toggled",
            lambda b: b.get_active() and sign_state.__setitem__("just_activated", True),
        )

        def on_sign_clicked(_b):
            sign_state["just_activated"] = False
            rebuild_sign_popover()
            # Defer past this click: popup() during the press is dismissed by
            # autohide on the same release (same pattern as saved-annot pops).
            GLib.idle_add(lambda: (popover_try_popup(sign_pop), False)[1])

        sign_btn.connect("clicked", on_sign_clicked)
        make_tool("check", "Checkmark stamp — places a ✓ on the page", paint_check)
        make_tool("cross", "Cross-out stamp — places an ✕ on the page", paint_cross)
        shape_btn = make_tool(
            "shape",
            "Shapes — drag line, arrow, rect, or oval (tap again to pick)",
            paint_shapes,
        )
        shape_pop = Gtk.Popover()
        shape_pop.set_parent(shape_btn)
        shape_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        shape_box.set_margin_top(8)
        shape_box.set_margin_bottom(8)
        shape_box.set_margin_start(8)
        shape_box.set_margin_end(8)
        for label, kind in (
            ("Line", "line"),
            ("Arrow", "arrow"),
            ("Rectangle", "rect"),
            ("Oval", "oval"),
        ):
            sb = Gtk.Button(label=label)
            sb.add_css_class("flat")
            sb.set_halign(Gtk.Align.FILL)
            sb.get_child().set_halign(Gtk.Align.START)

            def pick_shape(_b, chosen=kind):
                ed.shape_kind = chosen
                popover_try_popdown(shape_pop)
                set_tool("shape")

            sb.connect("clicked", pick_shape)
            shape_box.append(sb)
        shape_hint = Gtk.Label(
            label="Uses the current pen color. Ghosts apply on Save.",
            wrap=True,
            xalign=0,
        )
        shape_hint.add_css_class("dim-label")
        shape_box.append(shape_hint)
        shape_pop.set_child(shape_box)
        shape_state = {"just_activated": False}
        shape_btn.connect(
            "toggled",
            lambda b: b.get_active() and shape_state.__setitem__("just_activated", True),
        )

        def on_shape_clicked(_b):
            if shape_state["just_activated"]:
                shape_state["just_activated"] = False
            elif ed.tool == "shape":
                popover_try_popup(shape_pop)

        shape_btn.connect("clicked", on_shape_clicked)
        make_tool(
            "crop",
            "Crop page — drag the region to keep (CropBox; applies on Save)",
            paint_crop,
        )
        redact_btn = make_tool(
            "redact",
            "Redact — drag over text (tap again for free-rectangle mode)",
            paint_redact,
        )
        redact_pop = Gtk.Popover()
        redact_pop.set_parent(redact_btn)
        redact_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        redact_box.set_margin_top(8)
        redact_box.set_margin_bottom(8)
        redact_box.set_margin_start(8)
        redact_box.set_margin_end(8)
        free_rect_sw = Gtk.Switch(active=False, halign=Gtk.Align.END)
        free_row = Gtk.Box(spacing=8)
        free_row.append(Gtk.Label(label="Free rectangle (images, handwriting)", xalign=0))
        free_row.append(free_rect_sw)
        redact_box.append(free_row)
        redact_hint = Gtk.Label(
            label="Ghosts are not applied until Save.",
            wrap=True,
            xalign=0,
        )
        redact_hint.add_css_class("dim-label")
        redact_box.append(redact_hint)
        redact_pop.set_child(redact_box)
        free_rect_sw.connect(
            "notify::active",
            lambda _sw, _pspec: setattr(ed, "redact_free_rect", free_rect_sw.get_active()),
        )
        redact_state = {"just_activated": False}
        redact_btn.connect(
            "toggled",
            lambda b: b.get_active() and redact_state.__setitem__("just_activated", True),
        )

        def on_redact_clicked(_b):
            if redact_state["just_activated"]:
                redact_state["just_activated"] = False
            elif ed.tool == "redact":
                popover_try_popup(redact_pop)

        redact_btn.connect("clicked", on_redact_clicked)
        tools["select"].set_active(True)

        page_label = Gtk.Label()
        prev_b = Gtk.Button(label="‹")
        next_b = Gtk.Button(label="›")
        prev_b.add_css_class("flat")
        next_b.add_css_class("flat")
        for nav_b in (prev_b, next_b):
            nav_b.add_css_class("tool-slim")
            nav_b.add_css_class("tool-icon")
        prev_b.set_tooltip_text("Previous page (PgUp)")
        next_b.set_tooltip_text("Next page (PgDn)")

        def goto_page(n):
            if ed.page_count() == 0:
                return
            n = max(0, min(ed.page_count() - 1, n))
            if n != ed.page_no:
                ed.page_no = n
                ed.selected = None
                render_page()
                # A fresh page starts at its top, wherever the old one was.
                adj = scroller.get_vadjustment()
                GLib.idle_add(lambda: (adj.set_value(0), False)[1])

        def go(delta):
            goto_page(ed.page_no + delta)

        prev_b.connect("clicked", lambda _b: go(-1))
        next_b.connect("clicked", lambda _b: go(1))

        # The page indicator is a button: click it (or Ctrl+G) to jump to a page.
        page_btn = Gtk.MenuButton()
        page_btn.add_css_class("flat")
        page_btn.add_css_class("tool-slim")
        page_label.add_css_class("page-indicator")
        page_btn.set_child(page_label)
        page_btn.set_tooltip_text("Go to page (Ctrl+G)")
        goto_pop = Gtk.Popover()
        goto_entry = Gtk.Entry()
        goto_entry.set_placeholder_text("Page #")
        goto_entry.set_width_chars(8)
        goto_pop.set_child(goto_entry)
        page_btn.set_popover(goto_pop)

        def on_goto(_e):
            try:
                n = int(goto_entry.get_text().strip())
            except ValueError:
                return
            popover_try_popdown(goto_pop)
            goto_entry.set_text("")
            goto_page(n - 1)

        goto_entry.connect("activate", on_goto)

        nav_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        nav_btns.add_css_class("omapdf-rail-row")
        nav_btns.set_halign(Gtk.Align.CENTER)
        nav_btns.append(prev_b)
        nav_btns.append(next_b)
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nav.add_css_class("omapdf-rail-nav")
        nav.set_halign(Gtk.Align.CENTER)
        nav.append(page_btn)
        nav.append(nav_btns)

        def update_nav():
            # Single-page documents get no pager at all; otherwise the
            # impossible direction is greyed out rather than hidden, so the
            # control keeps a stable shape.
            nav.set_visible(ed.page_count() > 1)
            prev_b.set_sensitive(ed.page_no > 0)
            next_b.set_sensitive(ed.page_no < ed.page_count() - 1)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("omapdf-ghost")
        undo_b = Gtk.Button()
        undo_b.set_child(icon_widget(paint_undo_glyph))
        redo_b = Gtk.Button()
        redo_b.set_child(icon_widget(paint_redo_glyph))
        undo_b.set_tooltip_text("Undo — steps back through edits AND saves (Ctrl+Z)")
        redo_b.set_tooltip_text("Redo (Ctrl+Shift+Z)")
        for icon_b in (undo_b, redo_b):
            icon_b.add_css_class("tool-slim")
            icon_b.add_css_class("tool-icon")

        def do_undo():
            mark_self_write()
            was_save = ed.undo()
            sidebar_api["refresh"]()
            if was_save:
                render_page()
                toast("Save reverted — the items are editable ghosts again")
            else:
                render_page()
                toast("Undone")
            area.queue_draw()
            refresh_title()

        def do_redo():
            mark_self_write()
            was_save = ed.redo()
            if was_save:
                render_page()
                toast("Save re-applied")
            else:
                sidebar_api["refresh"]()
                render_page()
            area.queue_draw()
            refresh_title()

        undo_b.connect("clicked", lambda _b: do_undo())
        redo_b.connect("clicked", lambda _b: do_redo())
        zoom_dot = Gtk.Label()
        zoom_dot.add_css_class("zoom-indicator")
        zoom_btn = Gtk.MenuButton()
        zoom_btn.add_css_class("tool-slim")
        zoom_btn.set_child(zoom_dot)
        zoom_btn.set_tooltip_text("Zoom (pinch, Ctrl+scroll; Ctrl+0 fits page)")
        zoom_pop = Gtk.Popover()
        zoom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        def set_zoom(pct):
            anchor = capture_v_anchor()
            ed.zoom_pct = pct
            popover_try_popdown(zoom_pop)
            render_page(v_anchor=anchor)

        for zlabel, zval in [("Fit page", None), ("50%", 50.0), ("75%", 75.0),
                             ("100%", 100.0), ("125%", 125.0), ("150%", 150.0),
                             ("200%", 200.0)]:
            zb = Gtk.Button(label=zlabel)
            zb.add_css_class("flat")
            zb.connect("clicked", lambda _b, v=zval: set_zoom(v))
            zoom_box.append(zb)
        zoom_pop.set_child(zoom_box)
        zoom_btn.set_popover(zoom_pop)

        search_btn = Gtk.ToggleButton()
        search_btn.set_child(Gtk.Image.new_from_icon_name("system-search-symbolic"))
        search_btn.set_tooltip_text("Search (Ctrl+F)")
        search_btn.add_css_class("tool-slim")
        search_btn.add_css_class("tool-icon")

        # -- ask the agent (Omarchy's native default agent) ---------------

        ask_btn = Gtk.MenuButton()
        ask_btn.set_child(icon_widget(paint_spark))
        ask_btn.set_tooltip_text("Ask your agent about this document")
        ask_btn.add_css_class("tool-slim")
        ask_btn.add_css_class("tool-icon")
        ask_pop = Gtk.Popover()
        # Shift left of the spark button so the popover stays inside the
        # window instead of overhanging the neighboring tile.
        ask_pop.set_has_arrow(False)
        ask_pop.set_offset(-110, 4)
        ask_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ask_box.set_margin_top(8)
        ask_box.set_margin_bottom(8)
        ask_box.set_margin_start(8)
        ask_box.set_margin_end(8)
        ask_entry = Gtk.Entry()
        ask_entry.set_placeholder_text("Ask about this document…")
        ask_entry.set_width_chars(26)
        ask_entry.set_hexpand(True)
        ask_send = Gtk.Button(label="Ask")
        ask_send.add_css_class("suggested-action")
        ask_row = Gtk.Box(spacing=6)
        ask_row.append(ask_entry)
        ask_row.append(ask_send)
        ask_hint = Gtk.Label(label="Opens your default agent with this file attached")
        ask_hint.add_css_class("dim-label")
        ask_hint.set_halign(Gtk.Align.START)
        ask_box.append(ask_row)
        ask_box.append(ask_hint)
        ask_pop.set_child(ask_box)
        ask_btn.set_popover(ask_pop)

        def on_ask(_e):
            import subprocess as sp

            if not ed.has_document():
                toast("Open a PDF first (Ctrl+O)")
                return
            question = ask_entry.get_text().strip() or "Review this document for me."
            popover_try_popdown(ask_pop)
            ask_entry.set_text("")
            prompt = (
                f"{question}\n\nThe document is the PDF at \"{ed.path}\" — "
                "use omepreview to read or mark it up. It is open in the omepreview "
                "editor, which auto-reloads when you save changes to it."
            )
            try:
                sp.Popen(["omarchy-agent-prompt", prompt], start_new_session=True)
                toast("Sent to your agent — a window is opening")
            except FileNotFoundError:
                toast("omarchy agent launcher not found on this system")

        ask_entry.connect("activate", on_ask)
        ask_send.connect("clicked", on_ask)

        # -- share menu ---------------------------------------------------

        import shutil as _shutil

        share_btn = Gtk.MenuButton()
        share_btn.set_child(icon_widget(paint_share))
        share_btn.set_tooltip_text("Share — send this PDF somewhere")
        share_btn.add_css_class("tool-slim")
        share_btn.add_css_class("tool-icon")
        share_pop = Gtk.Popover()
        share_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        flatten_check = Gtk.CheckButton(label="Flatten copy first")
        flatten_check.set_tooltip_text(
            "Bake annotations and form fields in, so every viewer shows them"
        )

        def share_target():
            if not ed.has_document():
                toast("Open a PDF first (Ctrl+O)")
                return None
            reason = unsaved_export_reason(
                ed.pending, ed.page_preview.has_changes(), action="sharing"
            )
            if reason:
                toast(reason)
                return None
            flatten = flatten_check.get_active()
            try:
                dest = share_target_path(
                    ed.path,
                    flatten=flatten,
                    flatten_fn=engine.flatten if flatten else None,
                )
            except Exception as exc:
                toast(f"Share failed: {exc}")
                return None
            if flatten:
                toast(f"Sharing flattened copy: {dest.name}")
            return str(dest)

        def share_action(fn):
            def go(_b):
                popover_try_popdown(share_pop)
                path = share_target()
                if path:
                    probe = os.environ.get("OMEPREVIEW_SHARE_PROBE")
                    if probe:
                        Path(probe).write_text(str(Path(path).resolve()) + "\n")
                    try:
                        fn(path)
                    except Exception as exc:
                        toast(f"Share failed: {exc}")
            return go

        def add_share(label, fn, tooltip=None):
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.set_halign(Gtk.Align.FILL)
            b.get_child().set_halign(Gtk.Align.START)
            if tooltip:
                b.set_tooltip_text(tooltip)
            b.connect("clicked", share_action(fn))
            share_box.append(b)

        def copy_file(path):
            import subprocess as sp

            uri = Path(path).resolve().as_uri() + "\n"
            sp.run(["wl-copy", "-t", "text/uri-list"], input=uri.encode(), check=True)
            toast(f"Copied {Path(path).name} — paste it into a chat, email, or folder")

        def zip_copy(path):
            zpath = zip_file_private(path)
            copy_file(zpath)

        def spawn(cmd):
            import subprocess as sp

            sp.Popen(cmd, start_new_session=True)

        add_share("Email…", lambda p: spawn(["xdg-email", "--attach", p]),
                  "Open your mail client with the PDF attached")
        if _shutil.which("localsend"):
            add_share("LocalSend…", lambda p: spawn(["localsend", p]),
                      "Send to a nearby device")
        if _shutil.which("wl-copy"):
            add_share("Copy file", copy_file,
                      "Puts the file itself on the clipboard")
            add_share("Zip & copy", zip_copy,
                      "Zips the PDF and puts the .zip on the clipboard")
        add_share("Show in folder", lambda p: spawn(["xdg-open", str(Path(p).parent)]))
        share_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        share_box.append(flatten_check)
        share_pop.set_child(share_box)
        share_btn.set_popover(share_pop)

        def refresh_save_style():
            dirty = ed.pending or ed.page_preview.has_changes()
            if dirty:
                save_btn.remove_css_class("omapdf-ghost")
                save_btn.add_css_class("omapdf-ink")
            else:
                save_btn.remove_css_class("omapdf-ink")
                save_btn.add_css_class("omapdf-ghost")

        save_style_hook["fn"] = refresh_save_style

        for w in (open_btn, side_toggle, search_btn, ask_btn):
            toolbar.append(w)
        rail_sep()
        for w in (
            tools["select"], pen_btn, tools["highlight"], tools["text"], tools["note"],
            tools["sign"], tools["check"], tools["cross"], shape_btn, tools["crop"],
            redact_btn,
        ):
            toolbar.append(w)
        rail_sep()
        undo_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        undo_row.add_css_class("omapdf-rail-row")
        undo_row.set_halign(Gtk.Align.CENTER)
        undo_row.append(undo_b)
        undo_row.append(redo_b)
        toolbar.append(zoom_btn)
        toolbar.append(undo_row)
        toolbar.append(nav)
        bottom_spacer = Gtk.Box()
        bottom_spacer.set_vexpand(True)
        toolbar.append(bottom_spacer)
        for w in (share_btn, save_btn):
            toolbar.append(w)

        toolbar.set_vexpand(True)
        toolbar_wrap = Gtk.Box()
        toolbar_wrap.set_halign(Gtk.Align.END)
        toolbar_wrap.set_valign(Gtk.Align.FILL)
        toolbar_wrap.set_can_target(True)
        toolbar_wrap.append(toolbar)

        def celebrate_save():
            label = save_btn.get_child()
            save_btn.set_sensitive(False)
            save_btn.remove_css_class("omapdf-ghost")
            save_btn.remove_css_class("omapdf-ink")
            label.set_text("Saved")

            def restore():
                save_btn.set_sensitive(True)
                label.set_text("Save")
                refresh_save_style()
                return False

            GLib.timeout_add(1200, restore)

        # Toast: bottom-centre ink strip; floats over the page without layout jump.
        toast_label = Gtk.Label()
        toast_label.add_css_class("toast-banner")
        toast_revealer = Gtk.Revealer()
        toast_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        toast_revealer.set_transition_duration(220)
        toast_revealer.set_halign(Gtk.Align.CENTER)
        toast_revealer.set_valign(Gtk.Align.END)
        toast_state = {"timeout": 0}
        # Suppresses the disk watcher while omepreview itself writes the file.
        write_guard = {"until": 0}

        def mark_self_write():
            write_guard["until"] = GLib.get_monotonic_time() + 2_000_000

        def toast(msg):
            if toast_state["timeout"]:
                GLib.source_remove(toast_state["timeout"])
                toast_state["timeout"] = 0
            if not msg:
                toast_revealer.set_reveal_child(False)
                return
            toast_label.set_text(msg)
            toast_revealer.set_reveal_child(True)

            def hide():
                toast_state["timeout"] = 0
                toast_revealer.set_reveal_child(False)
                return False

            toast_state["timeout"] = GLib.timeout_add(5000, hide)

        def load_document(path: str):
            try:
                ed.open_path(path)
            except Exception as exc:
                toast(f"Could not open {Path(path).name}: {exc}")
                return
            file_watch["retarget"]()
            sidebar_api["refresh"]()
            if ed.page_count() > 1:
                side_toggle.set_active(True)
            render_page()
            refresh_title()
            toast(f"Opened {Path(path).name}")

        def choose_open(_b=None):
            dialog = pdf_open_dialog()

            def on_open(_d, result):
                try:
                    chosen = dialog.open_finish(result)
                except GLib.Error:
                    return
                if chosen is None:
                    return
                path = chosen.get_path()
                if path:
                    load_document(path)

            dialog.open(win, None, on_open)

        open_hook["fn"] = choose_open
        open_btn.connect("clicked", choose_open)

        empty_cue = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty_cue.set_halign(Gtk.Align.CENTER)
        empty_cue.set_valign(Gtk.Align.CENTER)
        empty_cue.set_can_target(True)
        empty_title = Gtk.Label(label="No document")
        empty_title.add_css_class("dim-label")
        empty_open_btn = Gtk.Button(label="Open PDF")
        empty_open_btn.add_css_class("suggested-action")
        empty_open_btn.set_tooltip_text("Open a PDF (Ctrl+O)")
        empty_hint = Gtk.Label(label="or press Ctrl+O")
        empty_hint.add_css_class("dim-label")
        empty_cue.append(empty_title)
        empty_cue.append(empty_open_btn)
        empty_cue.append(empty_hint)
        empty_open_btn.connect("clicked", choose_open)

        def refresh_empty_state():
            empty_cue.set_visible(not ed.has_document())

        empty_hook["fn"] = refresh_empty_state
        refresh_empty_state()

        def show_redact_intro(on_done):
            if ed.redact_modal_shown:
                on_done()
                return
            ed.redact_modal_shown = True
            dialog = Gtk.Window(transient_for=win, modal=True, title="Redaction")
            dialog.set_default_size(440, -1)
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            vbox.set_margin_top(16)
            vbox.set_margin_bottom(16)
            vbox.set_margin_start(16)
            vbox.set_margin_end(16)
            vbox.append(Gtk.Label(
                label=(
                    "Redactions are translucent ghosts until Save.\n\n"
                    "Save writes a new file by default (*_redacted.pdf). "
                    "The original stays untouched."
                ),
                wrap=True,
                xalign=0,
            ))
            copy_sw = Gtk.Switch(active=True, halign=Gtk.Align.END)
            copy_row = Gtk.Box(spacing=8)
            copy_row.append(Gtk.Label(
                label="Save as *_redacted.pdf (recommended)", hexpand=True, xalign=0,
            ))
            copy_row.append(copy_sw)
            vbox.append(copy_row)
            confirm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            confirm_box.append(Gtk.Label(
                label="Type REDACT to overwrite the original:", xalign=0,
            ))
            confirm_entry = Gtk.Entry()
            confirm_box.append(confirm_entry)
            confirm_box.set_visible(False)
            vbox.append(confirm_box)
            btn_row = Gtk.Box(spacing=8, halign=Gtk.Align.END)
            cancel_btn = Gtk.Button(label="Cancel")
            ok_btn = Gtk.Button(label="Continue")
            ok_btn.add_css_class("suggested-action")
            btn_row.append(cancel_btn)
            btn_row.append(ok_btn)
            vbox.append(btn_row)
            dialog.set_child(vbox)

            def validate(_obj=None):
                inplace = not copy_sw.get_active()
                confirm_box.set_visible(inplace)
                ok_btn.set_sensitive(
                    not inplace or confirm_entry.get_text().strip() == "REDACT"
                )

            copy_sw.connect("notify::active", validate)
            confirm_entry.connect("changed", validate)
            validate()

            def accept():
                ed.redact_save_as_copy = copy_sw.get_active()
                dialog.close()
                on_done()

            def cancel():
                if ed.undo_stack and ed.undo_stack[-1]["kind"] == "pending":
                    ed.undo()
                dialog.close()

            ok_btn.connect("clicked", lambda _b: accept())
            cancel_btn.connect("clicked", lambda _b: cancel())
            dialog.present()

        def after_redact_drag():
            def done():
                toast("Redaction not applied until Save")
            show_redact_intro(done)

        finish_redact_drag["fn"] = after_redact_drag

        def on_save(_b):
            if not ed.has_document():
                toast("Open a PDF first (Ctrl+O)")
                return
            if not ed.pending and not ed.page_preview.has_changes():
                toast("Nothing to save")
                return
            mark_self_write()
            try:
                result = ed.save_pending()
            except Exception as exc:
                toast(f"Save failed: {exc}")
                render_page()
                return
            file_watch["retarget"]()
            render_page()
            sidebar_api["refresh"]()
            refresh_title()
            if result["redacted_copy"]:
                toast(
                    f"Saved {result['saved']} change(s) to "
                    f"{Path(result['path']).name} — original unchanged"
                )
            else:
                toast(f"Saved {result['saved']} change(s) — Ctrl+Z reverts the save")
            celebrate_save()

        save_btn.connect("clicked", on_save)

        keys = Gtk.EventControllerKey()

        def on_key(_c, keyval, _code, state):
            # Runs in capture phase so arrows reach us before the scroll
            # view eats them — but typing in any entry must stay untouched.
            focus = win.get_focus()
            if focus is not None and isinstance(focus, (Gtk.Text, Gtk.Editable)):
                return False
            ctrl = state & Gdk.ModifierType.CONTROL_MASK
            shift = state & Gdk.ModifierType.SHIFT_MASK
            step = 10.0 if shift else 2.0
            if ctrl and keyval in (Gdk.KEY_o, Gdk.KEY_O):
                open_hook["fn"]()
            elif ctrl and keyval == Gdk.KEY_s:
                on_save(None)
            elif ctrl and keyval == Gdk.KEY_f:
                search_btn.set_active(True)
            elif ctrl and keyval in (Gdk.KEY_plus, Gdk.KEY_equal):
                current = ed.zoom_pct if ed.zoom_pct else ed.zoom / (96 / 72) * 100
                ed.zoom_pct = min(400.0, current + 25)
                render_page(v_anchor=capture_v_anchor())
            elif ctrl and keyval == Gdk.KEY_minus:
                current = ed.zoom_pct if ed.zoom_pct else ed.zoom / (96 / 72) * 100
                ed.zoom_pct = max(25.0, current - 25)
                render_page(v_anchor=capture_v_anchor())
            elif ctrl and keyval == Gdk.KEY_0:
                ed.zoom_pct = None
                render_page(v_anchor=capture_v_anchor())
            elif keyval == Gdk.KEY_F9:
                side_toggle.set_active(not side_toggle.get_active())
            elif keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace) and ed.selected:
                # Selected page ghosts (signatures, markup) beat sidebar page-delete.
                # When the thumbnail rail is open this used to fall into the
                # side_toggle branch and return without removing the ghost.
                ed.delete_selected()
            elif side_toggle.get_active() and ctrl and keyval == Gdk.KEY_v:
                sidebar_api["paste_pages"]()
                return True
            elif (
                side_toggle.get_active()
                and sidebar_api["has_page_selection"]()
                and ctrl
                and keyval == Gdk.KEY_c
            ):
                sidebar_api["copy_selected_pages"]()
                return True
            elif (
                side_toggle.get_active()
                and sidebar_api["has_page_selection"]()
                and ctrl
                and keyval == Gdk.KEY_x
            ):
                sidebar_api["cut_selected_pages"]()
                return True
            elif (
                (sidebar_api["sidebar_focus"]["active"] or side_toggle.get_active())
                and keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace)
            ):
                sidebar_api["delete_selected_pages"]()
                area.queue_draw()
                refresh_title()
                return True
            elif (
                (sidebar_api["sidebar_focus"]["active"] or side_toggle.get_active())
                and ctrl
                and keyval == Gdk.KEY_r
                and shift
            ):
                sidebar_api["rotate_selected"](-90)
                area.queue_draw()
                refresh_title()
                return True
            elif (
                (sidebar_api["sidebar_focus"]["active"] or side_toggle.get_active())
                and ctrl
                and keyval == Gdk.KEY_r
            ):
                sidebar_api["rotate_selected"](90)
                area.queue_draw()
                refresh_title()
                return True
            elif (
                (sidebar_api["sidebar_focus"]["active"] or side_toggle.get_active())
                and keyval == Gdk.KEY_b
            ):
                sidebar_api["insert_blank_after_current"]()
                area.queue_draw()
                refresh_title()
                return True
            elif ctrl and keyval in (Gdk.KEY_z, Gdk.KEY_Z) and shift:
                do_redo()
            elif ctrl and keyval == Gdk.KEY_z:
                do_undo()
            elif ctrl and keyval == Gdk.KEY_y:
                do_redo()
            elif keyval == Gdk.KEY_Escape:
                ed.selected = None
            elif keyval == Gdk.KEY_r and not ctrl:
                set_tool("redact")
            elif ctrl and keyval == Gdk.KEY_g:
                popover_try_popup(page_btn)
            elif keyval == Gdk.KEY_Page_Up:
                go(-1)
            elif keyval == Gdk.KEY_Page_Down:
                go(1)
            elif keyval == Gdk.KEY_Home:
                goto_page(0)
            elif keyval == Gdk.KEY_End:
                goto_page(ed.page_count() - 1)
            elif (
                ed.selected
                and ed.selected.get("kind") not in ("saved_annot", "field_fill", "delete_annot")
                and ed.selected in ed.pending
                and keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down)
            ):
                dx = {Gdk.KEY_Left: -step, Gdk.KEY_Right: step}.get(keyval, 0.0)
                dy = {Gdk.KEY_Up: -step, Gdk.KEY_Down: step}.get(keyval, 0.0)
                ed.move_item(ed.selected, dx, dy)
            elif keyval == Gdk.KEY_Up:
                go(-1)
            elif keyval == Gdk.KEY_Down:
                go(1)
            else:
                return False
            area.queue_draw()
            refresh_title()
            return True

        keys.connect("key-pressed", on_key)
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        win.add_controller(keys)

        scroller = Gtk.ScrolledWindow()
        scroller.add_css_class("omapdf-page-canvas")
        scroller.set_child(area)
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        scroller.set_hexpand_set(True)

        # -- thumbnails sidebar -------------------------------------------

        def on_sidebar_navigate(idx):
            if idx != ed.page_no:
                ed.page_no = idx
                ed.selected = None
                render_page()

        def on_sidebar_change():
            dropped = ed.last_dropped_pending
            ed.last_dropped_pending = 0
            ed.invalidate_view()
            n_pages = ed.page_count()
            if n_pages:
                ed.page_no = min(ed.page_no, n_pages - 1)
            sidebar_api["refresh"]()
            render_page()
            refresh_title()
            if dropped == 1:
                toast("Dropped a pending edit on a deleted page")
            elif dropped > 1:
                toast(f"Dropped {dropped} pending edits on deleted pages")

        sidebar_api = gui_pages.build_page_sidebar(
            ed, on_sidebar_navigate, on_sidebar_change, toast
        )
        win.insert_action_group("page", sidebar_api["actions"])
        side_list = sidebar_api["widget"]
        side_scroll = Gtk.ScrolledWindow()
        side_scroll.add_css_class("omapdf-thumb-rail")
        side_scroll.set_child(side_list)
        side_scroll.set_size_request(132, -1)
        side_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        side_revealer = Gtk.Revealer()
        side_revealer.add_css_class("omapdf-thumb-revealer")
        side_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        side_revealer.set_child(side_scroll)
        side_revealer.set_hexpand(False)
        side_revealer.set_vexpand(True)
        side_revealer.set_halign(Gtk.Align.START)
        side_toggle.connect("toggled", lambda b: side_revealer.set_reveal_child(b.get_active()))

        # -- search -------------------------------------------------------

        search_bar = Gtk.SearchBar()
        search_entry = Gtk.SearchEntry()
        search_entry.set_width_chars(32)
        search_bar.set_child(search_entry)
        search_bar.connect_entry(search_entry)

        def run_search(term):
            if not ed.has_document():
                ed.search_hits = []
                ed.search_pos = -1
                toast("Open a PDF first (Ctrl+O)")
                return
            ed.search_term = term
            doc = ed.viewing_doc()
            ed.search_hits = [
                (n, rect)
                for n in range(doc.page_count)
                for rect in (doc[n].search_for(term) if term else [])
            ]
            ed.search_pos = -1
            if ed.search_hits:
                goto_hit(0)
            else:
                toast(f"No matches for {term!r}" if term else "")
                area.queue_draw()

        def goto_hit(i):
            ed.search_pos = i % len(ed.search_hits)
            pno, rect = ed.search_hits[ed.search_pos]
            if pno != ed.page_no:
                ed.page_no = pno
                ed.selected = None
                render_page()
            toast(f"Match {ed.search_pos + 1} of {len(ed.search_hits)}")

            def scroll_to():
                adj = scroller.get_vadjustment()
                _, page_y = ed.page_origin
                _, hit_y, _, _ = to_view_rect(rect)
                adj.set_value(max(0, page_y + hit_y * ed.zoom - scroller.get_height() / 3))
                return False

            GLib.idle_add(scroll_to)
            area.queue_draw()

        search_entry.connect("search-changed", lambda e: run_search(e.get_text().strip()))
        search_entry.connect("activate", lambda _e: ed.search_hits and goto_hit(ed.search_pos + 1))
        search_entry.connect(
            "next-match", lambda _e: ed.search_hits and goto_hit(ed.search_pos + 1)
        )
        search_entry.connect(
            "previous-match", lambda _e: ed.search_hits and goto_hit(ed.search_pos - 1)
        )

        def on_search_toggle(btn):
            search_bar.set_search_mode(btn.get_active())
            if btn.get_active():
                search_entry.grab_focus()
            else:
                run_search("")

        search_btn.connect("toggled", on_search_toggle)
        search_bar.connect(
            "notify::search-mode-enabled",
            lambda bar, _p: search_btn.set_active(bar.get_search_mode()),
        )

        # Ctrl+scroll zoom on the page.
        scroll_ctl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )

        def on_scroll(ctl, _dx, dy):
            if not ed.has_document():
                return False
            if ctl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
                current = ed.zoom_pct if ed.zoom_pct else ed.zoom / (96 / 72) * 100
                ed.zoom_pct = max(25.0, min(400.0, current - dy * 10))
                render_page(v_anchor=capture_v_anchor())
                return True
            return False

        scroll_ctl.connect("scroll", on_scroll)
        scroller.add_controller(scroll_ctl)

        pinch = Gtk.GestureZoom.new()
        pinch.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def pinch_committed_pct() -> float:
            return current_zoom_pct(ed.zoom_pct, ed.zoom)

        def _pinch_focus(gesture):
            """Scroller-relative Y and drawing-area XY of the pinch (else viewport center)."""
            return compute_pinch_focus(gesture, scroller, area)

        def on_pinch_begin(gesture, _seq):
            if not ed.has_document():
                return
            if sig_drag["active"] or popover_busy():
                return
            if pinch_state["commit_id"]:
                GLib.source_remove(pinch_state["commit_id"])
                pinch_state["commit_id"] = 0
            try:
                viewport_y, focus_area = _pinch_focus(gesture)
            except Exception:
                vadj = scroller.get_vadjustment()
                hadj = scroller.get_hadjustment()
                vw = float(hadj.get_page_size() or scroller.get_width() or 1.0)
                vh = float(vadj.get_page_size() or scroller.get_height() or 1.0)
                cx, cy = vw / 2.0, vh / 2.0
                viewport_y, focus_area = cy, (
                    float(hadj.get_value()) + cx,
                    float(vadj.get_value()) + cy,
                )
            start = pinch_committed_pct()
            pinch_state["start_pct"] = start
            pinch_state["live_pct"] = start
            ed.pinch_live_scale = 1.0
            ed.pinch_focus_area = focus_area
            pinch_state["anchor"] = {
                "page_y": page_y_at_focus(
                    page_origin_y=ed.page_origin[1],
                    zoom=ed.zoom,
                    vscroll=float(scroller.get_vadjustment().get_value()),
                    viewport_y=float(viewport_y),
                ),
                "viewport_y": float(viewport_y),
            }

        def on_pinch(_gesture, scale):
            start = pinch_state["start_pct"]
            if start is None:
                return
            live = pinch_live_pct(start, scale)
            pinch_state["live_pct"] = live
            ed.pinch_live_scale = pinch_pixmap_scale(start, live)
            zoom_dot.set_text(f"{int(live)}%")
            area.queue_draw()

        def commit_pinch_zoom():
            pinch_state["commit_id"] = 0
            if sig_drag["active"] or popover_busy():
                pinch_state["pending_commit"] = True
                return False
            live = pinch_state["live_pct"]
            anchor = pinch_state["anchor"]
            pinch_state["start_pct"] = None
            pinch_state["live_pct"] = None
            pinch_state["anchor"] = None
            pinch_state["pending_commit"] = False
            if live is None:
                ed.pinch_live_scale = 1.0
                ed.pinch_focus_area = None
                return False
            ed.zoom_pct = live
            render_page(v_anchor=anchor)
            return False

        def on_pinch_end(_gesture, _seq):
            if pinch_state["start_pct"] is None and pinch_state["live_pct"] is None:
                return
            if pinch_state["commit_id"]:
                GLib.source_remove(pinch_state["commit_id"])
            pinch_state["commit_id"] = GLib.idle_add(commit_pinch_zoom)

        pinch.connect("begin", on_pinch_begin)
        pinch.connect("scale-changed", on_pinch)
        pinch.connect("end", on_pinch_end)
        pinch.connect("cancel", on_pinch_end)
        scroller.add_controller(pinch)

        # Keep fit-page honest when the viewport changes — sidebar sliding
        # in or out, window resizes. Debounced so the revealer animation
        # causes one re-render, not thirty.
        fit_state = {"pending": False}

        def on_viewport_change(_adj, _p):
            if pinch_state["start_pct"] is not None:
                return
            if not ed.has_document():
                return
            if ed.zoom_pct is not None or fit_state["pending"]:
                return
            fit_state["pending"] = True

            def rerender():
                fit_state["pending"] = False
                if ed.zoom_pct is None and abs(fit_page_zoom() - ed.zoom) > 0.004:
                    render_page()
                return False

            GLib.timeout_add(130, rerender)

        scroller.get_hadjustment().connect("notify::page-size", on_viewport_change)

        def on_scroller_width(_widget, _pspec):
            if scroller.get_width() < 50:
                return
            if ed.page_surface is None or ed.page_surface.get_width() < 8:
                GLib.idle_add(lambda: (render_page(), False)[1])
            elif ed.zoom_pct is None:
                on_viewport_change(scroller.get_hadjustment(), None)

        scroller.connect("notify::width", on_scroller_width)

        def on_scroller_height(_widget, _pspec):
            if scroller.get_height() < 50:
                return
            if ed.zoom_pct is None:
                on_viewport_change(scroller.get_vadjustment(), None)

        scroller.connect("notify::height", on_scroller_height)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content.set_hexpand(True)
        content.set_vexpand(True)
        content.append(side_revealer)
        content.append(scroller)
        toast_revealer.set_child(toast_label)
        cheer_label = Gtk.Label()
        cheer_label.set_halign(Gtk.Align.END)
        cheer_label.set_valign(Gtk.Align.START)
        cheer_label.set_margin_end(56)
        cheer_label.set_margin_top(8)
        cheer_label.set_can_target(False)
        overlay = Gtk.Overlay()
        overlay.set_child(content)
        overlay.set_vexpand(True)
        overlay.set_hexpand(True)
        overlay.add_overlay(toast_revealer)
        overlay.add_overlay(cheer_label)
        overlay.add_overlay(empty_cue)
        overlay.add_overlay(toolbar_wrap)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        box.append(search_bar)
        box.append(overlay)
        win.set_child(box)
        if show_window_controls:
            header = Gtk.HeaderBar()
            header.add_css_class("omapdf-window-controls")
            header.set_show_title_buttons(True)
            header.set_title_widget(Gtk.Box())  # empty — tools stay on the rail
            win.set_titlebar(header)

        # Watch the file: when an agent (or anything else) saves changes to
        # it, refresh the view — the GUI half of the ask-the-agent loop.

        def on_disk_change(_m, _f, _o, event):
            if event != Gio.FileMonitorEvent.CHANGES_DONE_HINT:
                return
            if GLib.get_monotonic_time() < write_guard["until"]:
                return

            def reload():
                if not ed.has_document() or not Path(ed.path).is_file():
                    return False
                try:
                    if ed.doc is not None and not ed.doc.is_closed:
                        ed.doc.close()
                    ed.doc = pymupdf.open(ed.path)
                except Exception:
                    return False
                ed.invalidate_view()
                n_pages = ed.page_count()
                if n_pages:
                    ed.page_no = min(ed.page_no, n_pages - 1)
                ed.page_preview.clear(preserve_temp_sources=True)
                ed._prune_page_sources()
                render_page()
                if ed.pending or ed.page_preview.has_changes():
                    toast("Changed on disk — view refreshed; your unsaved items are kept")
                else:
                    toast("Updated by another program — reloaded")
                return False

            GLib.timeout_add(200, reload)

        def attach_monitor(path: str):
            if not path:
                return None
            m = Gio.File.new_for_path(path).monitor_file(
                Gio.FileMonitorFlags.NONE, None
            )
            m.connect("changed", on_disk_change)
            return m

        monitor = attach_monitor(ed.path)
        watch_state = {"monitor": monitor}

        def retarget_watch():
            old = watch_state["monitor"]
            if old is not None:
                try:
                    old.cancel()
                except Exception:
                    pass
            m = attach_monitor(ed.path)
            watch_state["monitor"] = m
            win._omapdf_monitor = m

        file_watch["retarget"] = retarget_watch
        win._omapdf_monitor = monitor  # keep the watcher alive

        sidebar_api["refresh"]()
        if ed.page_count() > 1:
            side_toggle.set_active(True)

        def initial_render():
            render_page()
            if ed.pending:
                toast(
                    f"{len(ed.pending)} proposed change(s) loaded — "
                    "drag to adjust, Save to apply"
                )
            return False

        win.present()
        GLib.idle_add(initial_render)

    app.connect("activate", on_activate)
    try:
        app.run(None)
    finally:
        ed.close()
    return 0


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        return run(None)
    return run(args[0], args[1] if len(args) > 1 else None)


if __name__ == "__main__":
    sys.exit(main())
