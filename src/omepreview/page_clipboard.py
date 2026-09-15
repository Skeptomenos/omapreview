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


def _read_gtk_bytes_async(mimes, on_result, *, timeout_ms):
    """Read a bounded MIME stream through EOF; report (MIME, raw bytes)."""
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, GLib
    except (ImportError, ValueError):
        on_result(None, None)
        return None
    display = Gdk.Display.get_default()
    clip = display.get_clipboard() if display is not None else None
    if clip is None:
        on_result(None, None)
        return None

    cancellable = Gio.Cancellable()
    state = {"done": False, "timeout_id": 0, "stream": None, "pending": False}
    data = bytearray()

    def close_stream():
        stream = state["stream"]
        if stream is None or state["pending"]:
            return
        state["stream"] = None

        def closed(source, result):
            try:
                source.close_finish(result)
            except GLib.Error:
                pass

        # Cleanup must not inherit the cancelled read token.
        stream.close_async(GLib.PRIORITY_DEFAULT, None, closed)

    def finish(mime=None, value=None):
        if state["done"]:
            return
        state["done"] = True
        if state["timeout_id"]:
            GLib.source_remove(state["timeout_id"])
            state["timeout_id"] = 0
        data.clear()
        close_stream()
        on_result(mime, value)

    def read_next(stream, mime):
        if state["done"]:
            close_stream()
            return
        state["pending"] = True
        try:
            stream.read_bytes_async(
                min(65536, CLIPBOARD_MAX_BYTES - len(data) + 1),
                GLib.PRIORITY_DEFAULT,
                cancellable,
                lambda source, result: on_bytes(source, result, mime),
            )
        except (GLib.Error, TypeError, ValueError):
            state["pending"] = False
            finish()

    def on_bytes(stream, result, mime):
        state["pending"] = False
        try:
            chunk = stream.read_bytes_finish(result).get_data()
            if state["done"]:
                return
            if not chunk:
                finish(mime, bytes(data))
            elif len(data) + len(chunk) > CLIPBOARD_MAX_BYTES:
                finish()
            else:
                data.extend(chunk)
                read_next(stream, mime)
        except (GLib.Error, TypeError, ValueError):
            finish()
        finally:
            if state["done"]:
                close_stream()

    def on_read(_clipboard, result):
        try:
            stream, mime = clip.read_finish(result)
            state["stream"] = stream
            if state["done"]:
                close_stream()
            elif stream is None or mime not in mimes:
                finish()
            else:
                read_next(stream, mime)
        except (GLib.Error, TypeError, ValueError):
            finish()

    def on_timeout():
        state["timeout_id"] = 0
        cancellable.cancel()
        return False

    # Gio.Cancellable.connect calls its callback without arguments.
    cancellable.connect(lambda: finish())
    state["timeout_id"] = GLib.timeout_add(max(1, int(timeout_ms)), on_timeout)
    try:
        clip.read_async(mimes, GLib.PRIORITY_DEFAULT, cancellable, on_read)
    except (GLib.Error, TypeError, ValueError):
        finish()
    return cancellable


def read_clipboard_pdf_bytes_async(
    on_result: Callable[[bytes | None], None],
    *,
    timeout_ms: int = CLIPBOARD_READ_TIMEOUT_MS,
):
    """Read a complete PDF asynchronously, with timeout and cancellation."""
    def decoded(mime, data):
        on_result(_parse_clipboard_data(mime, data) if data is not None else None)

    return _read_gtk_bytes_async(
        [*PAGE_CLIPBOARD_MIMES, MIME_PDF], decoded, timeout_ms=timeout_ms
    )


def _read_gtk_clipboard_mime(mime: str) -> bytes | None:
    """Return raw MIME bytes; the synchronous caller decodes them once."""
    try:
        from gi.repository import GLib
    except ImportError:
        return None

    result = {"ready": False, "value": None}

    def done(_mime, data):
        result.update(ready=True, value=data)

    cancellable = _read_gtk_bytes_async(
        [mime], done, timeout_ms=CLIPBOARD_READ_TIMEOUT_MS
    )
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + CLIPBOARD_READ_TIMEOUT_MS / 1000
    while not result["ready"] and time.monotonic() < deadline:
        ctx.iteration(False)
        time.sleep(0.001)
    if not result["ready"] and cancellable is not None:
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
