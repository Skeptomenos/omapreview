"""Validate O0 corpus/oracle; these tests do not claim OCR is implemented."""
import shutil

import pymupdf as fitz
import pytest

from ocr_fixtures import SCAN, assert_saved_copy, build, fingerprint, inspect_pair


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("o0") / "fixtures"
    return root, build(root)


def test_generated_manifest_and_source_traits(corpus):
    root, cases = corpus
    assert len(cases) == 19
    for case in cases.values():
        assert fingerprint(root / case["file"]) == case["sha256"]
    with fitz.open(root / "clean.pdf") as d:
        assert not d[0].get_text().strip()
        assert len(d[0].get_images()) == 1
    with fitz.open(root / "mixed_page.pdf") as d:
        assert "DIGITAL CONTROL" in d[0].get_text()
        assert SCAN not in d[0].get_text()
        assert d[0].get_images()
    with fitz.open(root / "geometry.pdf") as d:
        assert [p.rotation for p in d] == [0, 90, 180, 270]
        assert all(p.cropbox == fitz.Rect(20, 30, 575, 812) for p in d)
    with fitz.open(root / "existing_ocr.pdf") as d:
        assert SCAN in d[0].get_text()
        assert d[0].get_texttrace()[0]["type"] == 3


def test_refusal_fixtures_have_real_structures(corpus):
    root, _ = corpus
    for name in ("encrypted", "owner_encrypted"):
        with fitz.open(root / f"{name}.pdf") as d:
            # Owner-only encryption is not needs_pass: inspect trailer too.
            assert d.xref_get_key(-1, "Encrypt")[0] != "null"
    with fitz.open(root / "signature_structure.pdf") as d:
        w = next(d[0].widgets())
        assert w.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE
        kind, value = d.xref_get_key(w.xref, "V")
        assert kind == "xref"
        sig = int(value.split()[0])
        assert d.xref_get_key(sig, "ByteRange")[0] == "array"
        assert d.xref_get_key(sig, "Contents")[0] != "null"
    with fitz.open(root / "unsigned_signature.pdf") as d:
        assert next(d[0].widgets()).field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE
    with fitz.open(root / "redaction.pdf") as d:
        page = d[0]
        assert next(page.annots()).type[0] == fitz.PDF_ANNOT_REDACT
    with fitz.open(root / "embedded.pdf") as d:
        assert d.embfile_get("synthetic.txt") == b"SYNTHETIC ATTACHMENT\n"
    with fitz.open(root / "widget.pdf") as d:
        assert next(d[0].widgets()).field_value == "SYNTHETIC VALUE"
    with fitz.open(root / "comment.pdf") as d:
        page = d[0]
        assert next(page.annots()).info["content"] == "SYNTHETIC NOTE"
    with fitz.open(root / "link.pdf") as d:
        assert d[0].get_links()[0]["uri"] == "https://example.invalid/synthetic"


def test_blank_and_faint_are_distinct(corpus):
    root, _ = corpus
    with fitz.open(root / "blank.pdf") as d:
        assert set(d[0].get_pixmap(dpi=100).samples) == {255}
    with fitz.open(root / "poor.pdf") as d:
        assert min(d[0].get_pixmap(dpi=100).samples) < 255
    with fitz.open(root / "photo.pdf") as d:
        assert not d[0].get_text().strip()
        assert min(d[0].get_pixmap(dpi=100).samples) < 255


def test_builder_refuses_overwriting_corpus(corpus):
    root, cases = corpus
    with pytest.raises(FileExistsError):
        build(root)
    assert fingerprint(root / "clean.pdf") == cases["clean"]["sha256"]


def test_oracle_detects_missing_ocr_despite_identical_render(corpus, tmp_path):
    root, cases = corpus
    output = tmp_path / "not-ocr.pdf"
    shutil.copyfile(root / "clean.pdf", output)
    result = inspect_pair(root / "clean.pdf", output, cases["clean"]["pages"])[0]
    assert result["pixels_equal"] and result["geometry_equal"]
    assert not result["expected_text"]
    with pytest.raises(AssertionError, match="expected_text"):
        assert_saved_copy(root / "clean.pdf", output, cases["clean"]["pages"])


@pytest.mark.parametrize("damage", ["rotation", "crop", "pixels", "page_count", "text"])
def test_oracle_rejects_saved_damage(corpus, tmp_path, damage):
    root, cases = corpus
    source = root / "native.pdf"
    output = tmp_path / "damaged.pdf"
    with fitz.open(source) as d:
        if damage == "rotation":
            d[0].set_rotation(90)
        elif damage == "crop":
            d[0].set_cropbox(fitz.Rect(20, 30, 575, 812))
        elif damage == "pixels":
            d[0].draw_rect((50, 50, 100, 100), fill=(0, 0, 0))
        elif damage == "page_count":
            d.new_page()
        else:
            # Invisible pre-existing text must not be silently changed.
            p = d[0]
            p.add_redact_annot(p.rect)
            p.apply_redactions()
        d.save(output)
    with pytest.raises(AssertionError):
        assert_saved_copy(source, output, cases["native"]["pages"])


def test_oracle_accepts_unchanged_native_copy(corpus, tmp_path):
    root, cases = corpus
    output = tmp_path / "copy.pdf"
    shutil.copyfile(root / "native.pdf", output)
    assert_saved_copy(root / "native.pdf", output, cases["native"]["pages"])


@pytest.mark.parametrize("damage", ["metadata", "xmp", "outline", "outline_destination", "labels", "trimbox", "bleedbox", "artbox"])
def test_oracle_rejects_nonvisual_property_loss(corpus, tmp_path, damage):
    root, _ = corpus
    source = root / "geometry.pdf"
    output = tmp_path / "damaged-structure.pdf"
    with fitz.open(source) as d:
        if damage == "metadata":
            d.set_metadata({})
        elif damage == "xmp":
            d.del_xml_metadata()
        elif damage == "outline":
            d.set_toc([])
        elif damage == "outline_destination":
            toc = d.get_toc(False)
            toc[0][3]["to"] = fitz.Point(100, 200)
            d.set_toc(toc)
        elif damage == "labels":
            d.set_page_labels([])
        else:
            getattr(d[0], "set_" + damage)(fitz.Rect(20, 30, 575, 812))
        d.save(output)
    with pytest.raises(AssertionError):
        assert_saved_copy(source, output, [{}] * 4)
