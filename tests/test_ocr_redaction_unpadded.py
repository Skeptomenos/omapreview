"""Unpadded OCR text-snap redaction removes scan pixels and hidden text."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from omepreview import engine, redact_scope
from omepreview.redact_io import redact_item_to_op


TARGET = "SECRET"
CONTROL = "KEEP CONTROL"
OCR_FONT_SIZE = 11.75


def _make_scanned_pdf(
    path: Path, *, rotation: int, crop: bool, adjacent_control: bool = False
) -> Path:
    """Make a synthetic scan with a separate invisible OCR text layer."""
    with pymupdf.open() as artwork:
        art_page = artwork.new_page(width=595, height=842)
        control_at = (120, 352)
        control_text = CONTROL
        if adjacent_control:
            art_page.insert_text((120, 312), "SECRET KEEP", fontname="helv", fontsize=12)
        else:
            art_page.insert_text((120, 312), TARGET, fontname="helv", fontsize=12)
            art_page.insert_text(control_at, control_text, fontname="helv", fontsize=16)
        scan = art_page.get_pixmap(dpi=200, alpha=False)

    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, pixmap=scan)
        if adjacent_control:
            page.insert_text(
                (120, 312), "SECRET KEEP", fontsize=OCR_FONT_SIZE, render_mode=3
            )
        else:
            page.insert_text(
                (120, 312), TARGET, fontsize=OCR_FONT_SIZE, render_mode=3
            )
            page.insert_text(control_at, control_text, fontsize=16, render_mode=3)
        if crop:
            page.set_cropbox(pymupdf.Rect(20, 30, 575, 812))
        page.set_rotation(rotation)
        control = doc.new_page(width=595, height=842)
        control.insert_text((72, 120), "CONTROL PAGE TWO", fontsize=22)
        doc.save(path)
    return path


def _gui_word_snap_ops(page: pymupdf.Page) -> tuple[list[dict], pymupdf.Rect]:
    words = [word for word in page.get_text("words") if word[4] == TARGET]
    assert len(words) == 1
    word = words[0]
    item = {
        "kind": "redact",
        "page": 0,
        "x0": word[0],
        "y0": word[1],
        "x1": word[2],
        "y1": word[3],
        "match": TARGET,
    }
    return [redact_item_to_op(item)], pymupdf.Rect(word[:4])


def _image_crop(
    path: Path, rect: pymupdf.Rect, *, crop_local: bool = False
) -> list[int]:
    with pymupdf.open(path) as doc:
        page = doc[0]
        images = page.get_images(full=True)
        assert images
        pix = pymupdf.Pixmap(doc, images[0][0])
        if crop_local:
            rect = pymupdf.Rect(
                rect.x0 + page.cropbox.x0,
                rect.y0 + page.cropbox.y0,
                rect.x1 + page.cropbox.x0,
                rect.y1 + page.cropbox.y0,
            )
        scale_x = pix.width / page.mediabox.width
        scale_y = pix.height / page.mediabox.height
        x0, y0 = round(rect.x0 * scale_x), round(rect.y0 * scale_y)
        x1, y1 = round(rect.x1 * scale_x), round(rect.y1 * scale_y)
        stride = pix.width * pix.n
        samples = []
        for y in range(y0, y1 + 1):
            row = pix.samples[y * stride : (y + 1) * stride]
            samples.extend(row[x0 * pix.n : (x1 + 1) * pix.n])
        return list(samples)


@pytest.mark.parametrize("rotation,crop", [(0, False), (90, True), (270, True)])
@pytest.mark.parametrize("route", ["gui", "match"])
def test_unpadded_ocr_redaction_clears_scan_and_preserves_controls(
    tmp_path, rotation, crop, route
):
    """Tight OCR-like boxes must not need caller-supplied padding."""
    source = _make_scanned_pdf(
        tmp_path / "scanned.pdf", rotation=rotation, crop=crop
    )
    with pymupdf.open(source) as doc:
        ops, target = _gui_word_snap_ops(doc[0])
        if route == "match":
            ops = [{"op": "redact", "page": 1, "match": TARGET}]
        before_page_two = doc[1].get_pixmap(dpi=100).samples
    target_region = target + (-2, -2, 2, 2)
    source_pixels = _image_crop(source, target_region, crop_local=crop)
    assert any(pixel < 255 for pixel in source_pixels)

    output = tmp_path / f"{route}-{rotation}-{crop}.pdf"
    result = engine.apply(source, ops, output=output)
    resolved = pymupdf.Rect(result["applied"][0]["rects"][0])
    assert resolved.x0 < target.x0 and resolved.y0 < target.y0
    assert resolved.x1 > target.x1 and resolved.y1 > target.y1

    with pymupdf.open(output) as doc:
        page = doc[0]
        assert TARGET not in page.get_text()
        assert CONTROL in page.get_text()
        assert page.rotation == rotation
        if crop:
            assert tuple(page.cropbox) == (20, 30, 575, 812)
        else:
            assert tuple(page.cropbox) == (0, 0, 595, 842)
        assert doc[1].get_pixmap(dpi=100).samples == before_page_two

    # Read the image object itself. The source glyph is within this one-point
    # observation window; a black overlay alone would not change these bytes.
    remaining = _image_crop(output, target_region, crop_local=crop)
    assert min(remaining) == 255


def test_unpadded_word_snap_caps_margin_before_adjacent_control(tmp_path):
    """The scan cleanup margin must not consume a neighboring same-line word."""
    source = _make_scanned_pdf(
        tmp_path / "adjacent.pdf", rotation=0, crop=False, adjacent_control=True
    )
    with pymupdf.open(source) as doc:
        ops, target = _gui_word_snap_ops(doc[0])
        control = next(word for word in doc[0].get_text("words") if word[4] == "KEEP")
        before_control = _image_crop(source, pymupdf.Rect(control[:4]))

    output = tmp_path / "adjacent-redacted.pdf"
    engine.apply(source, ops, output=output)
    with pymupdf.open(output) as doc:
        assert TARGET not in doc[0].get_text()
        assert "KEEP" in doc[0].get_text()
    assert _image_crop(output, pymupdf.Rect(control[:4])) == before_control


def test_unpadded_word_snap_does_not_expand_into_diagonal_glyph(tmp_path):
    """A corner margin must not consume a nearby diagonal OCR glyph."""
    with pymupdf.open() as artwork:
        art_page = artwork.new_page(width=300, height=300)
        art_page.insert_text((100, 100), "A", fontsize=12)
        art_page.insert_text((108.2, 105.8), "B", fontsize=2)
        scan = art_page.get_pixmap(dpi=200, alpha=False)

    source = tmp_path / "diagonal.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_image(page.rect, pixmap=scan)
        page.insert_text((100, 100), "A", fontsize=12, render_mode=3)
        page.insert_text((108.2, 105.8), "B", fontsize=2, render_mode=3)
        doc.save(source)

    with pymupdf.open(source) as doc:
        page = doc[0]
        words = page.get_text("words")
        target = next(word for word in words if word[4] == "A")
        neighbor = next(word for word in words if word[4] == "B")
        target_rect = pymupdf.Rect(target[:4])
        neighbor_rect = pymupdf.Rect(neighbor[:4])
        expanded = redact_scope.expand_rects_to_glyphs(page, [target_rect])[0]
        assert not target_rect.intersects(neighbor_rect)
        assert not expanded.intersects(neighbor_rect)
        before_neighbor = _image_crop(source, neighbor_rect)

    output = tmp_path / "diagonal-redacted.pdf"
    engine.apply(
        source,
        [{"op": "redact", "page": 1, "rect": list(target_rect)}],
        output=output,
    )
    with pymupdf.open(output) as doc:
        assert "A" not in doc[0].get_text()
        assert "B" in doc[0].get_text()
    assert _image_crop(output, neighbor_rect) == before_neighbor
