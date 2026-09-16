"""Batch B regressions for proposal intent, strict inputs and MCP safety."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from omepreview import read, render
from omepreview.gui import Editor
from omepreview.mcp_server import apply_ops, redact
from omepreview.ops import OpError, validate
from omepreview.redact_io import redact_item_to_op


def _make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 80), "PROPOSAL SECRET", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def _make_sized_pdf(path: Path, width: float, height: float) -> Path:
    doc = pymupdf.open()
    doc.new_page(width=width, height=height)
    doc.save(str(path))
    doc.close()
    return path


def _make_object_target_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 80), "Contract body text", fontsize=14)
    annot = page.add_highlight_annot(page.search_for("Contract")[0])
    annot.update()
    widget = pymupdf.Widget()
    widget.field_name = "answer"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect = pymupdf.Rect(160, 180, 400, 198)
    page.add_widget(widget)
    doc.save(str(path))
    doc.close()
    return path


def _load_proposal_in_subprocess(pdf: Path, proposal: Path) -> dict:
    script = """
import json
import sys

from omepreview.gui import Editor

editor = Editor(sys.argv[1], None)
try:
    try:
        editor._load_proposals(sys.argv[2])
    except Exception as exc:
        result = {"error": str(exc), "pending": editor.pending}
    else:
        result = {"pending": editor.pending, "ops": editor.to_ops()}
    print("PROPOSAL_RESULT=" + json.dumps(result))
finally:
    editor.doc.close()
"""
    completed = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", script, str(pdf), str(proposal)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result_line = next(
        line for line in completed.stdout.splitlines() if line.startswith("PROPOSAL_RESULT=")
    )
    return json.loads(result_line.removeprefix("PROPOSAL_RESULT="))


def test_apply_now_rejects_string_booleans():
    with pytest.raises(OpError, match="apply_now must be a JSON boolean"):
        validate({"op": "redact", "page": 1, "match": "SECRET", "apply_now": "false"})


@pytest.mark.parametrize(
    "op, message",
    [
        (
            {"op": "redact", "page": 1, "rect": [0, 0, math.nan, 10]},
            "finite",
        ),
        (
            {"op": "shape", "page": 1, "shape": "rect", "rect": [0, 0, 10, 10], "color": [2, 0, 0]},
            "at most 1",
        ),
        (
            {"op": "ink", "page": 1, "strokes": [[[0, 0], [1, 1]]], "width": math.inf},
            "finite",
        ),
        (
            {"op": "place_signature", "page": 1, "at": [0, 0], "width": 0},
            "greater than 0",
        ),
    ],
)
def test_operation_numbers_are_finite_and_bounded(op, message):
    with pytest.raises(OpError, match=message):
        validate(op)


@pytest.mark.parametrize("grid", [0, -1, math.inf, math.nan, 10_001])
@pytest.mark.parametrize("scale", [1.0])
def test_snapshot_rejects_bad_grid_before_writing(tmp_path, grid, scale):
    pdf = _make_pdf(tmp_path / "source.pdf")
    output = tmp_path / "snapshot.png"
    with pytest.raises(ValueError):
        render.snapshot(pdf, output=output, grid=grid, scale=scale)
    assert not output.exists()


@pytest.mark.parametrize("scale", [0, -1, math.inf, math.nan, 4.01])
def test_snapshot_rejects_bad_scale_before_writing(tmp_path, scale):
    pdf = _make_pdf(tmp_path / "source.pdf")
    output = tmp_path / "snapshot.png"
    with pytest.raises(ValueError):
        render.snapshot(pdf, output=output, scale=scale)
    assert not output.exists()


def test_snapshot_rejects_pixel_budget_before_allocation_and_preserves_output(
    tmp_path, monkeypatch
):
    pdf = _make_sized_pdf(tmp_path / "huge.pdf", 20_000, 20_000)
    output = tmp_path / "snapshot.png"
    output.write_bytes(b"existing output")
    raster_called = False

    def observe_raster(*_args, **_kwargs):
        nonlocal raster_called
        raster_called = True
        raise AssertionError("raster allocation must not be reached")

    monkeypatch.setattr(render, "raster_page", observe_raster)
    with pytest.raises(ValueError, match=r"80,000x80,000.*6,400,000,000"):
        render.snapshot(pdf, output=output, scale=4)

    assert not raster_called
    assert output.read_bytes() == b"existing output"


def test_snapshot_rejects_grid_budget_before_drawing_and_preserves_output(
    tmp_path, monkeypatch
):
    pdf = _make_sized_pdf(tmp_path / "huge.pdf", 20_000, 20_000)
    output = tmp_path / "snapshot.png"
    output.write_bytes(b"existing output")
    raster_called = False

    def observe_raster(*_args, **_kwargs):
        nonlocal raster_called
        raster_called = True
        raise AssertionError("raster allocation must not be reached")

    monkeypatch.setattr(render, "raster_page", observe_raster)
    with pytest.raises(ValueError, match=r"39,998 lines.*79,996 labels"):
        render.snapshot(pdf, output=output, grid=1, scale=0.05)

    assert not raster_called
    assert output.read_bytes() == b"existing output"


def test_snapshot_and_grid_budget_boundaries_are_allowed():
    assert render._validate_snapshot_budget(10_000, 4_000, 1) == (10_000, 4_000)
    assert render._validate_snapshot_budget(render.MAX_SNAPSHOT_DIMENSION, 1, 1) == (
        render.MAX_SNAPSHOT_DIMENSION,
        1,
    )
    assert render._validate_grid_budget(render.MAX_GRID_LINES + 1, 1, 1) == (
        render.MAX_GRID_LINES,
        0,
    )


@pytest.mark.parametrize(
    "width, height",
    [
        (render.MAX_SNAPSHOT_DIMENSION + 1, 1),
        (10_000, 4_001),
    ],
)
def test_snapshot_rejects_each_raster_budget(width, height):
    with pytest.raises(ValueError, match="snapshot would render"):
        render._validate_snapshot_budget(width, height, 1)


def test_grid_rejects_one_line_over_budget_before_shape_creation():
    shape_created = False

    class FakePage:
        rect = pymupdf.Rect(0, 0, render.MAX_GRID_LINES + 2, 1)

        def new_shape(self):
            nonlocal shape_created
            shape_created = True
            raise AssertionError("page drawing must not be reached")

    with pytest.raises(ValueError, match=f"{render.MAX_GRID_LINES + 1:,} lines"):
        render._draw_grid(FakePage(), 1)
    assert not shape_created


def test_redact_conversion_preserves_fill_and_apply_now():
    op = redact_item_to_op(
        {
            "kind": "redact",
            "page": 0,
            "x0": 30,
            "y0": 40,
            "x1": 10,
            "y1": 20,
            "fill": [1, 1, 1],
            "apply_now": False,
            "match": "metadata only",
        }
    )
    assert op == {
        "op": "redact",
        "page": 1,
        "rect": [10.0, 20.0, 30.0, 40.0],
        "fill": [1, 1, 1],
        "apply_now": False,
    }


def test_proposal_round_trip_preserves_supported_intent(tmp_path):
    pdf = _make_pdf(tmp_path / "proposal.pdf")
    proposal = tmp_path / "proposal.json"
    source_ops = [
        {
            "op": "redact",
            "page": 1,
            "rect": [30, 60, 210, 95],
            "apply_now": False,
            "fill": [1, 1, 1],
        },
        {
            "op": "highlight",
            "page": 1,
            "rect": [220, 100, 420, 130],
            "style": "underline",
        },
        {
            "op": "text_box",
            "page": 1,
            "rect": [10, 200, 310, 248],
            "text": "fixed geometry",
            "size": 18,
        },
    ]
    proposal.write_text(json.dumps(source_ops), encoding="utf-8")

    editor = Editor(str(pdf), str(proposal))
    try:
        emitted = editor.to_ops()
        assert emitted[:3] == source_ops
        assert editor.item_rect(editor.pending[2]) == (10.0, 200.0, 310.0, 248.0)
        result = editor.save_pending()
        assert result["redacted_copy"]
        with pymupdf.open(result["path"]) as saved:
            assert "PROPOSAL SECRET" in saved[0].get_text()
        annotations = read.extract(result["path"])["pages"][0]["annotations"]
        types = {annotation["type"] for annotation in annotations}
        assert "Redact" in types
        assert "Underline" in types
        with pymupdf.open(result["path"]) as saved:
            redact = next(
                annotation
                for annotation in (saved[0].annots() or [])
                if annotation.type[1] == "Redact"
            )
            assert redact.colors["fill"] == [1.0, 1.0, 1.0]
    finally:
        if editor.doc is not None and not editor.doc.is_closed:
            editor.doc.close()


def test_proposal_object_targets_keep_their_page_alive(tmp_path):
    pdf = _make_object_target_pdf(tmp_path / "object-targets.pdf")
    proposal = tmp_path / "object-targets.json"
    source_ops = [
        {
            "op": "fill_field",
            "field": "answer",
            "value": "verified",
            "page": 1,
            "rect": [160, 180, 400, 198],
        },
        {"op": "delete_annotation", "page": 1, "index": 0},
    ]
    proposal.write_text(json.dumps(source_ops), encoding="utf-8")

    result = _load_proposal_in_subprocess(pdf, proposal)

    assert result["ops"] == source_ops
    assert result["pending"][0]["rect"] == [160.0, 180.0, 400.0, 198.0]
    assert result["pending"][1]["annot_type"] == "Highlight"
    assert len(result["pending"][1]["rect"]) == 4


@pytest.mark.parametrize(
    "source_ops, message",
    [
        (
            [
                {"op": "delete_annotation", "page": 1, "index": 0},
                {"op": "fill_field", "field": "missing", "value": "x"},
            ],
            "no form field named 'missing'",
        ),
        (
            [
                {"op": "fill_field", "field": "answer", "value": "verified"},
                {"op": "delete_annotation", "page": 1, "index": 9},
            ],
            "annotation index 9 is out of range",
        ),
    ],
)
def test_invalid_proposal_object_target_is_atomic(tmp_path, source_ops, message):
    pdf = _make_object_target_pdf(tmp_path / "object-targets.pdf")
    proposal = tmp_path / "invalid-object-target.json"
    proposal.write_text(json.dumps(source_ops), encoding="utf-8")

    result = _load_proposal_in_subprocess(pdf, proposal)

    assert message in result["error"]
    assert result["pending"] == []


def test_unsupported_proposal_is_rejected_without_partial_load(tmp_path):
    pdf = _make_pdf(tmp_path / "proposal.pdf")
    proposal = tmp_path / "unsupported.json"
    proposal.write_text(
        json.dumps(
            [
                {"op": "redact", "page": 1, "rect": [10, 10, 30, 30]},
                {"op": "rotate_pages", "pages": [1], "degrees": 90},
            ]
        ),
        encoding="utf-8",
    )
    editor = Editor(str(pdf), None)
    sentinel = {"kind": "note", "page": 0, "x": 1, "y": 1, "text": "existing"}
    editor.pending.append(sentinel)
    try:
        with pytest.raises(OpError, match="proposal rejected.*rotate_pages"):
            editor._load_proposals(str(proposal))
        assert editor.pending == [sentinel]
    finally:
        editor.doc.close()


def test_generic_mcp_apply_defaults_to_dry_run_and_requires_boolean(tmp_path):
    pdf = _make_pdf(tmp_path / "source.pdf")
    output = tmp_path / "out.pdf"
    op = [{"op": "redact", "page": 1, "match": "SECRET"}]

    result = apply_ops(str(pdf), op, output=str(output))
    assert result["output"] is None
    assert "needs_confirmation" in result
    assert not output.exists()
    assert "SECRET" in pymupdf.open(pdf)[0].get_text()

    with pytest.raises(ValueError, match="dry_run must be a boolean"):
        apply_ops(str(pdf), op, output=str(output), dry_run="false")
    assert not output.exists()

    applied = apply_ops(str(pdf), op, output=str(output), dry_run=False)
    assert applied["output"] == str(output)
    assert "SECRET" not in pymupdf.open(output)[0].get_text()


def test_dedicated_redact_confirmation_is_strict_and_safe(tmp_path):
    pdf = _make_pdf(tmp_path / "source.pdf")
    output = tmp_path / "out.pdf"
    op = {"page": 1, "match": "SECRET", "output": str(output)}

    with pytest.raises(ValueError, match="confirm must be a boolean"):
        redact(str(pdf), **op, confirm="false")
    assert not output.exists()

    result = redact(str(pdf), **op)
    assert result["output"] is None
    assert not output.exists()
    assert "SECRET" in pymupdf.open(pdf)[0].get_text()

    redact(str(pdf), **op, confirm=True)
    assert "SECRET" not in pymupdf.open(output)[0].get_text()
