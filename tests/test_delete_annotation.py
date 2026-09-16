"""delete_annotation and GUI-adjacent form/annot tests (Slice 5)."""

import json
import subprocess
import sys

import pymupdf
import pytest

from omepreview import engine, read
from omepreview.ops import OpError


@pytest.fixture()
def marked_pdf(tmp_path):
    """One-page PDF with a highlight annotation and a text form field."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Contract body text", fontsize=12)
    rects = page.search_for("Contract")
    annot = page.add_highlight_annot(rects[0])
    annot.set_info(content="review this clause")
    annot.update()
    widget = pymupdf.Widget()
    widget.field_name = "tenant_name"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect = pymupdf.Rect(160, 180, 400, 198)
    page.add_widget(widget)
    path = tmp_path / "marked.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_delete_annotation_removes_highlight(marked_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    result = engine.apply(
        marked_pdf,
        [{"op": "delete_annotation", "page": 1, "index": 0}],
        output=out,
    )
    assert result["applied"][0]["type"] == "Highlight"
    annots = read.extract(out)["pages"][0]["annotations"]
    assert annots == []


def test_delete_annotation_dry_run(marked_pdf):
    before = marked_pdf.read_bytes()
    result = engine.apply(
        marked_pdf,
        [{"op": "delete_annotation", "page": 1, "index": 0}],
        dry_run=True,
    )
    assert result["output"] is None
    assert result["applied"][0]["applied"] is False
    assert marked_pdf.read_bytes() == before
    assert len(read.extract(marked_pdf)["pages"][0]["annotations"]) == 1


def test_delete_annotation_bad_index(marked_pdf):
    with pytest.raises(OpError, match="out of range"):
        engine.apply(
            marked_pdf,
            [{"op": "delete_annotation", "page": 1, "index": 9}],
        )


def test_delete_multiple_annotations_same_page(marked_pdf, tmp_path):
    doc = pymupdf.open(marked_pdf)
    page = doc[0]
    rects = page.search_for("body")
    annot = page.add_underline_annot(rects[0])
    annot.update()
    out_path = tmp_path / "multi.pdf"
    doc.save(str(out_path))
    doc.close()

    engine.apply(
        out_path,
        [
            {"op": "delete_annotation", "page": 1, "index": 0},
            {"op": "delete_annotation", "page": 1, "index": 1},
        ],
        output=out_path,
    )
    assert read.extract(out_path)["pages"][0]["annotations"] == []


def test_read_lists_annotation_index(marked_pdf):
    annots = read.extract(marked_pdf)["pages"][0]["annotations"]
    assert annots[0]["index"] == 0
    assert annots[0]["type"] == "Highlight"


def test_fill_field_still_works(marked_pdf, tmp_path):
    out = tmp_path / "filled.pdf"
    engine.apply(
        marked_pdf,
        [{"op": "fill_field", "field": "tenant_name", "value": "Jane Doe"}],
        output=out,
    )
    fields = read.form_fields(out)
    assert fields[0]["value"] == "Jane Doe"


def test_cli_delete_annotation(marked_pdf, tmp_path):
    out = tmp_path / "cli.pdf"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "omepreview.cli",
            "delete-annotation",
            str(marked_pdf),
            "--page",
            "1",
            "--index",
            "0",
            "-o",
            str(out),
            "--confirm",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(proc.stdout)
    assert report["applied"][0]["type"] == "Highlight"
    assert read.extract(out)["pages"][0]["annotations"] == []
