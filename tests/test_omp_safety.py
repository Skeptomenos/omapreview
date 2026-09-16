"""OMP-02 / OMP-05 / OMP-06 / OMP-08 regression tests.

Generated PDFs only — synthetic tokens, no bank/medical/PII.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from omepreview import engine, signature
from omepreview.export_guard import (
    UnsavedExport,
    extract_pages_bytes_if_clean,
    serialize_pages_if_clean,
    unsaved_export_reason,
    write_pages_if_clean,
)
from omepreview.fs_privacy import FILE_MODE, scratch_dir
from omepreview.ops import OpError
from omepreview.page_preview import (
    PagePreviewState,
    identities_from_ops,
    rebind_items_to_identities,
)
from omepreview.redact_io import share_target_path
from tests.data.make_docs import make_labeled_pdf, make_unique_secret_pdf

SECRET = "ZXQ-OMP-SECRET-4b8e21"


def _mode(path: Path | str) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _ghost(rect, page=0, match=None) -> dict:
    item = {
        "kind": "redact",
        "page": page,
        "x0": float(rect.x0),
        "y0": float(rect.y0),
        "x1": float(rect.x1),
        "y1": float(rect.y1),
    }
    if match is not None:
        item["match"] = match
    return item


def make_three_page_secret(path: Path, secret: str = SECRET) -> Path:
    doc = pymupdf.open()
    for label, extra in (("ALPHA", None), ("BRAVO", secret), ("GAMMA", None)):
        page = doc.new_page()
        page.insert_text((72, 72), label, fontsize=16)
        if extra:
            page.insert_text((72, 120), extra, fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def stage_redact_annot(src: Path, dest: Path, secret: str = SECRET) -> Path:
    doc = pymupdf.open(src)
    page = doc[0]
    rects = page.search_for(secret)
    assert rects
    page.add_redact_annot(rects[0], fill=(0, 0, 0))
    doc.save(str(dest))
    doc.close()
    return dest


@pytest.fixture()
def umask_022():
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


# --- OMP-02: page surgery remaps pending markup --------------------------------

def test_identities_delete_insert_move(tmp_path):
    pdf = make_labeled_pdf(tmp_path / "six.pdf", page_count=6)
    state = PagePreviewState(pdf)
    assert state.identities() == [0, 1, 2, 3, 4, 5]
    state.add_delete_pages([2, 4])
    assert state.identities() == [0, 2, 4, 5]
    state.add_insert_blank(0)
    assert state.identities() == [6, 0, 2, 4, 5]
    state.add_move_pages([3], after=0)
    assert state.identities() == [2, 6, 0, 4, 5]


def test_rebind_drops_deleted_page_does_not_retarget():
    pending = [
        {"kind": "redact", "page": 0},
        {"kind": "sig", "page": 1},
        {"kind": "note", "page": 2},
    ]
    old = [0, 1, 2]
    new = identities_from_ops(3, [{"op": "delete_pages", "pages": [2]}])
    dropped = rebind_items_to_identities(pending, old, new)
    assert dropped == 1
    assert [it["page"] for it in pending] == [0, 1]
    assert pending[0]["kind"] == "redact"
    assert pending[1]["kind"] == "note"
    assert pending[1]["page"] == 1


def test_pending_redact_follows_page_after_delete_then_save(tmp_path):
    from omepreview.gui import Editor

    src = make_three_page_secret(tmp_path / "doc.pdf")
    ed = Editor(str(src), None)
    rect = ed.doc[1].search_for(SECRET)[0]
    ed.pending.append(_ghost(rect, page=1, match=SECRET))
    ed.page_preview.add_delete_pages([1])
    assert len(ed.pending) == 1
    assert ed.pending[0]["page"] == 0
    result = ed.save_pending()
    out = pymupdf.open(result["path"])
    try:
        assert out.page_count == 2
        assert SECRET not in out[0].get_text()
        assert "GAMMA" in out[1].get_text()
        assert SECRET not in out[1].get_text()
    finally:
        out.close()
    assert SECRET in pymupdf.open(src)[1].get_text()
    ed.doc.close()


def test_pending_dropped_when_target_page_deleted(tmp_path):
    from omepreview.gui import Editor

    src = make_three_page_secret(tmp_path / "doc.pdf")
    ed = Editor(str(src), None)
    rect = ed.doc[1].search_for(SECRET)[0]
    ghost = _ghost(rect, page=1, match=SECRET)
    ed.pending.append(ghost)
    ed.selected = ghost
    ed.page_preview.add_delete_pages([2])
    assert ed.pending == []
    assert ed.selected is None
    assert ed.last_dropped_pending == 1
    ed.doc.close()


def test_pending_shifts_on_insert_and_follows_move(tmp_path):
    from omepreview.gui import Editor

    src = make_three_page_secret(tmp_path / "doc.pdf")
    ed = Editor(str(src), None)
    rect = ed.doc[1].search_for(SECRET)[0]
    ed.pending.append(_ghost(rect, page=1, match=SECRET))
    ed.page_preview.add_insert_blank(0)
    assert ed.pending[0]["page"] == 2
    ed.page_preview.add_move_pages([3], after=0)
    assert ed.pending[0]["page"] == 0
    ed.page_preview.add_rotate_pages([1], 90)
    assert ed.pending[0]["page"] == 0
    ed.doc.close()


def test_undo_restores_unremapped_pending(tmp_path):
    from omepreview.gui import Editor

    src = make_three_page_secret(tmp_path / "doc.pdf")
    ed = Editor(str(src), None)
    rect = ed.doc[1].search_for(SECRET)[0]
    ed.pending.append(_ghost(rect, page=1, match=SECRET))
    ed.checkpoint()
    ed.page_preview.add_delete_pages([1])
    assert ed.pending[0]["page"] == 0
    ed.undo()
    assert ed.pending[0]["page"] == 1
    assert not ed.page_preview.has_changes()
    ed.doc.close()


# --- OMP-06: extract/copy honor Share's unsaved guard ---------------------------

def test_unsaved_export_reason_matches_share():
    assert unsaved_export_reason([], False) is None
    assert unsaved_export_reason([{"kind": "redact", "page": 0}], False) == (
        "Unsaved changes — Save before sharing"
    )
    assert unsaved_export_reason([], True, action="exporting pages") == (
        "Unsaved changes — Save before exporting pages"
    )


def test_write_pages_blocked_leaves_sentinel(tmp_path):
    src = make_unique_secret_pdf(tmp_path / "doc.pdf", secret=SECRET)
    dest = tmp_path / "excerpt.pdf"
    dest.write_bytes(b"SENTINEL")
    doc = pymupdf.open(src)
    ghost = [{"kind": "redact", "page": 0, "x0": 1, "y0": 1, "x1": 2, "y1": 2}]
    with pytest.raises(UnsavedExport, match="exporting pages"):
        write_pages_if_clean(ghost, False, doc, [1], dest)
    with pytest.raises(UnsavedExport):
        serialize_pages_if_clean(ghost, False, doc, [1])
    with pytest.raises(UnsavedExport):
        extract_pages_bytes_if_clean([], True, doc, [1])
    doc.close()
    assert dest.read_bytes() == b"SENTINEL"


def test_write_pages_allowed_when_clean(tmp_path):
    src = make_unique_secret_pdf(tmp_path / "doc.pdf", secret=SECRET)
    dest = tmp_path / "excerpt.pdf"
    doc = pymupdf.open(src)
    write_pages_if_clean([], False, doc, [1], dest)
    doc.close()
    text = pymupdf.open(dest)[0].get_text()
    assert SECRET in text
    assert _mode(dest) == FILE_MODE


def test_gui_export_call_sites_use_guard():
    root = Path(__file__).resolve().parents[1]
    pages = (root / "src/omepreview/gui_pages.py").read_text(encoding="utf-8")
    gui = (root / "src/omepreview/gui.py").read_text(encoding="utf-8")
    assert "extract_pages_bytes_if_clean" in pages
    assert "serialize_pages_if_clean" in pages
    assert "write_pages_if_clean" in pages
    assert "require_clean_export" in pages
    assert "unsaved_export_reason" in gui


# --- OMP-05: flatten refuses pending redaction annots ---------------------------

def test_flatten_refuses_staged_redaction_leaves_source(tmp_path):
    src = make_unique_secret_pdf(tmp_path / "doc.pdf", secret=SECRET)
    staged = stage_redact_annot(src, tmp_path / "staged.pdf")
    before = staged.read_bytes()
    out = tmp_path / "flat.pdf"
    with pytest.raises(OpError, match="flatten refused"):
        engine.flatten(staged, output=out)
    assert staged.read_bytes() == before
    assert not out.exists()
    page = pymupdf.open(staged)[0]
    doc = page.parent
    try:
        n_redact = sum(
            1 for a in (page.annots() or []) if a.type[0] == pymupdf.PDF_ANNOT_REDACT
        )
        assert n_redact == 1
        assert SECRET in page.get_text()
        assert "KEEP-VISIBLE" in page.get_text()
    finally:
        doc.close()


def test_flatten_in_place_refuses_without_baking(tmp_path):
    src = make_unique_secret_pdf(tmp_path / "doc.pdf", secret=SECRET)
    staged = stage_redact_annot(src, tmp_path / "staged.pdf")
    before = staged.read_bytes()
    with pytest.raises(OpError, match="pending PDF redaction"):
        engine.flatten(staged)
    assert staged.read_bytes() == before
    assert SECRET in pymupdf.open(staged)[0].get_text()


def test_flatten_of_applied_redaction_still_works(tmp_path):
    src = make_unique_secret_pdf(tmp_path / "doc.pdf", secret=SECRET)
    redacted = tmp_path / "redacted.pdf"
    engine.apply(
        src,
        [{"op": "redact", "page": 1, "match": SECRET, "fill": [0, 0, 0]}],
        output=redacted,
    )
    assert SECRET not in pymupdf.open(redacted)[0].get_text()
    flat = tmp_path / "flat.pdf"
    engine.flatten(redacted, output=flat)
    text = pymupdf.open(flat)[0].get_text()
    assert SECRET not in text
    assert "KEEP-VISIBLE" in text


def test_share_flatten_refuses_staged_redaction(tmp_path):
    src = make_unique_secret_pdf(tmp_path / "doc.pdf", secret=SECRET)
    staged = stage_redact_annot(src, tmp_path / "staged.pdf")
    with pytest.raises(OpError, match="flatten refused"):
        share_target_path(staged, flatten=True, flatten_fn=engine.flatten)
    assert not (tmp_path / "staged-final.pdf").exists()
    assert SECRET in pymupdf.open(staged)[0].get_text()


def test_cli_flatten_refuses_staged_redaction(tmp_path):
    src = make_unique_secret_pdf(tmp_path / "doc.pdf", secret=SECRET)
    staged = stage_redact_annot(src, tmp_path / "staged.pdf")
    out = tmp_path / "flat.pdf"
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "omepreview.cli",
            "flatten",
            str(staged),
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1, run.stderr
    assert "flatten refused" in run.stderr
    assert not out.exists()


# --- OMP-08: 0600 files / 0700 dirs --------------------------------------------

def test_engine_save_is_not_world_readable(tmp_path, umask_022):
    src = make_unique_secret_pdf(tmp_path / "doc.pdf", secret=SECRET)
    os.chmod(src, 0o600)
    out = tmp_path / "noted.pdf"
    engine.apply(
        src,
        [{"op": "note", "page": 1, "at": [72, 200], "text": "private"}],
        output=out,
    )
    assert _mode(out) == FILE_MODE
    engine.apply(
        out,
        [{"op": "note", "page": 1, "at": [72, 240], "text": "again"}],
        output=out,
    )
    assert _mode(out) == FILE_MODE


def test_flatten_output_is_private(tmp_path, umask_022):
    src = make_unique_secret_pdf(tmp_path / "doc.pdf", secret=SECRET)
    engine.apply(
        src,
        [{"op": "note", "page": 1, "at": [72, 200], "text": "mark"}],
        output=src,
    )
    flat = tmp_path / "flat.pdf"
    engine.flatten(src, output=flat)
    assert _mode(flat) == FILE_MODE


def test_preview_scratch_is_private(tmp_path, umask_022):
    src = make_labeled_pdf(tmp_path / "doc.pdf", page_count=3)
    os.chmod(src, 0o600)
    state = PagePreviewState(src)
    state.add_rotate_pages([1], 90)
    assert state.scratch_path
    assert _mode(state.scratch_path) == FILE_MODE
    assert _mode(scratch_dir()) == 0o700
    state.clear()


def test_signature_store_is_private(tmp_path, monkeypatch, umask_022):
    monkeypatch.setenv(
        "OMEPREVIEW_SIGNATURE_DIR",
        str(tmp_path / "signature-store"),
    )
    svg = tmp_path / "jane.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="30">'
        '<path d="M 2 20 L 70 8" fill="none" stroke="#000" stroke-width="3"/>'
        "</svg>",
        encoding="utf-8",
    )
    dest = signature.add(svg, "jane")
    assert _mode(dest) == FILE_MODE
    assert _mode(dest.parent) == 0o700
    assert _mode(dest.parent.parent) == 0o700
