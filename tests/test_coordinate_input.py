"""R01/R11/R12 coordinate, overlay and editor-input regressions."""

from __future__ import annotations

import copy

import pymupdf

from omepreview import engine
from omepreview.view_gestures import matrix_delta, matrix_point, matrix_rect


def _rotated_cropped_pdf(path, rotation: int):
    doc = pymupdf.open()
    page = doc.new_page(width=360, height=260)
    page.insert_text((80, 90), "KEEP", fontsize=16)
    page.insert_text((80, 140), "SECRET", fontsize=16)
    doc.save(str(path))
    doc.close()
    cropped = path.with_name(f"{path.stem}-crop.pdf")
    engine.apply(
        path,
        [{"op": "crop_pages", "pages": [1], "rect": [50, 40, 310, 220]}],
        output=cropped,
    )
    if not rotation:
        return cropped
    rotated = path.with_name(f"{path.stem}-{rotation}.pdf")
    engine.apply(
        cropped,
        [{"op": "rotate_pages", "pages": [1], "degrees": rotation}],
        output=rotated,
    )
    return rotated


def test_page_matrix_round_trip_all_rotations_with_crop(tmp_path):
    for rotation in (0, 90, 180, 270):
        path = _rotated_cropped_pdf(tmp_path / f"matrix-{rotation}.pdf", rotation)
        doc = pymupdf.open(path)
        page = doc[0]
        local = (37.0, 61.0)
        displayed = matrix_point(page.rotation_matrix, *local)
        restored = matrix_point(page.derotation_matrix, *displayed)
        assert restored == local
        x0, y0, x1, y1 = matrix_rect(page.rotation_matrix, (10, 20, 80, 100))
        expected = (70.0, 80.0) if rotation in (0, 180) else (80.0, 70.0)
        assert (x1 - x0, y1 - y0) == expected
        doc.close()


def test_rotated_cropped_pointer_deletes_secret_and_keeps_neighbor(tmp_path):
    for rotation in (0, 90, 180, 270):
        path = _rotated_cropped_pdf(tmp_path / f"delete-{rotation}.pdf", rotation)
        doc = pymupdf.open(path)
        page = doc[0]
        secret = page.search_for("SECRET")[0]
        center = ((secret.x0 + secret.x1) / 2, (secret.y0 + secret.y1) / 2)
        displayed = matrix_point(page.rotation_matrix, *center)
        assert matrix_point(page.derotation_matrix, *displayed) == center
        assert matrix_rect(page.rotation_matrix, secret)[0] <= displayed[0]
        doc.close()

        out = path.with_name(f"{path.stem}-redacted.pdf")
        engine.apply(path, [{"op": "redact", "page": 1, "rect": list(secret)}], output=out)
        reopened = pymupdf.open(out)
        try:
            text = reopened[0].get_text()
            assert "SECRET" not in text
            assert "KEEP" in text
        finally:
            reopened.close()


def test_rotated_drag_delta_and_hit_geometry_use_page_space(tmp_path):
    path = _rotated_cropped_pdf(tmp_path / "drag.pdf", 90)
    doc = pymupdf.open(path)
    page = doc[0]
    local = (24.0, 42.0)
    displayed = matrix_point(page.rotation_matrix, *local)
    delta = matrix_delta(page.derotation_matrix, 8.0, -5.0)
    restored = matrix_point(page.derotation_matrix, *displayed)
    doc.close()

    from omepreview.gui import Editor

    ed = Editor(str(path), None)
    try:
        ghost = {"kind": "note", "page": 0, "x": 20.0, "y": 40.0, "text": "x"}
        ed.pending.append(ghost)
        ed.page_no = 0
        assert ed.hit(*local) is ghost
        assert ed.move_item(ghost, *delta) is True
        assert ghost["x"] == 20.0 + delta[0]
        assert ghost["y"] == 40.0 + delta[1]
        assert restored == local
    finally:
        ed.doc.close()


def test_identity_ghost_drag_is_safe_and_does_not_move_target():
    from omepreview.gui import Editor

    ed = Editor(None, None)
    for kind in ("field_fill", "delete_annot"):
        ghost = {
            "kind": kind,
            "page": 0,
            "rect": [10.0, 20.0, 80.0, 40.0],
        }
        before = copy.deepcopy(ghost)
        assert ed.move_item(ghost, 12.0, -4.0) is False
        assert ghost == before
