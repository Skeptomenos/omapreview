"""Saved graphics must use the same crop-local frame as GUI/CLI proposals."""

import datetime

import pymupdf as fitz
import pytest

from omepreview import engine, signature


CROPS = [(37, 61, 537, 661), (83, 29, 463, 609)]
ROTATIONS = [0, 90, 180, 270]


def make_pdf(path, rotation, crop):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=700)
        page.insert_text((crop[0] + 50, crop[1] + 100), "SECRET", fontsize=20)
        page.insert_text((crop[0] + 50, crop[1] + 160), "KEEP", fontsize=14)
        page.set_cropbox(fitz.Rect(crop))
        page.set_rotation(rotation)
        doc.set_metadata({"title": "coordinate regression", "author": "synthetic"})
        doc.save(path)


@pytest.fixture
def isolated_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(signature, "store_dir", lambda **kw: tmp_path)
    monkeypatch.setattr(signature, "legacy_store_dir", lambda: tmp_path)
    svg = tmp_path / "vector.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40">'
                   '<rect width="100" height="40" fill="#0044cc"/></svg>')
    with fitz.open(svg) as image:
        image[0].get_pixmap(alpha=True).save(tmp_path / "bitmap.png")


def check_geometry(doc, rotation, crop):
    page = doc[0]
    assert page.rotation == rotation
    assert tuple(page.cropbox) == crop
    assert tuple(page.mediabox) == (0, 0, 600, 700)
    assert doc.metadata["title"] == "coordinate regression"
    assert doc.metadata["author"] == "synthetic"
    return page


@pytest.mark.parametrize("crop", CROPS)
@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("name", ["vector", "bitmap"])
@pytest.mark.parametrize("date", [False, True])
def test_signature_saved_geometry_and_pixels(tmp_path, isolated_signature, crop, rotation, name, date):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    make_pdf(source, rotation, crop)
    before = source.read_bytes()
    op = {"op": "place_signature", "page": 1, "at": [60, 230],
          "width": 100, "signature": name, "date": date}
    result = engine.apply(source, [op], output)
    assert result["applied"][0]["rect"] == [60, 230, 160, 270]
    assert source.read_bytes() == before
    with fitz.open(output) as doc:
        page = check_geometry(doc, rotation, crop)
        if name == "vector":
            blue = [d for d in page.get_drawings() if d["fill"] and d["fill"][2] > 0.7]
            assert len(blue) == 1
            assert tuple(blue[0]["rect"]) == pytest.approx((60, 230, 160, 270), abs=0.01)
        else:
            assert tuple(page.get_image_info()[0]["bbox"]) == pytest.approx((60, 230, 160, 270), abs=0.01)
        # Verify pixels in the displayed (rotated) target, not only path metadata.
        center = fitz.Point(110, 250) * page.rotation_matrix
        assert page.get_pixmap().pixel(round(center.x), round(center.y)) == (0, 68, 204)
        if date:
            word = page.search_for(datetime.date.today().isoformat())
            assert len(word) == 1
            assert word[0].x0 == pytest.approx(60, abs=0.01)
            assert word[0].y0 == pytest.approx(271.25, abs=0.02)
        # Compare the entire reopened normalized raster with an independently
        # generated 0-degree control, including signature edges and date text.
        page.set_rotation(0)
        actual_pixels = page.get_pixmap().samples
    control, control_output = tmp_path / "control.pdf", tmp_path / "control-output.pdf"
    make_pdf(control, 0, crop)
    engine.apply(control, [op], control_output)
    with fitz.open(control_output) as doc:
        assert actual_pixels == doc[0].get_pixmap().samples


@pytest.mark.parametrize("crop", CROPS)
@pytest.mark.parametrize("rotation", ROTATIONS)
@pytest.mark.parametrize("apply_now", [False, True])
@pytest.mark.parametrize("by_match", [False, True])
def test_redaction_saved_fill_and_intent(tmp_path, crop, rotation, apply_now, by_match):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    make_pdf(source, rotation, crop)
    before = source.read_bytes()
    with fitz.open(source) as doc:
        target = doc[0].search_for("SECRET")[0]
    op = {"op": "redact", "page": 1, "apply_now": apply_now,
          **({"match": "SECRET"} if by_match else {"rect": list(target)})}
    result = engine.apply(source, [op], output)
    resolved = fitz.Rect(result["applied"][0]["rects"][0])
    assert tuple(resolved) == pytest.approx(tuple(target), abs=0.01)
    assert source.read_bytes() == before
    with fitz.open(output) as doc:
        page = check_geometry(doc, rotation, crop)
        assert "KEEP" in page.get_text()
        if apply_now:
            assert "SECRET" not in page.get_text()
            assert not list(page.annots() or [])
            fills = [d for d in page.get_drawings() if d["fill"] == (0, 0, 0)]
            assert len(fills) == 1
            assert tuple(fills[0]["rect"]) == pytest.approx(tuple(resolved), abs=0.01)
            center = (resolved.tl + resolved.br) / 2 * page.rotation_matrix
            assert page.get_pixmap().pixel(round(center.x), round(center.y)) == (0, 0, 0)
        else:
            assert "SECRET" in page.get_text()
            annots = list(page.annots())
            assert len(annots) == 1 and annots[0].type[0] == fitz.PDF_ANNOT_REDACT
            assert tuple(annots[0].rect) == pytest.approx(tuple(resolved), abs=0.01)
        page.set_rotation(0)
        actual_pixels = page.get_pixmap().samples
    control, control_output = tmp_path / "control.pdf", tmp_path / "control-output.pdf"
    make_pdf(control, 0, crop)
    engine.apply(control, [op], control_output)
    with fitz.open(control_output) as doc:
        assert actual_pixels == doc[0].get_pixmap().samples


@pytest.mark.parametrize("operation", ["signature", "date", "redact_add", "redact_apply"])
def test_rotation_restored_when_write_raises(tmp_path, isolated_signature, monkeypatch, operation):
    source = tmp_path / "source.pdf"
    make_pdf(source, 270, CROPS[0])
    before = source.read_bytes()
    def fail(page, *args, **kwargs):
        assert page.rotation == 0
        raise RuntimeError("injected writer failure")
    if operation == "signature":
        monkeypatch.setattr(signature, "insert_on_page", fail)
    elif operation == "date":
        monkeypatch.setattr(fitz.Page, "insert_text", fail)
    elif operation == "redact_add":
        monkeypatch.setattr(fitz.Page, "add_redact_annot", fail)
    else:
        monkeypatch.setattr(fitz.Page, "apply_redactions", fail)
    op = ({"op": "place_signature", "page": 1, "at": [60, 230], "width": 100,
           "signature": "vector", "date": operation == "date"}
          if operation in ("signature", "date") else
          {"op": "redact", "page": 1, "match": "SECRET", "apply_now": True})
    with fitz.open(source) as doc:
        page = doc[0]
        monkeypatch.setattr(engine, "_page", lambda doc, number: page)
        with pytest.raises(RuntimeError, match="injected"):
            if op["op"] == "place_signature":
                engine._apply_place_signature(doc, op)
            else:
                engine._apply_redact(doc, op, dry_run=False)
        check_geometry(doc, 270, CROPS[0])
    assert source.read_bytes() == before


@pytest.mark.parametrize("in_place", [False, True])
@pytest.mark.parametrize("operation", ["signature", "redact"])
def test_writer_failure_does_not_publish(tmp_path, isolated_signature, monkeypatch, in_place, operation):
    source = tmp_path / "source.pdf"
    make_pdf(source, 180, CROPS[1])
    output = source if in_place else tmp_path / "existing.pdf"
    if not in_place:
        output.write_bytes(b"existing output must survive")
    before_source, before_output = source.read_bytes(), output.read_bytes()
    def fail(page, *args, **kwargs):
        assert page.rotation == 0
        raise RuntimeError("injected writer failure")
    if operation == "signature":
        monkeypatch.setattr(signature, "insert_on_page", fail)
        op = {"op": "place_signature", "page": 1, "at": [60, 230], "signature": "vector"}
    else:
        monkeypatch.setattr(fitz.Page, "apply_redactions", fail)
        op = {"op": "redact", "page": 1, "match": "SECRET"}
    with pytest.raises(RuntimeError, match="injected"):
        engine.apply(source, [op], output)
    assert source.read_bytes() == before_source
    assert output.read_bytes() == before_output


@pytest.mark.parametrize("rotation", ROTATIONS)
def test_dry_run_keeps_source_and_existing_output(tmp_path, isolated_signature, rotation):
    source, output = tmp_path / "source.pdf", tmp_path / "existing.pdf"
    make_pdf(source, rotation, CROPS[0])
    output.write_bytes(b"existing output")
    before = source.read_bytes()
    result = engine.apply(source, [
        {"op": "place_signature", "page": 1, "at": [60, 230], "signature": "vector", "date": True},
        {"op": "redact", "page": 1, "match": "SECRET"},
    ], output, dry_run=True)
    assert result["output"] is None
    assert not any(op["applied"] for op in result["applied"])
    assert source.read_bytes() == before
    assert output.read_bytes() == b"existing output"
