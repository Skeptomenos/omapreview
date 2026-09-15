"""Tests for cross-window page clipboard serialization."""

import base64
import json
import time

import pymupdf

from omepreview.page_clipboard import (
    MIME_OMEPREVIEW_PAGES,
    MIME_OMAPDF_PAGES,
    PAGE_CLIPBOARD_MIMES,
    pdf_bytes_from_clipboard_text,
    push_clipboard,
    read_clipboard_pdf_bytes,
    read_clipboard_pdf_bytes_async,
    serialize_pages,
    write_pages_to_file,
)
from tests.data.make_docs import make_labeled_pdf


def test_clipboard_mime_prefers_omepreview_accepts_legacy():
    assert MIME_OMEPREVIEW_PAGES == "application/x-omepreview-pages"
    assert MIME_OMAPDF_PAGES == "application/x-omapdf-pages"
    assert PAGE_CLIPBOARD_MIMES[0] == MIME_OMEPREVIEW_PAGES
    assert MIME_OMAPDF_PAGES in PAGE_CLIPBOARD_MIMES


def test_serialize_and_parse_roundtrip(tmp_path):
    pdf = make_labeled_pdf(tmp_path / "doc.pdf", page_count=4)
    doc = pymupdf.open(str(pdf))
    json_bytes, pdf_bytes = serialize_pages(doc, [2, 3])
    doc.close()
    payload = json.loads(json_bytes.decode())
    assert payload["n"] == 2
    assert pdf_bytes_from_clipboard_text(json.dumps(payload)) == pdf_bytes
    out = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    assert out.page_count == 2
    out.close()


def test_extract_pages_to_file(tmp_path):
    pdf = make_labeled_pdf(tmp_path / "doc.pdf", page_count=5)
    doc = pymupdf.open(str(pdf))
    dest = tmp_path / "excerpt.pdf"
    write_pages_to_file(doc, [1, 5], dest)
    doc.close()
    out = pymupdf.open(str(dest))
    assert out.page_count == 2
    assert "PAGE 1" in out[0].get_text()
    assert "PAGE 5" in out[1].get_text()
    out.close()


def test_paste_accepts_legacy_omapdf_mime(monkeypatch):
    doc = pymupdf.open()
    doc.new_page()
    doc[0].insert_text((72, 72), "legacy mime")
    json_bytes, pdf_bytes = serialize_pages(doc, [1])
    doc.close()

    monkeypatch.setattr(
        "omepreview.page_clipboard._read_gtk_clipboard_mime", lambda _mime: None
    )
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/xclip" if name == "xclip" else None
    )

    def fake_xclip(mime):
        if mime == MIME_OMAPDF_PAGES:
            return json_bytes
        return None

    monkeypatch.setattr("omepreview.page_clipboard._read_xclip", fake_xclip)
    got = read_clipboard_pdf_bytes()
    assert got == pdf_bytes


def test_gtk_clipboard_roundtrip():
    import gi

    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib

    display = Gdk.Display.get_default()
    if display is None:
        return
    doc = pymupdf.open()
    doc.new_page()
    doc[0].insert_text((72, 72), "clip test")
    json_bytes, pdf_bytes = serialize_pages(doc, [1])
    doc.close()
    push_clipboard(json_bytes, pdf_bytes)
    got = read_clipboard_pdf_bytes()
    assert got is not None
    out = pymupdf.open(stream=got, filetype="pdf")
    assert out.page_count == 1
    assert "clip test" in out[0].get_text()
    out.close()


def test_gtk_clipboard_async_roundtrip_calls_back_once():
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gdk, GLib

    if Gdk.Display.get_default() is None:
        return
    doc = pymupdf.open()
    doc.new_page()
    doc[0].insert_text((72, 72), "async clip test")
    json_bytes, pdf_bytes = serialize_pages(doc, [1])
    doc.close()
    push_clipboard(json_bytes, pdf_bytes)
    seen = []
    read_clipboard_pdf_bytes_async(seen.append)
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + 2.0
    while not seen and time.monotonic() < deadline:
        if ctx.pending():
            ctx.iteration(False)
        else:
            time.sleep(0.005)
    assert seen == [pdf_bytes]


def test_gtk_clipboard_async_cancel_calls_back_once():
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gdk, GLib

    if Gdk.Display.get_default() is None:
        return
    seen = []
    cancellable = read_clipboard_pdf_bytes_async(seen.append, timeout_ms=500)
    assert cancellable is not None
    cancellable.cancel()
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + 1.0
    while not seen and time.monotonic() < deadline:
        if ctx.pending():
            ctx.iteration(False)
        else:
            time.sleep(0.005)
    assert len(seen) == 1
