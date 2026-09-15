"""Real pipe transport regressions; never access the system clipboard."""

import os
import time
from types import SimpleNamespace

import gi
import pymupdf
import pytest

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib

from omepreview import page_clipboard as clipboard


def pump(until, seconds=1):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        context.iteration(False)
        if until():
            return
        time.sleep(0.001)


@pytest.fixture
def pipe_clipboard(monkeypatch):
    read_fd, write_fd = os.pipe()
    stream = Gio.UnixInputStream.new(read_fd, True)
    writer = os.fdopen(write_fd, "wb", buffering=0)
    fixture = SimpleNamespace(stream=stream, writer=writer, mime=clipboard.MIME_PDF)

    class Clipboard:
        def read_async(self, mimes, priority, cancellable, callback):
            GLib.idle_add(lambda: (callback(self, None), False)[1])

        def read_finish(self, result):
            return stream, fixture.mime

    fake = Clipboard()
    monkeypatch.setattr(Gdk.Display, "get_default", lambda: SimpleNamespace(get_clipboard=lambda: fake))
    monkeypatch.setattr("shutil.which", lambda name: None)
    yield fixture
    writer.close()
    pump(stream.is_closed, 0.1)
    if not stream.is_closed():
        stream.close(None)


@pytest.fixture
def payload():
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "stream payload")
        return clipboard.serialize_pages(doc, [1])


@pytest.mark.parametrize("custom", [False, True])
def test_chunks_wait_for_eof(pipe_clipboard, payload, custom):
    transport = pipe_clipboard
    raw, pdf = payload
    if custom:
        transport.mime = clipboard.MIME_OMEPREVIEW_PAGES
    else:
        raw = pdf
    seen = []
    clipboard.read_clipboard_pdf_bytes_async(seen.append)
    transport.writer.write(raw[:100])
    pump(lambda: bool(seen), 0.05)
    assert seen == [], "short read is not EOF"
    transport.writer.write(raw[100:])
    pump(lambda: bool(seen), 0.05)
    assert seen == [], "complete payload still requires EOF"
    transport.writer.close()
    pump(lambda: bool(seen) and transport.stream.is_closed())
    assert seen == [pdf]
    assert transport.stream.is_closed()


@pytest.mark.parametrize("size", [128, 129])
def test_total_size_limit(pipe_clipboard, monkeypatch, size):
    monkeypatch.setattr(clipboard, "CLIPBOARD_MAX_BYTES", 128)
    seen = []
    clipboard.read_clipboard_pdf_bytes_async(seen.append)
    pipe_clipboard.writer.write(b"a" * 80)
    pump(lambda: bool(seen), 0.03)
    assert not seen
    pipe_clipboard.writer.write(b"a" * (size - 80))
    if size == 128:
        pump(lambda: bool(seen), 0.03)
        assert not seen
        pipe_clipboard.writer.close()
    pump(lambda: bool(seen) and pipe_clipboard.stream.is_closed())
    assert seen == ([b"a" * size] if size == 128 else [None])
    assert pipe_clipboard.stream.is_closed()


@pytest.mark.parametrize("mode", ["cancel", "timeout", "early_cancel"])
def test_cancel_timeout_close_once(pipe_clipboard, mode, capsys):
    seen = []
    cancel = clipboard.read_clipboard_pdf_bytes_async(seen.append, timeout_ms=80)
    if mode != "early_cancel":
        pipe_clipboard.writer.write(b"partial")
        pump(lambda: bool(seen), 0.02)
    if mode != "timeout":
        cancel.cancel()
    pump(lambda: bool(seen) and pipe_clipboard.stream.is_closed())
    assert seen == [None]
    assert pipe_clipboard.stream.is_closed()
    cancel.cancel()
    pump(lambda: False, 0.02)
    assert seen == [None]
    assert "TypeError" not in capsys.readouterr().err


def test_sync_custom_mime_parsed_once(pipe_clipboard, payload):
    raw, pdf = payload
    pipe_clipboard.mime = clipboard.MIME_OMEPREVIEW_PAGES
    pipe_clipboard.writer.write(raw)
    pipe_clipboard.writer.close()
    assert clipboard.read_clipboard_pdf_bytes() == pdf
    pump(pipe_clipboard.stream.is_closed)
    assert pipe_clipboard.stream.is_closed()
