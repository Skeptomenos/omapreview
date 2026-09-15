"""GTK thumbnail sidebar with page-operation ghosts."""

from __future__ import annotations

import mimetypes
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
import pymupdf
from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from .export_guard import (
    UnsavedExport,
    extract_pages_bytes_if_clean,
    require_clean_export,
    serialize_pages_if_clean,
    write_pages_if_clean,
)
from .page_clipboard import (
    MIME_PDF,
    push_clipboard,
    read_clipboard_pdf_bytes_async,
    write_temp_pdf,
)
from .ops import OpError
from .page_preview import PagePreviewState, index_after_move
from .popover_safe import popover_try_popup
from .render import raster_page


def insertion_marker_y(target_row_y: int | None, last_row_bottom: int) -> int:
    """Y of the drop line: top of the hovered row (in front of it), or after the last."""
    if target_row_y is None:
        return last_row_bottom
    return target_row_y


def clear_thumb_dragging(thumb_row) -> None:
    """Drop the dragging dim class. No-op if *thumb_row* is not a widget.

    Gtk.DragSource ``drag-end`` is ``(source, drag, delete_data: bool)``.
    Do not take that bool as the row. Capture the widget with a keyword-only
    default so the third positional arg stays ``delete_data``.
    """
    remover = getattr(thumb_row, "remove_css_class", None)
    if not callable(remover):
        return
    try:
        remover("omapdf-thumb-dragging")
    except Exception:
        pass


def build_page_sidebar(
    ed,
    on_navigate,
    on_change,
    toast,
):
    """Build the thumbnail sidebar; mutates ``ed`` with page-preview state."""

    preview: PagePreviewState = ed.page_preview

    def touch_preview():
        ed.invalidate_view()

    selected: set[int] = set()
    anchor: int | None = None
    sidebar_focus = {"active": False}
    row_widgets: list[Gtk.Widget] = []
    paste_state = {"cancellable": None, "generation": 0}

    side_list = Gtk.ListBox()
    side_list.add_css_class("omapdf-thumbs")
    overlay = Gtk.Overlay()
    overlay.add_css_class("omapdf-thumbs-overlay")
    overlay.set_child(side_list)
    slot_line = Gtk.Box()
    slot_line.add_css_class("omapdf-drop-slot")
    slot_line.set_valign(Gtk.Align.START)
    slot_line.set_halign(Gtk.Align.FILL)
    slot_line.set_can_target(False)
    slot_line.set_margin_start(8)
    slot_line.set_margin_end(8)
    slot_line.set_visible(False)
    overlay.add_overlay(slot_line)

    def last_row_bottom() -> int:
        if not row_widgets:
            return 0
        alloc = row_widgets[-1].get_allocation()
        return int(alloc.y + alloc.height)

    def show_drop_slot(y: float):
        row = side_list.get_row_at_y(int(y))
        if row is None:
            slot_y = insertion_marker_y(None, last_row_bottom())
        else:
            alloc = row.get_allocation()
            slot_y = insertion_marker_y(int(alloc.y), last_row_bottom())
        slot_line.set_margin_top(max(0, slot_y - 2))
        slot_line.set_visible(True)

    def hide_drop_slot(*_a):
        slot_line.set_visible(False)

    def pages_doc() -> pymupdf.Document:
        return ed.page_doc()

    def refresh_thumbs():
        nonlocal row_widgets
        row_widgets.clear()
        side_list.remove_all()
        if not ed.has_document():
            return
        doc = pages_doc()
        for n in range(doc.page_count):
                pg = doc[n]
                thumb_w = 84
                thumb_h = int(pg.rect.height * thumb_w / pg.rect.width)
                scale = (thumb_w * 2) / pg.rect.width
                pix = raster_page(pg, scale)
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(pix.tobytes("png")))
                # Drag ghost must match sidebar CSS size. The 2× texture is
                # for the in-rail Picture; using it as set_icon paints a large overlay.
                icon_scale = thumb_w / pg.rect.width
                icon_pix = raster_page(pg, icon_scale)
                drag_tex = Gdk.Texture.new_from_bytes(
                    GLib.Bytes.new(icon_pix.tobytes("png"))
                )
                pic = Gtk.Picture.new_for_paintable(texture)
                pic.add_css_class("omapdf-thumb")
                pic.set_size_request(thumb_w, thumb_h)
                pic.set_content_fit(Gtk.ContentFit.FILL)
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                cell.set_margin_top(8)
                cell.set_margin_bottom(4)
                cell.set_margin_start(8)
                cell.set_margin_end(8)
                cell.append(pic)
                label = Gtk.Label(label=str(n + 1))
                label.add_css_class("omapdf-thumb-num")
                if (n + 1) in preview.inserted_pages:
                    label.set_text(f"+ {n + 1}")
                cell.append(label)
                row = Gtk.ListBoxRow()
                row.set_child(cell)
                row.set_activatable(False)
                row.page_index = n
                if n in selected:
                    row.add_css_class("omapdf-thumb-selected")
                if (n + 1) in preview.inserted_pages:
                    row.add_css_class("omapdf-thumb-inserted")
                gesture = Gtk.GestureClick()
                gesture.set_button(0)
                gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

                def on_click(g, _n, _x, _y, idx=n):
                    nonlocal anchor
                    state = g.get_current_event_state()
                    ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
                    shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
                    if shift and anchor is not None:
                        lo, hi = sorted((anchor, idx))
                        selected.clear()
                        selected.update(range(lo, hi + 1))
                    elif ctrl:
                        if idx in selected:
                            selected.discard(idx)
                        else:
                            selected.add(idx)
                    else:
                        selected.clear()
                        selected.add(idx)
                    anchor = idx
                    refresh_selection_style()
                    on_navigate(idx)

                gesture.connect("pressed", on_click)
                row.add_controller(gesture)

                drag = Gtk.DragSource()
                drag.set_actions(Gdk.DragAction.MOVE | Gdk.DragAction.COPY)

                def prepare(g, _x, _y, idx=n):
                    if idx not in selected:
                        selected.clear()
                        selected.add(idx)
                        refresh_selection_style()
                    state = g.get_current_event_state()
                    if state & Gdk.ModifierType.SHIFT_MASK:
                        pages = selected_1based() or [idx + 1]
                        try:
                            pdf = extract_pages_bytes_if_clean(
                                ed.pending,
                                ed.page_preview.has_changes(),
                                pages_doc(),
                                pages,
                            )
                        except UnsavedExport as exc:
                            toast(str(exc))
                            return None
                        return Gdk.ContentProvider.new_for_bytes(
                            MIME_PDF, GLib.Bytes.new(pdf)
                        )
                    return Gdk.ContentProvider.new_for_value(str(idx))

                def on_drag_begin(
                    src,
                    _drag,
                    *,
                    thumb_row=row,
                    drag_tex=drag_tex,
                    hot_x=thumb_w // 2,
                    hot_y=thumb_h // 2,
                ):
                    adder = getattr(thumb_row, "add_css_class", None)
                    if callable(adder):
                        try:
                            adder("omapdf-thumb-dragging")
                        except Exception:
                            pass
                    try:
                        src.set_icon(drag_tex, hot_x, hot_y)
                    except Exception:
                        pass

                def on_drag_end(_src, _drag, _delete_data=False, *, thumb_row=row):
                    # GTK4: third arg is delete_data (bool), not the row widget.
                    clear_thumb_dragging(thumb_row)
                    hide_drop_slot()

                drag.connect("drag-begin", on_drag_begin)
                drag.connect("drag-end", on_drag_end)
                drag.connect("prepare", prepare)
                row.add_controller(drag)
                side_list.append(row)
                row_widgets.append(row)

    def refresh_selection_style():
        for row in row_widgets:
            idx = row.page_index
            if idx in selected:
                row.add_css_class("omapdf-thumb-selected")
            else:
                row.remove_css_class("omapdf-thumb-selected")

    def select_pages(indices: set[int]):
        selected.clear()
        selected.update(indices)
        refresh_selection_style()

    def highlight_current(n: int):
        """Keep multi-select; ensure the current page stays selected."""
        nonlocal anchor
        anchor = n
        if not selected:
            selected.add(n)
        refresh_selection_style()

    def selected_1based() -> list[int]:
        return sorted(i + 1 for i in selected)

    def drop_after_for_row(row_index: int) -> int:
        """Map sidebar row index to move_pages ``after`` (0 = beginning)."""
        return 0 if row_index <= 0 else row_index

    def scroll_thumb_into_view(idx: int) -> bool:
        """Scroll the thumb rail so row ``idx`` is visible after a rebuild."""
        if idx < 0 or idx >= len(row_widgets):
            return False
        row = row_widgets[idx]
        scroll = None
        widget = row
        while widget is not None:
            if isinstance(widget, Gtk.ScrolledWindow):
                scroll = widget
                break
            widget = widget.get_parent()
        if scroll is None:
            return False
        adj = scroll.get_vadjustment()
        alloc = row.get_allocation()
        y = int(alloc.y)
        h = int(alloc.height)
        vis_top = adj.get_value()
        vis_bot = vis_top + adj.get_page_size()
        if y < vis_top:
            adj.set_value(y)
        elif y + h > vis_bot:
            adj.set_value(max(adj.get_lower(), y + h - adj.get_page_size()))
        return False

    drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)

    def on_reorder_drop(_t, value, _x, y):
        nonlocal anchor
        try:
            src_idx = int(value)
        except (TypeError, ValueError):
            return False
        row = side_list.get_row_at_y(int(y))
        if row is None:
            after = pages_doc().page_count
        else:
            after = drop_after_for_row(row.page_index)
        pages = selected_1based() or [src_idx + 1]
        # Dropping onto a page in the selection is a no-op — engine would
        # raise "after page N is among the pages being moved".
        if after != 0 and after in pages:
            return False
        n = pages_doc().page_count
        ed.checkpoint()
        try:
            preview.move_selection_to_after(pages, after)
        except OpError:
            return False
        dest = index_after_move(n, pages, after)
        ed.page_no = dest
        selected.clear()
        selected.update(range(dest, dest + len(pages)))
        anchor = dest
        touch_preview()
        on_change()
        toast(f"Moved page(s) {pages} after {after}")
        hide_drop_slot()
        GLib.idle_add(scroll_thumb_into_view, dest)
        return True

    def on_reorder_enter(_t, x, y):
        show_drop_slot(y)
        return Gdk.DragAction.MOVE

    def on_reorder_motion(_t, x, y):
        show_drop_slot(y)
        return Gdk.DragAction.MOVE

    drop_target.connect("drop", on_reorder_drop)
    drop_target.connect("enter", on_reorder_enter)
    drop_target.connect("motion", on_reorder_motion)
    drop_target.connect("leave", hide_drop_slot)
    side_list.add_controller(drop_target)

    file_target = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
    file_target.set_gtypes([Gdk.FileList.__gtype__])

    def on_file_drop(_t, files, _x, y):
        row = side_list.get_row_at_y(int(y))
        after = drop_after_for_row(row.page_index) if row else pages_doc().page_count
        paths = [Path(f.get_path()) for f in files.get_files() if f.get_path()]
        if not paths:
            return False
        ed.checkpoint()
        for path in paths:
            mime = mimetypes.guess_type(str(path))[0] or ""
            if path.suffix.lower() == ".pdf" or mime == "application/pdf":
                preview.add_insert_pdf(after, str(path))
                after += pymupdf.open(str(path)).page_count
                pymupdf.open(str(path)).close()
            elif mime.startswith("image/") or path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                preview.add_insert_image(after, str(path))
                after += 1
        touch_preview()
        on_change()
        toast(f"Inserted {len(paths)} file(s)")
        return True

    file_target.connect("drop", on_file_drop)
    side_list.add_controller(file_target)

    menu = Gio.Menu()
    menu.append("Rotate right (90°)", "page.rotate_cw")
    menu.append("Rotate left (90°)", "page.rotate_ccw")
    menu.append("Delete", "page.delete")
    menu.append("Insert blank after", "page.blank")
    menu.append("Insert file…", "page.insert_file")
    menu.append("Extract…", "page.extract")
    popover = Gtk.PopoverMenu.new_from_model(menu)
    # One parent for the editor lifetime. Unparenting on ``closed`` races
    # GMenu activate: the popover is already orphaned, so ``page.*`` never
    # reaches the window action group and every item is a dead click.
    # Do not call set_autohide (GTK 4.22 SEGV on mapped popovers).
    menu_host = {"widget": None}

    def show_menu(x, y):
        host = ed.window or side_list
        parent = popover.get_parent()
        if parent is None:
            popover.set_parent(host)
            menu_host["widget"] = host
        elif parent is not host:
            try:
                popover.unparent()
            except Exception:
                pass
            popover.set_parent(host)
            menu_host["widget"] = host
        px, py = int(x), int(y)
        if host is not side_list:
            try:
                ok, tx, ty = side_list.translate_coordinates(host, x, y)
                if ok:
                    px, py = int(tx), int(ty)
            except Exception:
                pass
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = px, py, 1, 1
        popover.set_pointing_to(rect)
        popover_try_popup(popover)

    def act_rotate_cw(_a, _p):
        pages = selected_1based() or [ed.page_no + 1]
        ed.checkpoint()
        preview.add_rotate_pages(pages, 90)
        touch_preview()
        on_change()

    def act_rotate_ccw(_a, _p):
        pages = selected_1based() or [ed.page_no + 1]
        ed.checkpoint()
        preview.add_rotate_pages(pages, -90)
        touch_preview()
        on_change()

    def act_delete(_a, _p):
        pages = selected_1based()
        if not pages:
            return
        ed.checkpoint()
        preview.add_delete_pages(pages)
        selected.clear()
        touch_preview()
        on_change()
        toast(f"Marked page(s) {pages} for deletion")

    def act_blank(_a, _p):
        after = selected_1based()[-1] if selected else ed.page_no + 1
        ed.checkpoint()
        preview.add_insert_blank(after)
        touch_preview()
        on_change()
        toast("Blank page inserted")

    def act_insert_file(_a, _p):
        after = selected_1based()[-1] if selected else ed.page_no + 1
        dialog = Gtk.FileDialog()
        dialog.set_title("Insert pages from file")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f_pdf = Gtk.FileFilter()
        f_pdf.set_name("PDF")
        f_pdf.add_mime_type("application/pdf")
        f_img = Gtk.FileFilter()
        f_img.set_name("Images")
        f_img.add_mime_type("image/png")
        f_img.add_mime_type("image/jpeg")
        filters.append(f_pdf)
        filters.append(f_img)
        dialog.set_filters(filters)

        def on_pick(_d, result):
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            path = file.get_path()
            if not path:
                return
            ed.checkpoint()
            try:
                if path.lower().endswith(".pdf"):
                    preview.add_insert_pdf(after, path)
                else:
                    preview.add_insert_image(after, path)
                touch_preview()
                on_change()
                toast(f"Inserted {Path(path).name}")
            except Exception as exc:
                toast(f"Insert failed: {exc}")

        parent = ed.window
        if parent is None:
            toast("Editor window not ready")
            return
        dialog.open(parent, None, on_pick)

    def act_extract(_a, _p):
        pages = selected_1based() or [ed.page_no + 1]
        try:
            require_clean_export(
                ed.pending,
                ed.page_preview.has_changes(),
                action="exporting pages",
            )
        except UnsavedExport as exc:
            toast(str(exc))
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Extract selected pages")
        dialog.set_initial_name("excerpt.pdf")

        def on_save(_d, result):
            try:
                dest = dialog.save_finish(result)
            except GLib.Error:
                return
            path = dest.get_path()
            if not path:
                return
            try:
                write_pages_if_clean(
                    ed.pending,
                    ed.page_preview.has_changes(),
                    pages_doc(),
                    pages,
                    path,
                )
                toast(f"Extracted {len(pages)} page(s) to {Path(path).name}")
            except UnsavedExport as exc:
                toast(str(exc))
            except Exception as exc:
                toast(f"Extract failed: {exc}")

        parent = ed.window
        if parent is None:
            toast("Editor window not ready")
            return
        dialog.save(parent, None, on_save)

    action_group = Gio.SimpleActionGroup()
    for name, cb in (
        ("rotate_cw", act_rotate_cw),
        ("rotate_ccw", act_rotate_ccw),
        ("delete", act_delete),
        ("blank", act_blank),
        ("insert_file", act_insert_file),
        ("extract", act_extract),
    ):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", cb)
        action_group.add_action(action)
    popover.insert_action_group("page", action_group)
    side_list.insert_action_group("page", action_group)

    gesture_menu = Gtk.GestureClick()
    gesture_menu.set_button(3)

    def on_right_click(g, _n, x, y):
        show_menu(x, y)

    gesture_menu.connect("pressed", on_right_click)
    side_list.add_controller(gesture_menu)

    focus_ctl = Gtk.EventControllerFocus()

    def on_focus_in(_c):
        sidebar_focus["active"] = True

    def on_focus_out(_c):
        sidebar_focus["active"] = False

    focus_ctl.connect("enter", on_focus_in)
    focus_ctl.connect("leave", on_focus_out)
    side_list.add_controller(focus_ctl)

    def insert_blank_after_current():
        ed.checkpoint()
        preview.add_insert_blank(ed.page_no + 1)
        touch_preview()
        on_change()
        toast("Blank page inserted")

    def delete_selected_pages():
        pages = selected_1based()
        if not pages:
            pages = [ed.page_no + 1]
        ed.checkpoint()
        preview.add_delete_pages(pages)
        selected.clear()
        touch_preview()
        on_change()
        toast(f"Marked page(s) {pages} for deletion")

    def rotate_selected(degrees: int):
        pages = selected_1based() or [ed.page_no + 1]
        ed.checkpoint()
        preview.add_rotate_pages(pages, degrees)
        touch_preview()
        on_change()

    def insert_after_focus() -> int:
        pages = selected_1based()
        if pages:
            return pages[-1]
        return ed.page_no + 1 if ed.page_no + 1 <= pages_doc().page_count else pages_doc().page_count

    def has_page_selection() -> bool:
        return bool(selected)

    def copy_selected_pages() -> bool:
        pages = selected_1based() or [ed.page_no + 1]
        try:
            json_bytes, pdf_bytes = serialize_pages_if_clean(
                ed.pending,
                ed.page_preview.has_changes(),
                pages_doc(),
                pages,
            )
            push_clipboard(json_bytes, pdf_bytes)
        except UnsavedExport as exc:
            toast(str(exc))
            return False
        except Exception as exc:
            toast(f"Copy failed: {exc}")
            return False
        toast(f"Copied {len(pages)} page(s)")
        return True

    def paste_pages() -> bool:
        target_window = getattr(ed, "window", None)
        target_doc = ed.doc
        target_path = ed.path

        paste_state["generation"] += 1
        generation = paste_state["generation"]
        previous = paste_state["cancellable"]
        if previous is not None:
            try:
                previous.cancel()
            except Exception:
                pass
            paste_state["cancellable"] = None

        def still_active() -> bool:
            """Reject a late clipboard result after close or document switch."""
            return (
                target_window is not None
                and getattr(ed, "window", None) is target_window
                and ed.has_document()
                and ed.doc is target_doc
                and ed.path == target_path
                and not target_doc.is_closed
            )

        def finish(pdf_bytes: bytes | None):
            if paste_state["generation"] == generation:
                paste_state["cancellable"] = None
            if not still_active():
                return
            if not pdf_bytes:
                toast("Clipboard has no omepreview pages")
                return
            after = insert_after_focus()
            tmp = None
            try:
                tmp = write_temp_pdf(pdf_bytes)
                ed.checkpoint()
                preview.add_insert_pdf(after, str(tmp), retain_source=True)
                touch_preview()
                on_change()
                toast(f"Pasted pages after {after}")
            except Exception as exc:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
                toast(f"Paste failed: {exc}")

        cancellable = read_clipboard_pdf_bytes_async(finish)
        if paste_state["generation"] == generation:
            paste_state["cancellable"] = cancellable
        return True

    def cut_selected_pages() -> bool:
        if not copy_selected_pages():
            return False
        delete_selected_pages()
        return True

    return {
        "widget": overlay,
        "refresh": refresh_thumbs,
        "highlight_current": highlight_current,
        "select_row": highlight_current,
        "sidebar_focus": sidebar_focus,
        "selected_1based": selected_1based,
        "insert_blank_after_current": insert_blank_after_current,
        "delete_selected_pages": delete_selected_pages,
        "rotate_selected": rotate_selected,
        "has_page_selection": has_page_selection,
        "copy_selected_pages": copy_selected_pages,
        "paste_pages": paste_pages,
        "cut_selected_pages": cut_selected_pages,
        "actions": action_group,
    }
