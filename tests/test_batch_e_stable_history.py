"""Batch E regressions: editor conflicts, immutable preview inputs, and badges."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest

from omepreview import engine
from omepreview.ops import OpError
from omepreview.page_preview import PagePreviewState
from tests.data.make_docs import make_image_asset, make_labeled_pdf


def make_pdf(path: Path, labels: list[str]) -> Path:
    doc = pymupdf.open()
    for label in labels:
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), label, fontsize=20)
    doc.save(str(path))
    doc.close()
    return path


def page_texts(path: Path) -> list[str]:
    doc = pymupdf.open(str(path))
    try:
        return [page.get_text("text").strip() for page in doc]
    finally:
        doc.close()


def test_external_change_keeps_dirty_session_and_blocks_stale_save(tmp_path):
    from omepreview.gui import Editor

    source = make_pdf(tmp_path / "document.pdf", ["FIRST", "SECOND"])
    editor = Editor(str(source), None)
    editor.pending.append({"kind": "note", "page": 0, "x": 100, "y": 100, "text": "local"})
    editor.checkpoint()
    editor.page_preview.add_rotate_pages([1], 90)
    old_pending = [dict(editor.pending[0])]
    old_page_ops = [dict(editor.page_preview.page_ops[0])]
    try:
        engine.apply(
            source,
            [{"op": "move_pages", "pages": [2], "after": 0}],
            output=source,
        )
        external_bytes = source.read_bytes()

        assert editor.handle_external_change() == "conflict"
        assert editor.external_conflict
        assert editor.pending == old_pending
        assert editor.page_preview.page_ops == old_page_ops
        # The visible session still reflects the version the user was editing.
        assert [page.get_text("text").strip() for page in editor.page_doc()] == [
            "FIRST",
            "SECOND",
        ]
        with pytest.raises(OpError, match="changed on disk"):
            editor.save_pending()
        assert source.read_bytes() == external_bytes
        with pytest.raises(OpError, match="changed on disk"):
            editor.undo()
        assert source.read_bytes() == external_bytes
        editor.reload_from_disk()
        assert not editor.external_conflict
        assert not editor.pending
        assert not editor.undo_stack
        assert [page.get_text("text").strip() for page in editor.doc] == [
            "SECOND",
            "FIRST",
        ]
    finally:
        editor.close()


def test_external_change_reloads_only_a_clean_session(tmp_path):
    from omepreview.gui import Editor

    source = make_pdf(tmp_path / "document.pdf", ["FIRST"])
    editor = Editor(str(source), None)
    try:
        engine.apply(
            source,
            [{"op": "rotate_pages", "pages": [1], "degrees": 90}],
            output=source,
        )
        assert editor.handle_external_change() == "reloaded"
        assert editor.doc[0].rotation == 90
        assert not editor.external_conflict
        assert not editor.undo_stack
        assert not editor.redo_stack
    finally:
        editor.close()


def test_editor_binds_loaded_document_and_fingerprint_to_same_bytes(tmp_path, monkeypatch):
    from omepreview.gui import Editor

    source = make_pdf(tmp_path / "document.pdf", ["FIRST REVIEWED"])
    replacement = make_pdf(tmp_path / "replacement.pdf", ["SECOND EXTERNAL"])
    real_read_bytes = Path.read_bytes
    replaced = False

    def read_bytes(path):
        nonlocal replaced
        data = real_read_bytes(path)
        if Path(path) == source and not replaced:
            replaced = True
            os.replace(replacement, source)
        return data

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    editor = Editor(str(source), None)
    try:
        assert replaced
        assert editor.doc[0].get_text().strip() == "FIRST REVIEWED"
        assert editor.handle_external_change() == "reloaded"
        assert not editor.external_conflict
        assert editor.doc[0].get_text().strip() == "SECOND EXTERNAL"
    finally:
        editor.close()


@pytest.mark.parametrize("kind", ["pdf", "image"])
def test_insert_input_is_snapshotted_before_external_change(tmp_path, kind):
    from omepreview.gui import Editor

    source = make_pdf(tmp_path / "document.pdf", ["BASE"])
    if kind == "pdf":
        inserted = make_pdf(tmp_path / "insert.pdf", ["ORIGINAL"])
    else:
        inserted = make_image_asset(tmp_path / "insert.png")
    editor = Editor(str(source), None)
    try:
        if kind == "pdf":
            editor.page_preview.add_insert_pdf(1, str(inserted))
            op = editor.page_preview.page_ops[-1]
            expected = "ORIGINAL"
        else:
            editor.page_preview.add_insert_image(1, str(inserted))
            op = editor.page_preview.page_ops[-1]
            expected = None
        snapshot = Path(op["source"] if kind == "pdf" else op["image"])
        assert snapshot != inserted
        assert snapshot.exists()
        editor.checkpoint()
        editor.page_preview.add_rotate_pages([2], 90)

        if kind == "pdf":
            make_pdf(inserted, ["REPLACED"])
        else:
            inserted.unlink()

        result = editor.save_pending()
        with pymupdf.open(result["path"]) as saved:
            assert saved.page_count == 2
            if expected:
                assert expected in saved[1].get_text()
            else:
                assert saved[1].get_images()
        if expected:
            assert editor.undo()
            assert editor.page_count() == 2
            assert expected in editor.page_doc()[1].get_text()
            assert editor.redo()
            assert editor.page_count() == 2
            assert expected in editor.page_doc()[1].get_text()
        else:
            assert editor.undo()
            assert editor.page_count() == 2
            assert editor.page_doc()[1].get_images()
            assert editor.redo()
            assert editor.page_count() == 2
            assert editor.page_doc()[1].get_images()
    finally:
        editor.close()
    assert not snapshot.exists()
    if kind == "pdf":
        assert inserted.exists()


def test_failed_scratch_rebuild_keeps_previous_preview_and_cleans_candidate(tmp_path):
    source = make_labeled_pdf(tmp_path / "document.pdf", page_count=2)
    state = PagePreviewState(source)
    state.add_insert_blank(1)
    previous = Path(state.scratch_path)
    previous_bytes = previous.read_bytes()
    scratch_before = set(previous.parent.glob("*.pdf"))
    with pytest.raises(OpError, match="out of range"):
        state.append_op({"op": "delete_pages", "pages": [99]})
    assert state.scratch_path == str(previous)
    assert previous.exists()
    assert previous.read_bytes() == previous_bytes
    assert state.page_count() == 3
    assert set(previous.parent.glob("*.pdf")) == scratch_before
    state.clear()


def test_insert_badges_follow_page_identity_not_text_or_rotation(tmp_path):
    source = make_labeled_pdf(tmp_path / "document.pdf", page_count=2)
    identical = make_labeled_pdf(tmp_path / "identical.pdf", page_count=1)
    state = PagePreviewState(source)
    state.add_insert_blank(0)
    assert state.inserted_pages == {1}
    state.add_insert_pdf(2, str(identical))
    assert state.inserted_pages == {1, 3}
    state.add_rotate_pages([2], 90)
    assert state.inserted_pages == {1, 3}
    state.add_move_pages([4], after=0)
    assert state.identities() == [1, 2, 0, 3]
    assert state.inserted_pages == {2, 4}
    state.clear()
