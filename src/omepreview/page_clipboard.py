"""Cross-window page clipboard serialization for the GTK editor."""

from __future__ import annotations

import base64
import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pymupdf

from .fs_privacy import chmod_private_file, scratch_dir

MIME_OMEPREVIEW_PAGES = "application/x-omepreview-pages"
MIME_OMAPDF_PAGES = "application/x-omapdf-pages"  # deprecated; accepted on paste
MIME_PDF = "application/pdf"
PAGE_CLIPBOARD_MIMES = (MIME_OMEPREVIEW_PAGES, MIME_OMAPDF_PAGES)
CLIPBOARD_READ_TIMEOUT_MS = 1000
CLIPBOARD_MAX_BYTES = 64 * 1024 * 1024


def extract_pages_bytes(doc: pymupdf.Document, pages_1based: list[int]) -> bytes:
    """Build a PDF containing ``pages_1based`` from ``doc``."""
    if not pages_1based:
        raise ValueError("no pages selected")
    out = pymupdf.open()
    try:
        for p in pages_1based:
            if p < 1 or p > doc.page_count:
                raise ValueError(f"page {p} out of range (document has {doc.page_count})")
            out.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def serialize_pages(doc: pymupdf.Document, pages_1based: list[int]) -> tuple[bytes, bytes]:
    """Return ``(json_bytes, pdf_bytes)`` for the clipboard."""
    pdf_bytes = extract_pages_bytes(doc, pages_1based)
    payload = {
        "n": len(pages_1based),
        "pdf_b64": base64.standard_b64encode(pdf_bytes).decode("ascii"),
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8"), pdf_bytes


def pdf_bytes_from_clipboard_text(text: str) -> bytes | None:
    """Parse ``application/x-omepreview-pages`` JSON payload (legacy MIME too)."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    b64 = payload.get("pdf_b64")
    if not isinstance(b64, str):
        return None
    try:
        return base64.standard_b64decode(b64.encode("ascii"))
    except (ValueError, json.JSONDecodeError):
        return None


def write_temp_pdf(pdf_bytes: bytes) -> Path:
    fd, path = tempfile.mkstemp(suffix=".pdf", dir=str(scratch_dir()))
    try:
        with open(fd, "wb") as fh:
            fh.write(pdf_bytes)
        chmod_private_file(path)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
    return Path(path)


def write_pages_to_file(doc: pymupdf.Document, pages_1based: list[int], dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(extract_pages_bytes(doc, pages_1based))
    chmod_private_file(dest)
    return dest


def push_clipboard(json_bytes: bytes, pdf_bytes: bytes) -> None:
    """Write omepreview pages to the system clipboard (GTK + xclip on X11)."""
    import shutil
    import subprocess

    try:
        import gi

        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, GLib

        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set_content(
                Gdk.ContentProvider.new_union([
                    Gdk.ContentProvider.new_for_bytes(
                        MIME_OMEPREVIEW_PAGES, GLib.Bytes.new(json_bytes)
                    ),
                    Gdk.ContentProvider.new_for_bytes(
                        MIME_PDF, GLib.Bytes.new(pdf_bytes)
                    ),
                ])
            )
    except (ImportError, ValueError):
        pass
    if shutil.which("xclip"):
        subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", MIME_OMEPREVIEW_PAGES],
            input=json_bytes,
            check=False,
        )


def _read_xclip(mime: str) -> bytes | None:
    import subprocess

    try:
        proc = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", mime, "-o"],
            capture_output=True,
            timeout=CLIPBOARD_READ_TIMEOUT_MS / 1000,
        )
    except subprocess.TimeoutExpired:
        return None
    return proc.stdout if proc.returncode == 0 and proc.stdout else None


def _parse_clipboard_data(mime: str, data: bytes) -> bytes | None:
    if mime in PAGE_CLIPBOARD_MIMES:
        return pdf_bytes_from_clipboard_text(data.decode("utf-8", errors="replace"))
    if mime == MIME_PDF:
        return data
    return None


def read_clipboard_pdf_bytes_async(
    on_result: Callable[[bytes | None], None],
    *,
    timeout_ms: int = CLIPBOARD_READ_TIMEOUT_MS,
):
    """Read page bytes without blocking GTK's main loop.

    GTK's MIME-aware ``read_async`` returns the selected MIME type and an
    input stream. The operation is cancelled after the bounded timeout and
    ``on_result`` runs exactly once, including on errors or unavailable GTK.
    The returned ``Gio.Cancellable`` can be cancelled by the caller.
    """
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, GLib
    except (ImportError, ValueError):
        on_result(None)
        return None
    display = Gdk.Display.get_default()
    if display is None:
        on_result(None)
        return None
    clip = display.get_clipboard()
    if clip is None:
        on_result(None)
        return None

    cancellable = Gio.Cancellable()
    state = {"done": False, "timeout_id": 0}

    def finish(value: bytes | None):
        if state["done"]:
            return
        state["done"] = True
        timeout_id = state["timeout_id"]
        if timeout_id:
            try:
                GLib.source_remove(timeout_id)
            except (TypeError, ValueError):
                pass
        try:
            on_result(value)
        except Exception:
            # The GTK callback must not unwind through the main loop.
            pass

    def on_bytes(stream, res, mime):
        try:
            raw = stream.read_bytes_finish(res)
            if isinstance(raw, GLib.Bytes):
                data = raw.get_data()
            elif isinstance(raw, (bytes, bytearray)):
                data = bytes(raw)
            else:
                data = None
            if data is None or len(data) > CLIPBOARD_MAX_BYTES:
                finish(None)
            else:
                finish(_parse_clipboard_data(mime, data))
        except (GLib.Error, TypeError, ValueError):
            finish(None)

    def on_read(_clipboard, res):
        try:
            stream, mime = clip.read_finish(res)
            if stream is None or not mime:
                finish(None)
                return
            stream.read_bytes_async(
                CLIPBOARD_MAX_BYTES + 1,
                GLib.PRIORITY_DEFAULT,
                cancellable,
                lambda _stream, result: on_bytes(stream, result, mime),
            )
        except (GLib.Error, TypeError, ValueError):
            finish(None)

    def on_timeout():
        if not state["done"]:
            cancellable.cancel()
            finish(None)
        return False

    cancellable.connect(lambda _cancellable: finish(None))
    state["timeout_id"] = GLib.timeout_add(
        max(1, int(timeout_ms)), on_timeout
    )
    try:
        clip.read_async(
            [*PAGE_CLIPBOARD_MIMES, MIME_PDF],
            GLib.PRIORITY_DEFAULT,
            cancellable,
            on_read,
        )
    except (GLib.Error, TypeError, ValueError):
        finish(None)
    return cancellable


def _read_gtk_clipboard_mime(mime: str) -> bytes | None:
    """Compatibility reader for one MIME, with a bounded non-blocking pump."""
    result: dict[str, object] = {"ready": False, "value": None}

    def done(value: bytes | None):
        result["value"] = value
        result["ready"] = True

    # Use the same MIME-aware async transport as the GTK editor. The short
    # compatibility wait is only for legacy synchronous callers and never
    # blocks inside ``MainContext.iteration``.
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, GLib
    except (ImportError, ValueError):
        return None
    display = Gdk.Display.get_default()
    if display is None:
        return None
    clip = display.get_clipboard()
    if clip is None:
        return None
    cancellable = Gio.Cancellable()
    state = {"done": False, "timeout_id": 0}

    def finish(value: bytes | None):
        if state["done"]:
            return
        state["done"] = True
        if state["timeout_id"]:
            try:
                GLib.source_remove(state["timeout_id"])
            except (TypeError, ValueError):
                pass
        done(value)

    def on_bytes(stream, res):
        try:
            raw = stream.read_bytes_finish(res)
            data = raw.get_data() if isinstance(raw, GLib.Bytes) else bytes(raw)
            finish(_parse_clipboard_data(mime, data))
        except (GLib.Error, TypeError, ValueError):
            finish(None)

    def on_read(_clipboard, res):
        try:
            stream, returned_mime = clip.read_finish(res)
            if stream is None or returned_mime != mime:
                finish(None)
                return
            stream.read_bytes_async(
                CLIPBOARD_MAX_BYTES + 1,
                GLib.PRIORITY_DEFAULT,
                cancellable,
                on_bytes,
            )
        except (GLib.Error, TypeError, ValueError):
            finish(None)

    def on_timeout():
        cancellable.cancel()
        finish(None)
        return False

    state["timeout_id"] = GLib.timeout_add(CLIPBOARD_READ_TIMEOUT_MS, on_timeout)
    try:
        clip.read_async([mime], GLib.PRIORITY_DEFAULT, cancellable, on_read)
    except (GLib.Error, TypeError, ValueError):
        finish(None)
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + CLIPBOARD_READ_TIMEOUT_MS / 1000
    while not result["ready"] and time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.005)
    if not result["ready"]:
        cancellable.cancel()
    return result["value"]


def read_clipboard_pdf_bytes() -> bytes | None:
    """Read page bytes for synchronous callers with a bounded fallback."""
    import shutil

    readers = []
    if shutil.which("xclip"):
        readers.append(_read_xclip)
    readers.append(_read_gtk_clipboard_mime)
    for mime in (*PAGE_CLIPBOARD_MIMES, MIME_PDF):
        for read in readers:
            data = read(mime)
            if data:
                pdf = _parse_clipboard_data(mime, data)
                if pdf:
                    return pdf
    return None
