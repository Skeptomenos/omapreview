"""Batch D regressions for execution order, identity, form targets and links."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from omepreview import engine, read
from omepreview.gui import Editor
from omepreview.mcp_server import fill_field
from omepreview.ops import OpError, operation_catalog


def _save(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(str(path))
    doc.close()
    return path


def _make_annot_pages(path: Path) -> Path:
    doc = pymupdf.open()
    for number in range(1, 4):
        page = doc.new_page()
        page.insert_text((40, 60), f"PAGE_{number}", fontsize=12)
        for suffix in ("A", "B"):
            annot = page.add_text_annot(pymupdf.Point(40, 90 if suffix == "A" else 120), f"P{number}_{suffix}")
            annot.update()
    return _save(doc, path)


def _make_text_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 100), "SECRET_TARGET", fontsize=14)
    page.insert_text((40, 400), "KEEP_VISIBLE", fontsize=14)
    return _save(doc, path)


def _make_repeated_form_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for number in range(1, 3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((40, 70), f"PAGE_{number}", fontsize=12)
        widget = pymupdf.Widget()
        widget.field_name = "same_name"
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(100, 100 + number * 30, 300, 120 + number * 30)
        widget.field_value = ""
        page.add_widget(widget)
    return _save(doc, path)


def _make_link_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for number in range(1, 5):
        page = doc.new_page(width=595, height=842)
        page.insert_text((40, 60), f"LINK_PAGE_{number}", fontsize=12)
    links = ((1, 2), (2, 4), (3, 1), (4, 4))
    for source, target in links:
        doc[source - 1].insert_link(
            {
                "kind": pymupdf.LINK_GOTO,
                "from": pymupdf.Rect(20, 20, 120, 45),
                "page": target - 1,
                "to": pymupdf.Point(40, 60),
            }
        )
    doc[0].insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(140, 20, 240, 45),
            "uri": "https://example.invalid/synthetic",
        }
    )
    return _save(doc, path)


def _goto_targets(path: Path, pages: list[int]) -> list[dict]:
    doc = pymupdf.open(path)
    try:
        return [link for number in pages for link in doc[number - 1].get_links()]
    finally:
        doc.close()


def test_annotation_delete_runs_do_not_cross_page_move(tmp_path):
    source = _make_annot_pages(tmp_path / "annotations.pdf")
    output = tmp_path / "out.pdf"

    engine.apply(
        source,
        [
            {"op": "delete_annotation", "page": 1, "index": 0},
            {"op": "move_pages", "pages": [1], "after": 2},
            {"op": "delete_annotation", "page": 1, "index": 1},
        ],
        output=output,
    )

    doc = pymupdf.open(output)
    try:
        contents = [
            [annot.info.get("content", "") for annot in (page.annots() or [])]
            for page in doc
        ]
    finally:
        doc.close()
    assert contents[0] == ["P2_A"]
    assert contents[1] == ["P1_B"]
    assert contents[2] == ["P3_A", "P3_B"]


def test_dry_run_simulates_redaction_dependencies_without_publishing(tmp_path):
    source = _make_text_pdf(tmp_path / "source.pdf")
    before = source.read_bytes()
    operations = [
        {"op": "redact", "page": 1, "match": "SECRET_TARGET"},
        {"op": "highlight", "page": 1, "match": "KEEP_VISIBLE"},
    ]

    dry = engine.apply(source, operations, output=tmp_path / "dry.pdf", dry_run=True)
    assert dry["output"] is None
    assert all(item["applied"] is False for item in dry["applied"])
    assert source.read_bytes() == before
    assert not (tmp_path / "dry.pdf").exists()

    committed = tmp_path / "committed.pdf"
    result = engine.apply(source, operations, output=committed)
    assert result["output"] == str(committed)
    assert "SECRET_TARGET" not in pymupdf.open(committed)[0].get_text()
    assert read.extract(committed)["pages"][0]["annotations"][0]["type"] == "Highlight"


def test_dry_run_rejects_sequence_that_commit_would_reject(tmp_path):
    source = _make_text_pdf(tmp_path / "source.pdf")
    operations = [
        {"op": "redact", "page": 1, "match": "SECRET_TARGET"},
        {"op": "highlight", "page": 1, "match": "SECRET_TARGET"},
    ]
    with pytest.raises(OpError, match="not found"):
        engine.apply(source, operations, dry_run=True)
    with pytest.raises(OpError, match="not found"):
        engine.apply(source, operations, output=tmp_path / "out.pdf")
    assert "SECRET_TARGET" in pymupdf.open(source)[0].get_text()


def test_redaction_verification_follows_page_after_move_and_crop(tmp_path):
    source = _make_text_pdf(tmp_path / "source.pdf")
    second = pymupdf.open()
    second.new_page(width=595, height=842).insert_text((40, 100), "CONTROL_PAGE", fontsize=14)
    second_path = tmp_path / "two.pdf"
    second.save(str(second_path))
    second.close()
    # Keep the target on page 1 and add a control page at page 2.
    merged = pymupdf.open(source)
    control = pymupdf.open(second_path)
    merged.insert_pdf(control)
    control.close()
    merged.save(str(tmp_path / "source-two.pdf"))
    merged.close()
    source = tmp_path / "source-two.pdf"
    page = pymupdf.open(source)[0]
    target = list(page.search_for("SECRET_TARGET")[0])
    page.parent.close()

    output = tmp_path / "out.pdf"
    engine.apply(
        source,
        [
            {"op": "redact", "page": 1, "rect": target},
            {"op": "move_pages", "pages": [1], "after": 2},
            {"op": "crop_pages", "pages": [2], "rect": [0, 0, 500, 700]},
        ],
        output=output,
    )
    doc = pymupdf.open(output)
    try:
        assert "SECRET_TARGET" not in doc[1].get_text()
        assert "CONTROL_PAGE" in doc[0].get_text()
        assert doc[1].rect.width == pytest.approx(500)
    finally:
        doc.close()


def test_redaction_verification_translates_after_nonzero_crop(tmp_path):
    source = tmp_path / "source.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 70), "SECRET_TOP", fontsize=14)
    page.insert_text((50, 100), "KEEP_BELOW", fontsize=14)
    control = doc.new_page(width=595, height=842)
    control.insert_text((40, 60), "CONTROL_PAGE", fontsize=12)
    doc.save(str(source))
    doc.close()
    current = pymupdf.open(source)
    target = list(current[0].search_for("SECRET_TOP")[0])
    current.close()

    output = tmp_path / "out.pdf"
    engine.apply(
        source,
        [
            {"op": "redact", "page": 1, "rect": target},
            {"op": "move_pages", "pages": [1], "after": 2},
            {"op": "crop_pages", "pages": [2], "rect": [0, 30, 595, 842]},
        ],
        output=output,
    )
    saved = pymupdf.open(output)
    try:
        assert "SECRET_TOP" not in saved[1].get_text()
        assert "KEEP_BELOW" in saved[1].get_text()
        assert "CONTROL_PAGE" in saved[0].get_text()
    finally:
        saved.close()


def test_repeated_form_name_requires_selector_and_targets_page(tmp_path):
    source = _make_repeated_form_pdf(tmp_path / "forms.pdf")
    rect = [100, 160, 300, 180]
    with pytest.raises(OpError, match="ambiguous"):
        engine.apply(source, [{"op": "fill_field", "field": "same_name", "value": "x"}])

    output = tmp_path / "filled.pdf"
    result = engine.apply(
        source,
        [{"op": "fill_field", "field": "same_name", "value": "page two", "page": 2, "rect": rect}],
        output=output,
    )
    assert result["applied"][0]["page"] == 2
    assert result["applied"][0]["rect"] == rect
    fields = read.form_fields(output)
    assert [(field["page"], field["value"]) for field in fields] == [(1, ""), (2, "page two")]

    with pytest.raises(OpError, match="no form field named"):
        engine.apply(
            source,
            [{"op": "fill_field", "field": "same_name", "value": "x", "page": 2, "rect": [1, 1, 2, 2]}],
        )


def test_form_selector_survives_cli_mcp_and_gui_conversion(tmp_path):
    source = _make_repeated_form_pdf(tmp_path / "forms.pdf")
    rect = [100, 160, 300, 180]
    cli_output = tmp_path / "cli.pdf"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "omepreview.cli",
            "fill",
            str(source),
            "--field",
            "same_name",
            "cli value",
            "--page",
            "2",
            "--rect",
            ",".join(str(value) for value in rect),
            "--output",
            str(cli_output),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert read.form_fields(cli_output)[1]["value"] == "cli value"

    mcp_output = tmp_path / "mcp.pdf"
    fill_field(str(source), "same_name", "mcp value", page=2, rect=rect, output=str(mcp_output))
    assert read.form_fields(mcp_output)[1]["value"] == "mcp value"

    proposal = tmp_path / "proposal.json"
    proposal.write_text(
        json.dumps([{"op": "fill_field", "field": "same_name", "value": "gui value", "page": 2, "rect": rect}]),
        encoding="utf-8",
    )
    editor = Editor(str(source), str(proposal))
    try:
        assert editor.to_ops() == json.loads(proposal.read_text(encoding="utf-8"))
    finally:
        editor.doc.close()


def test_operation_catalog_advertises_form_selectors():
    fill = next(item for item in operation_catalog()["operations"] if item["name"] == "fill_field")
    assert set(fill["fields"]) == {"op", "field", "value", "page", "rect"}


@pytest.mark.parametrize("selected", [[1, 2], [3, 1], [1, 1, 2]])
def test_copied_and_extracted_pages_remap_internal_links(selected, tmp_path):
    source = _make_link_pdf(tmp_path / "links.pdf")
    copied = tmp_path / "copied.pdf"
    engine.apply(
        source,
        [{"op": "insert_pages", "after": 0, "source": str(source), "source_pages": selected}],
        output=copied,
    )
    copied_links = _goto_targets(copied, list(range(1, len(selected) + 1)))
    uri_links = [link for link in copied_links if link["kind"] == pymupdf.LINK_URI]
    goto_links = [link for link in copied_links if link["kind"] == pymupdf.LINK_GOTO]
    assert [link["uri"] for link in uri_links] == [
        "https://example.invalid/synthetic"
    ] * selected.count(1)
    source_targets = {1: 2, 2: 4, 3: 1, 4: 4}
    expected_targets = [
        selected.index(source_targets[source_page])
        for source_page in selected
        if source_targets[source_page] in selected
    ]
    assert [link["page"] for link in goto_links] == expected_targets

    excerpt = tmp_path / "excerpt.pdf"
    engine.apply(source, [{"op": "extract_pages", "pages": selected, "to": str(excerpt)}])
    excerpt_links = _goto_targets(excerpt, list(range(1, len(selected) + 1)))
    assert [link["uri"] for link in excerpt_links if link["kind"] == pymupdf.LINK_URI] == [
        "https://example.invalid/synthetic"
    ] * selected.count(1)
    assert [
        link["page"] for link in excerpt_links if link["kind"] == pymupdf.LINK_GOTO
    ] == expected_targets
