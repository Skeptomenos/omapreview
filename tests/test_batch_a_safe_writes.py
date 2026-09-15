"""Batch A regressions: staged outputs, encrypted saves, and GUI recovery."""

from __future__ import annotations

import copy
import os
import stat
from pathlib import Path

import pymupdf
import pytest

from omepreview import engine
from omepreview.fs_privacy import FILE_MODE
from omepreview.ops import OpError


def make_pdf(path: Path, pages: int = 2) -> Path:
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"PAGE-{number} KEEP", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def file_mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_apply_failure_preserves_source_and_existing_output(tmp_path, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf")
    output = tmp_path / "output.pdf"
    sentinel = b"existing output"
    output.write_bytes(sentinel)

    def fail(*_args, **_kwargs):
        raise OpError("injected operation failure")

    monkeypatch.setitem(engine._APPLIERS, "note", fail)
    before = source.read_bytes()
    with pytest.raises(OpError, match="injected operation failure"):
        engine.apply(
            source,
            [
                {"op": "rotate_pages", "pages": [1], "degrees": 90},
                {"op": "note", "page": 1, "at": [100, 100], "text": "later"},
            ],
            output=output,
        )
    assert source.read_bytes() == before
    assert output.read_bytes() == sentinel
    assert not list(tmp_path.glob(".output.pdf.*.tmp"))


def test_publish_failure_preserves_existing_output(tmp_path, monkeypatch):
    source = make_pdf(tmp_path / "source.pdf")
    output = tmp_path / "output.pdf"
    sentinel = b"existing output"
    output.write_bytes(sentinel)
    before = source.read_bytes()

    def fail_publish(*_args, **_kwargs):
        raise OSError("injected publish failure")

    monkeypatch.setattr(engine, "_publish_staged", fail_publish)
    with pytest.raises(OSError, match="injected publish failure"):
        engine.apply(
            source,
            [{"op": "note", "page": 1, "at": [100, 100], "text": "later"}],
            output=output,
        )
    assert source.read_bytes() == before
    assert output.read_bytes() == sentinel
    assert not list(tmp_path.glob(".output.pdf.*.tmp"))


def test_extract_only_preserves_source_and_stages_until_success(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", pages=3)
    excerpt = tmp_path / "excerpt.pdf"
    before = source.read_bytes()

    result = engine.apply(
        source,
        [{"op": "extract_pages", "pages": [2], "to": str(excerpt)}],
    )
    assert result["output"] is None
    assert source.read_bytes() == before
    extracted = pymupdf.open(excerpt)
    try:
        assert "PAGE-2" in extracted[0].get_text()
    finally:
        extracted.close()
    assert file_mode(excerpt) == FILE_MODE


def test_extract_failure_keeps_existing_destination(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", pages=3)
    excerpt = tmp_path / "excerpt.pdf"
    sentinel = b"existing excerpt"
    excerpt.write_bytes(sentinel)
    before = source.read_bytes()

    with pytest.raises(OpError, match="not found"):
        engine.apply(
            source,
            [
                {"op": "extract_pages", "pages": [2], "to": str(excerpt)},
                {"op": "highlight", "page": 1, "match": "MISSING"},
            ],
        )
    assert source.read_bytes() == before
    assert excerpt.read_bytes() == sentinel


def test_extract_rejects_alias_duplicate_and_main_collisions(tmp_path):
    source = make_pdf(tmp_path / "source.pdf", pages=2)
    alias = tmp_path / "source-hardlink.pdf"
    os.link(source, alias)
    with pytest.raises(OpError, match="aliases the source"):
        engine.apply(
            source,
            [{"op": "extract_pages", "pages": [1], "to": str(alias)}],
        )
    with pytest.raises(OpError, match="aliases source"):
        engine.apply(
            source,
            [{"op": "note", "page": 1, "at": [100, 100], "text": "no alias"}],
            output=alias,
        )

    first = tmp_path / "first.pdf"
    with pytest.raises(OpError, match="duplicates"):
        engine.apply(
            source,
            [
                {"op": "extract_pages", "pages": [1], "to": str(first)},
                {"op": "extract_pages", "pages": [2], "to": str(first)},
            ]
        )

    with pytest.raises(OpError, match="collides"):
        engine.apply(
            source,
            [{"op": "extract_pages", "pages": [1], "to": str(first)}],
            output=first,
        )
    assert not first.exists()


def test_encrypted_save_preserves_owner_password_and_permissions(tmp_path):
    plain = make_pdf(tmp_path / "plain.pdf")
    encrypted = tmp_path / "encrypted.pdf"
    doc = pymupdf.open(str(plain))
    doc.save(
        str(encrypted),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="",
        permissions=pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_COPY,
    )
    doc.close()

    before = pymupdf.open(str(encrypted))
    permissions = before.permissions
    assert before.metadata["encryption"]
    before.close()
    engine.apply(
        encrypted,
        [{"op": "note", "page": 1, "at": [100, 100], "text": "kept"}],
    )

    reopened = pymupdf.open(str(encrypted))
    try:
        assert reopened.metadata["encryption"]
        assert reopened.permissions == permissions
        assert reopened.authenticate("") == 2
        page = reopened[0]
        assert any(annot.info.get("content") == "kept" for annot in page.annots() or [])
    finally:
        reopened.close()


def test_encrypted_extraction_is_rejected_before_output_write(tmp_path):
    plain = make_pdf(tmp_path / "plain.pdf")
    encrypted = tmp_path / "encrypted.pdf"
    excerpt = tmp_path / "excerpt.pdf"
    doc = pymupdf.open(str(plain))
    doc.save(
        str(encrypted),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="",
        permissions=pymupdf.PDF_PERM_ACCESSIBILITY,
    )
    doc.close()
    before = encrypted.read_bytes()

    with pytest.raises(OpError, match="cannot preserve"):
        engine.apply(
            encrypted,
            [{"op": "extract_pages", "pages": [1], "to": str(excerpt)}],
        )
    assert encrypted.read_bytes() == before
    assert not excerpt.exists()


def test_flatten_rejects_output_alias_before_replacement(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    alias = tmp_path / "source-hardlink.pdf"
    os.link(source, alias)
    before = source.read_bytes()

    with pytest.raises(OpError, match="aliases source"):
        engine.flatten(source, output=alias)
    assert source.read_bytes() == before
    assert alias.read_bytes() == before


def test_editor_save_stages_page_and_markup_once_and_retry_applies_once(
    tmp_path, monkeypatch
):
    from omepreview.gui import Editor

    source = make_pdf(tmp_path / "source.pdf")
    editor = Editor(str(source), None)
    try:
        editor.page_preview.add_rotate_pages([1], 90)
        editor.pending.extend(
            [
                {"kind": "highlight", "page": 0, "x0": 70, "y0": 60, "x1": 160, "y1": 85},
                {"kind": "note", "page": 0, "x": 180, "y": 100, "text": "once"},
            ]
        )
        before = source.read_bytes()
        pending_before = copy.deepcopy(editor.pending)
        real_note = engine._APPLIERS["note"]

        def fail_note(*_args, **_kwargs):
            raise OpError("injected retry failure")

        calls = []
        real_apply = engine.apply

        def record_apply(*args, **kwargs):
            calls.append((args, kwargs))
            return real_apply(*args, **kwargs)

        monkeypatch.setattr(engine, "apply", record_apply)
        monkeypatch.setitem(engine._APPLIERS, "note", fail_note)
        with pytest.raises(OpError, match="injected retry failure"):
            editor.save_pending()
        assert len(calls) == 1
        assert source.read_bytes() == before
        assert editor.pending == pending_before
        assert editor.undo_stack == []
        assert editor.redo_stack == []
        assert editor.doc is not None and not editor.doc.is_closed

        monkeypatch.setitem(engine._APPLIERS, "note", real_note)
        result = editor.save_pending()
        assert len(calls) == 2
        assert result["saved"] == 3
        saved = pymupdf.open(source)
        try:
            assert saved[0].rotation == 90
            page = saved[0]
            types = [annot.type[1] for annot in page.annots() or []]
            assert types.count("Text") == 1
            assert types.count("Highlight") == 1
        finally:
            saved.close()
        assert editor.pending == []
        assert len(editor.undo_stack) == 1
    finally:
        if editor.doc is not None and not editor.doc.is_closed:
            editor.doc.close()


def test_editor_undo_redo_keep_history_when_restore_fails(tmp_path, monkeypatch):
    from omepreview import gui
    from omepreview.fs_privacy import atomic_write_private as real_atomic_write_private
    from omepreview.gui import Editor

    source = make_pdf(tmp_path / "source.pdf")
    editor = Editor(str(source), None)
    try:
        editor.pending.append(
            {"kind": "note", "page": 0, "x": 100, "y": 100, "text": "history"}
        )
        editor.save_pending()
        saved_bytes = source.read_bytes()
        assert len(editor.undo_stack) == 1

        def fail_restore(*_args, **_kwargs):
            raise OSError("injected restore failure")

        monkeypatch.setattr(gui, "atomic_write_private", fail_restore)
        with pytest.raises(OSError, match="injected restore failure"):
            editor.undo()
        assert source.read_bytes() == saved_bytes
        assert len(editor.undo_stack) == 1
        assert editor.redo_stack == []
        assert editor.pending == []

        monkeypatch.setattr(gui, "atomic_write_private", real_atomic_write_private)
        assert editor.undo() is True
        original_bytes = source.read_bytes()
        assert len(editor.undo_stack) == 0
        assert len(editor.redo_stack) == 1

        monkeypatch.setattr(gui, "atomic_write_private", fail_restore)
        with pytest.raises(OSError, match="injected restore failure"):
            editor.redo()
        assert source.read_bytes() == original_bytes
        assert len(editor.undo_stack) == 0
        assert len(editor.redo_stack) == 1
        assert editor.pending and editor.pending[0]["text"] == "history"
    finally:
        if editor.doc is not None and not editor.doc.is_closed:
            editor.doc.close()
