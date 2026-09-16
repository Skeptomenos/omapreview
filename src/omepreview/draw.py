#!/usr/bin/env python3
"""Signature drawing window (GTK4).

The window *is* the trackpad: a rounded pad surface. Space arms recording.
While armed, the physical pad is read as an absolute 2D surface (evdev
ABS_MT_POSITION_* / ABS_X/Y) and mapped onto the on-screen pad. The touchpad
node is EVIOCGRAB'd so the compositor does not also move the system cursor;
the keyboard is not grabbed (Space/Enter still work). A click is not
required. Finger up ends a stroke; a new contact starts at that abs
location. Relative pointer motion is fallback only when no abs axes can be
opened. Enter writes SVG. Space while recording/recorded clears and starts
over. `--click` is mouse click-and-drag fallback only.

Runs under system python when the venv lacks PyGObject; callers use draw.run().
"""

from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
import sys
import tempfile
from pathlib import Path

from . import signature as sig_store
from .abs_pad import AbsPadWatcher, probe_abs_touchpad
from .trackpad_sig import RecorderSession, ribbon_outline, stroke_mode_label


def legacy_event_usable(event) -> bool:
    """GTK EventControllerLegacy sometimes delivers event=None.

    Guard before reading event.type — do not print a traceback per motion.
    """
    return event is not None and getattr(event, "type", None) is not None


# On-screen pad, including the aluminum bezel. Glass is inset.
PAD_W, PAD_H = 560, 380
BEZEL = 22.0
CORNER = 28.0
GLASS_CORNER = 16.0


def run(out_path: str | Path, *, trackpad: bool = True) -> bool:
    """Open the drawing window; True if a signature was saved to out_path."""
    try:
        import gi  # noqa: F401  (venv python usually lacks it)

        return _gtk_main(str(out_path), trackpad=trackpad) == 0
    except ImportError:
        proc = subprocess.run(
            [_system_python(), __file__, str(out_path), "1" if trackpad else "0"]
        )
        return proc.returncode == 0


def run_and_save(name: str = "default", *, trackpad: bool = True) -> bool:
    """Draw a signature and store it under `name` in ~/Downloads/omapreview/signature/."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "signature.svg"
        if not run(out, trackpad=trackpad):
            return False
        sig_store.add(out, name)
        return True


def _system_python() -> str:
    for candidate in ("/usr/bin/python3", "/usr/bin/python"):
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("no system python found for the GTK drawing window")


def _gtk_main(out_path: str, *, trackpad: bool = True, screenshot: str | None = None, cancel_file: Path | None = None) -> int:
    import cairo
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    try:
        gi.require_foreign("cairo")
    except (ImportError, ValueError) as exc:
        raise SystemExit(
            "omepreview sig draw needs PyGObject cairo integration — install python3-gi-cairo."
        ) from exc
    from gi.repository import Gdk, Gio, GLib, Gtk

    from .window_controls import window_controls_enabled

    click_mode = not trackpad
    glass_w = PAD_W - BEZEL * 2
    glass_h = PAD_H - BEZEL * 2
    session = RecorderSession(click_mode=click_mode, pad_size=(glass_w, glass_h))
    saved = False
    grab_release = lambda: None
    abs_stop = lambda: None
    idle_stroke_id = 0
    abs_denied = False

    def glass_rect():
        return BEZEL, BEZEL, glass_w, glass_h

    def in_glass(x: float, y: float) -> bool:
        gx, gy, gw, gh = glass_rect()
        return gx <= x <= gx + gw and gy <= y <= gy + gh

    def to_glass(x: float, y: float) -> tuple[float, float]:
        gx, gy, gw, gh = glass_rect()
        return (
            min(max(x - gx, 0.0), gw),
            min(max(y - gy, 0.0), gh),
        )

    def draw_func(_area, ctx, width, height):
        _paint_trackpad(ctx, width, height, session, cairo)

    def redraw():
        if area is not None:
            area.queue_draw()
        if hint is not None:
            hint.set_text(
                stroke_mode_label(
                    armed=session.armed,
                    click_mode=click_mode,
                    has_ink=session.has_ink(),
                    abs_mode=session.abs_active,
                    abs_denied=abs_denied,
                )
            )
        if status is not None:
            if not session.armed:
                status.set_text("Press Space to start")
            elif session.has_ink():
                status.set_text("Recording — Enter saves")
            elif session.abs_active:
                status.set_text("Recording — finger on the pad draws there")
            elif abs_denied:
                status.set_text("Recording — relative fallback (need input group)")
            else:
                status.set_text("Recording — move on the trackpad")

    def cancel_idle():
        nonlocal idle_stroke_id
        if idle_stroke_id:
            GLib.source_remove(idle_stroke_id)
            idle_stroke_id = 0

    def stop_abs():
        nonlocal abs_stop
        abs_stop()
        abs_stop = lambda: None
        session.abs_active = False

    def start_abs_reader() -> bool:
        nonlocal abs_stop, abs_denied
        stop_abs()
        probe = probe_abs_touchpad()
        abs_denied = probe.permission_denied
        if probe.device is None:
            return False
        device = probe.device
        watcher = None
        try:

            def on_contacts(contacts):
                for ev in contacts:
                    if ev.kind == "up":
                        session.apply_abs("up")
                    elif ev.x is not None and ev.y is not None:
                        gx, gy = device.map_point(ev.x, ev.y, glass_w, glass_h)
                        session.apply_abs(
                            ev.kind,
                            gx,
                            gy,
                            pressure=ev.pressure,
                            pressure_range=device.pressure_range,
                        )
                redraw()

            from gi.repository import GLib

            watcher = AbsPadWatcher(device, on_contacts, idle_add=GLib.idle_add)
            grabbed = watcher.start(exclusive=True)
        except Exception:
            if watcher is not None:
                watcher.stop()
            else:
                device.close()
            return False
        abs_stop = watcher.stop
        session.abs_active = True
        session.grab_pointer = grabbed
        session.mapper.absolute = True
        return True

    def arm_recording():
        nonlocal grab_release
        grab_release()
        grab_release = lambda: None
        cancel_idle()
        stop_abs()
        session.handle_space()
        if not click_mode and start_abs_reader():
            grab_release = _hide_cursor(win, area)
        else:
            release, confined = _grab_pointer(win, area)
            grab_release = release
            session.mapper.absolute = confined
        redraw()

    def release_grab():
        nonlocal grab_release
        grab_release()
        grab_release = lambda: None
        cancel_idle()
        stop_abs()
        session.grab_pointer = False

    def schedule_idle_end():
        nonlocal idle_stroke_id

        def fire():
            nonlocal idle_stroke_id
            idle_stroke_id = 0
            session.end_stroke()
            return False

        if idle_stroke_id:
            GLib.source_remove(idle_stroke_id)
        idle_stroke_id = GLib.timeout_add(320, fire)

    def on_motion_coords(x: float, y: float, *, button1: bool):
        if not session.armed:
            return
        if session.abs_active:
            # Absolute pad owns inking; relative cursor must not continue
            # a stroke from the last (x, y) after a finger lift.
            return
        on_pad = in_glass(x, y)
        if session.mapper.absolute and on_pad:
            gx, gy = to_glass(x, y)
            session.add_point(gx, gy, button1=button1)
        else:
            session.motion(x, y, button1=button1, on_glass=on_pad)
        schedule_idle_end()
        redraw()

    def try_save() -> bool:
        nonlocal saved
        if not session.handle_enter():
            win.set_title("Draw something first — Space to record")
            redraw()
            return False
        release_grab()
        session.write_svg(out_path)
        saved = True
        win.close()
        return True

    app = Gtk.Application(
        application_id="org.omepreview.SignatureDraw",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )

    def on_activate(app):
        nonlocal area, hint, status, win
        win = Gtk.ApplicationWindow(application=app, title="Create Signature")
        win.set_default_size(PAD_W + 48, PAD_H + 150)
        win.set_resizable(False)

        if window_controls_enabled():
            header = Gtk.HeaderBar()
            header.set_show_title_buttons(True)
            header.set_title_widget(Gtk.Label(label="Create Signature"))
            win.set_titlebar(header)
        else:
            win.set_decorated(False)

        area = Gtk.DrawingArea()
        area.set_content_width(PAD_W)
        area.set_content_height(PAD_H)
        area.set_hexpand(True)
        area.set_vexpand(True)
        area.set_draw_func(draw_func)
        area.set_focusable(True)
        area.add_css_class("sig-pad")

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_key(_ctrl, keyval, _code, _state):
            if keyval == Gdk.KEY_space:
                arm_recording()
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                try_save()
                return True
            if keyval == Gdk.KEY_Escape:
                release_grab()
                win.close()
                return True
            return False

        keys.connect("key-pressed", on_key)
        win.add_controller(keys)

        motion = Gtk.EventControllerMotion()

        def button1_from(ctrl) -> bool:
            try:
                return bool(ctrl.get_current_event_state() & Gdk.ModifierType.BUTTON1_MASK)
            except Exception:
                return False

        def on_motion(ctrl, x, y):
            on_motion_coords(x, y, button1=button1_from(ctrl))

        motion.connect("motion", on_motion)
        area.add_controller(motion)

        legacy = Gtk.EventControllerLegacy()
        legacy.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_legacy(_ctrl, event):
            if not legacy_event_usable(event):
                return False
            if session.abs_active:
                return False
            et = event.type
            if et in (Gdk.EventType.TOUCH_END, Gdk.EventType.BUTTON_RELEASE):
                session.end_stroke()
                redraw()
                return False
            if et not in (
                Gdk.EventType.MOTION_NOTIFY,
                Gdk.EventType.TOUCH_BEGIN,
                Gdk.EventType.TOUCH_UPDATE,
            ):
                return False
            ok, x, y = event.get_coords()
            if not ok:
                return False
            button1 = bool(event.get_state() & Gdk.ModifierType.BUTTON1_MASK)
            on_motion_coords(x, y, button1=button1)
            return False

        legacy.connect("event", on_legacy)
        area.add_controller(legacy)

        if click_mode:
            session.mapper.absolute = True
            drag = Gtk.GestureDrag()

            def on_begin(_g, x, y):
                if not session.armed:
                    return
                if in_glass(x, y):
                    gx, gy = to_glass(x, y)
                    session.add_point(gx, gy, button1=True)
                    redraw()

            def on_update(gesture, dx, dy):
                if not session.armed:
                    return
                ok, sx, sy = gesture.get_start_point()
                if ok:
                    on_motion_coords(sx + dx, sy + dy, button1=True)

            def on_end(_g, _x, _y):
                session.end_stroke()

            drag.connect("drag-begin", on_begin)
            drag.connect("drag-update", on_update)
            drag.connect("drag-end", on_end)
            area.add_controller(drag)

        def on_map(_w):
            area.grab_focus()
            if screenshot:
                import os

                session.armed = os.environ.get("OMEPREVIEW_SIG_DEMO", "1") != "0"
                session.grab_pointer = False
                if session.armed:
                    # Two disconnected strokes (finger lift, then a new abs contact).
                    pr = (0, 100)
                    # One smooth stroke: hairline rise, heavy fall. Not a name.
                    session.apply_abs(
                        "down", 50, 240, pressure=4, pressure_range=pr
                    )
                    for i in range(1, 140):
                        t = i / 140
                        x = 50 + t * 400
                        if t < 0.42:
                            s = (0.42 - t) / 0.42
                            y = 80 + 160 * s * s
                            pressure = 5
                        else:
                            s = (t - 0.42) / 0.58
                            y = 80 + 200 * s * s
                            pressure = int(10 + 90 * s)
                        session.apply_abs(
                            "move", x, y, pressure=pressure, pressure_range=pr
                        )
                    session.apply_abs("up")
                    session.apply_abs(
                        "down", 310, 72, pressure=8, pressure_range=pr
                    )
                    for i in range(1, 50):
                        t = i / 50
                        session.apply_abs(
                            "move",
                            310 + t * 85,
                            72 + 16 * ((i % 16) / 8 - 1) ** 2,
                            pressure=int(8 + 18 * t),
                            pressure_range=pr,
                        )
                    session.apply_abs("up")
                redraw()

                def dump():
                    try:
                        _dump_and_maybe_quit(win, screenshot)
                    finally:
                        release_grab()
                        app.quit()
                    return False

                GLib.timeout_add(250, dump)

        win.connect("map", on_map)
        win.connect("close-request", lambda *_: (release_grab(), False)[1])

        status = Gtk.Label(label="Press Space to start")
        status.add_css_class("title-4")
        hint = Gtk.Label(
            label=stroke_mode_label(
                armed=False, click_mode=click_mode, abs_denied=abs_denied
            )
        )
        hint.add_css_class("dim-label")
        hint.set_wrap(True)
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_max_width_chars(64)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.append(status)
        box.append(area)
        box.append(hint)
        win.set_child(box)
        _install_css()
        win.present()
        area.grab_focus()

    area = None
    hint = None
    status = None
    win = None
    app.connect("activate", on_activate)
    cancel_timer = 0
    if cancel_file is not None:
        def check_cancel():
            if cancel_file.exists():
                app.quit()
                return False
            return True
        cancel_timer = GLib.timeout_add(100, check_cancel)
    try:
        app.run(None)
        return 0 if saved else 1
    finally:
        release_grab()


def _paint_trackpad(ctx, width, height, session: RecorderSession, cairo):
    """Aluminum bezel + glass. The widget *is* the trackpad."""
    # Desk behind the pad
    ctx.set_source_rgb(0.91, 0.90, 0.88)
    ctx.paint()

    _round_rect(ctx, 0, 0, width, height, CORNER)
    ctx.set_source_rgb(0.78, 0.78, 0.77)
    ctx.fill_preserve()
    ctx.set_source_rgb(0.62, 0.62, 0.61)
    ctx.set_line_width(1.2)
    ctx.stroke()

    gx, gy, gw, gh = BEZEL, BEZEL, width - BEZEL * 2, height - BEZEL * 2
    _round_rect(ctx, gx, gy, gw, gh, GLASS_CORNER)
    if session.armed:
        ctx.set_source_rgb(0.98, 0.98, 0.96)
    else:
        ctx.set_source_rgb(0.93, 0.93, 0.91)
    ctx.fill_preserve()
    ctx.set_source_rgba(0, 0, 0, 0.10)
    ctx.set_line_width(1.0)
    ctx.stroke()

    # Recessed glass edge
    _round_rect(ctx, gx + 1.5, gy + 1.5, gw - 3, gh - 3, GLASS_CORNER - 2)
    ctx.set_source_rgba(1, 1, 1, 0.35)
    ctx.set_line_width(1.0)
    ctx.stroke()

    ctx.save()
    ctx.translate(gx, gy)
    ctx.rectangle(0, 0, gw, gh)
    # clip to glass (approx — rounded clip via path)
    _round_rect(ctx, 0, 0, gw, gh, GLASS_CORNER - 2)
    ctx.clip()
    _paint_strokes(ctx, session.strokes, cairo)
    if session.armed and session.mapper.pen and not session.has_ink() and session.drawing:
        x, y = session.mapper.pen
        ctx.set_source_rgba(0.05, 0.05, 0.2, 0.35)
        ctx.arc(x, y, 2.2, 0, 6.3)
        ctx.fill()
    ctx.restore()


def _round_rect(ctx, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -1.5708, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, 1.5708)
    ctx.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    ctx.arc(x + r, y + r, r, 3.1416, 4.7124)
    ctx.close_path()


def _paint_strokes(ctx, strokes, cairo):
    ctx.set_source_rgb(0.05, 0.05, 0.2)
    for stroke in strokes:
        if len(stroke) < 2:
            continue
        outline = ribbon_outline(stroke)
        if len(outline) < 3:
            continue
        ctx.new_path()
        ctx.move_to(*outline[0])
        for point in outline[1:]:
            ctx.line_to(*point)
        ctx.close_path()
        ctx.fill()


def _install_css():
    from gi.repository import Gdk, Gtk

    css = Gtk.CssProvider()
    css.load_from_data(
        b"""
        .sig-pad { background: transparent; }
        """
    )
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def _hide_cursor(win, area):
    """Hide the OS cursor over the pad window. Returns a restore callable."""
    from gi.repository import Gdk

    native = win.get_native() if win is not None else None
    surface = native.get_surface() if native is not None else None
    if surface is None:
        return lambda: None
    try:
        blank = Gdk.Cursor.new_from_name("none")
        surface.set_cursor(blank)
        if area is not None:
            area.set_cursor(blank)
    except Exception:
        return lambda: None

    def restore():
        try:
            surface.set_cursor(None)
            if area is not None:
                area.set_cursor(None)
        except Exception:
            pass

    return restore


def _grab_pointer(win, area) -> tuple:
    """X11-only relative-pointer fallback when no abs touchpad can be opened.

    Wayland/Hyprland cannot use XGrabPointer; the armed recorder's path is
    EVIOCGRAB on the touchpad evdev node (see AbsPadWatcher.start).
    """
    restore_cursor = _hide_cursor(win, area)

    native = win.get_native() if win is not None else None
    surface = native.get_surface() if native is not None else None
    if surface is None:
        return (restore_cursor, False)

    ungrab_x = _x11_grab_pointer(surface)

    def release():
        if ungrab_x:
            ungrab_x()
        restore_cursor()

    return release, ungrab_x is not None


def _x11_grab_pointer(surface):
    try:
        import gi

        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
    except (ImportError, ValueError):
        return None
    if not isinstance(surface, GdkX11.X11Surface):
        return None
    lib_name = ctypes.util.find_library("X11")
    if not lib_name:
        return None
    libX11 = ctypes.CDLL(lib_name)
    libX11.XGrabPointer.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    libX11.XGrabPointer.restype = ctypes.c_int
    libX11.XUngrabPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    libX11.XWarpPointer.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
    ]
    try:
        display = surface.get_display()
        xdisplay = GdkX11.X11Display.get_xdisplay(display)
        xid = int(surface.get_xid())
        xdisplay_p = ctypes.c_void_p(int(xdisplay))
    except Exception:
        return None
    # PointerMotionMask | ButtonPressMask | ButtonReleaseMask | Button1MotionMask
    event_mask = (1 << 6) | (1 << 2) | (1 << 3) | (1 << 8)
    GrabModeAsync = 1
    try:
        status = libX11.XGrabPointer(
            xdisplay_p,
            xid,
            1,
            event_mask,
            GrabModeAsync,
            GrabModeAsync,
            xid,  # confine to this window
            0,
            0,  # CurrentTime
        )
    except Exception:
        return None
    if status != 0:
        return None
    try:
        libX11.XWarpPointer(
            xdisplay_p, 0, xid, 0, 0, 0, 0, int(PAD_W / 2), int(PAD_H / 2)
        )
    except Exception:
        pass

    def ungrab():
        try:
            libX11.XUngrabPointer(xdisplay_p, 0)
        except Exception:
            pass

    return ungrab


def _dump_and_maybe_quit(win, path: str) -> None:
    try:
        _snapshot_window(win, path)
    except Exception as exc:
        print(f"screenshot failed: {exc}", file=sys.stderr)


def _snapshot_window(win, path: str) -> None:
    from gi.repository import Graphene, Gtk

    native = win.get_native()
    renderer = native.get_renderer() if native is not None else None
    paintable = Gtk.WidgetPaintable.new(win)
    width = max(win.get_width(), 1)
    height = max(win.get_height(), 1)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None or renderer is None:
        raise RuntimeError("GTK renderer produced no scene node")
    rect = Graphene.Rect()
    rect.init(0, 0, float(width), float(height))
    texture = renderer.render_texture(node, rect)
    texture.save_to_png(path)


def render_pad_png(path: str | Path, session: RecorderSession | None = None) -> Path:
    """Paint the trackpad surface to PNG without opening a window (docs / tests)."""
    import cairo

    path = Path(path)
    session = session or RecorderSession(pad_size=(PAD_W - BEZEL * 2, PAD_H - BEZEL * 2))
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, PAD_W, PAD_H)
    ctx = cairo.Context(surface)
    _paint_trackpad(ctx, PAD_W, PAD_H, session, cairo)
    surface.write_to_png(str(path))
    return path


if __name__ == "__main__":
    trackpad = True
    screenshot = None
    args = sys.argv[1:]
    if "--screenshot" in args:
        i = args.index("--screenshot")
        screenshot = args[i + 1]
        del args[i : i + 2]
    if len(args) == 2:
        trackpad = args[1] != "0"
        out = args[0]
    elif len(args) == 1:
        out = args[0]
    else:
        print("usage: draw.py <output.svg> [trackpad:0|1] [--screenshot png]", file=sys.stderr)
        sys.exit(2)
    sys.exit(_gtk_main(out, trackpad=trackpad, screenshot=screenshot))
