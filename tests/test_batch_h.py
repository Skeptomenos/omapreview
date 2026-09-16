"""Batch H: real CLI/MCP discovery, rendering, safety and observation paths."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from omepreview import signature
from omepreview.ops import OP_TYPES, operation_catalog, validate
from omepreview.render import render_page_bytes

CLI = Path(sys.executable).with_name("omepreview")
MCP = Path(sys.executable).with_name("omepreview-mcp")
SIGNATURE_NAME = "batch-h-synthetic"


def _make_pdf(path: Path, *, pages: int = 3, annotation: bool = True) -> Path:
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=240, height=160)
        page.insert_text((20, 35), f"TARGET SECRET PAGE {number}", fontsize=12)
        page.insert_text((20, 65), f"CONTROL {number}", fontsize=10)
        if number == 1 and annotation:
            annot = page.add_highlight_annot(page.search_for("TARGET")[0])
            annot.set_info(content="existing review")
            annot.update()
        if number == 1:
            widget = pymupdf.Widget()
            widget.field_name = "answer"
            widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.rect = pymupdf.Rect(20, 90, 150, 108)
            page.add_widget(widget)
    doc.save(str(path))
    doc.close()
    return path


def _make_signature(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    signature_dir = config / "omepreview" / "signatures"
    signature_dir.mkdir(parents=True)
    svg = tmp_path / "default.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" '
        'viewBox="0 0 120 40"><path d="M5 30 C20 5 40 5 60 20 S100 35 115 10" '
        'fill="none" stroke="#111133" stroke-width="3"/></svg>',
        encoding="utf-8",
    )
    (signature_dir / f"{SIGNATURE_NAME}.svg").write_text(svg.read_text(encoding="utf-8"), encoding="utf-8")


def _json_command(args: list[str], *, env: dict[str, str] | None = None) -> dict:
    run = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
    assert run.returncode == 0, f"{args}\nstdout={run.stdout}\nstderr={run.stderr}"
    return json.loads(run.stdout)


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_dir(tmp_path: Path, name: str) -> Path:
    root = os.environ.get("OMEPREVIEW_BATCH_H_EVIDENCE")
    return (Path(root) if root else tmp_path / "batch-h-evidence") / name


def _result_value(result, name: str):
    value = getattr(result, name, None)
    if value is not None:
        return value
    alias = {"structured_content": "structuredContent", "is_error": "isError"}.get(name)
    return getattr(result, alias, None) if alias else None


def _result_text(result) -> list[str]:
    return [block.text for block in result.content if getattr(block, "type", None) == "text"]


def _result_data(result) -> dict:
    value = _result_value(result, "structured_content")
    if value is not None:
        return value
    texts = _result_text(result)
    assert texts, result
    return json.loads(texts[0])


def _result_image(result) -> bytes:
    images = [block for block in result.content if getattr(block, "type", None) == "image"]
    assert len(images) == 1
    mime_type = getattr(images[0], "mime_type", None) or getattr(images[0], "mimeType", None)
    assert mime_type == "image/png"
    return base64.b64decode(images[0].data)


def _changed_pixels(before_png: bytes, after_png: bytes, rect: list[float], *, scale: float = 2.0) -> int:
    before = pymupdf.Pixmap(before_png)
    after = pymupdf.Pixmap(after_png)
    assert (before.width, before.height, before.n) == (after.width, after.height, after.n)
    x0, y0, x1, y1 = rect
    left = max(0, int(x0 * scale))
    top = max(0, int(y0 * scale))
    right = min(before.width, int(x1 * scale + 0.999))
    bottom = min(before.height, int(y1 * scale + 0.999))
    return sum(
        before.pixel(x, y) != after.pixel(x, y)
        for y in range(top, bottom)
        for x in range(left, right)
    )


def _assert_rendered_region(
    before_png: bytes,
    after_png: bytes,
    target_rect: list[float],
    protected_rect: list[float],
) -> None:
    target_changes = _changed_pixels(before_png, after_png, target_rect)
    protected_changes = _changed_pixels(before_png, after_png, protected_rect)
    assert target_changes >= 10, (
        f"expected saved pixels in target {target_rect}, changed={target_changes}"
    )
    assert protected_changes <= 2, (
        f"protected region {protected_rect} changed unexpectedly: {protected_changes}"
    )


def _assert_shape_geometry(read_result: dict, expected_rect: list[float]) -> None:
    x0, y0, x1, y1 = expected_rect
    rectangles = [
        annotation["rect"]
        for annotation in read_result["pages"][0]["annotations"]
        if annotation["type"] == "Square"
    ]
    assert rectangles
    assert any(
        rect[0] <= x0 and rect[1] <= y0 and rect[2] >= x1 and rect[3] >= y1
        for rect in rectangles
    ), f"expected observed Square geometry around {expected_rect}, got {rectangles}"


def _assert_rendered_image(png: bytes) -> None:
    pix = pymupdf.Pixmap(png)
    red, green, blue = pix.pixel(pix.width // 2, pix.height // 2)[:3]
    assert max(abs(channel - 180) for channel in (red, green, blue)) <= 3


def _assert_annotation(read_result: dict, *, kind: str, content: str | None = None) -> None:
    annots = read_result["pages"][0]["annotations"]
    assert any(
        annot["type"] == kind and (content is None or annot["content"] == content)
        for annot in annots
    ), f"expected {kind} annotation in {annots!r}"


def _assert_redacted(read_result: dict, page_number: int = 1) -> None:
    page = read_result["pages"][page_number - 1]
    text = " ".join(
        block["text"]
        for block in page.get("text_blocks", [])
    )
    assert "SECRET" not in text, f"redaction postcondition failed: {text!r}"


def _operation_cases(tmp_path: Path) -> list[tuple[str, dict, Path]]:
    source_insert = _make_pdf(tmp_path / "insert-source.pdf", pages=2, annotation=False)
    image = tmp_path / "insert-image.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30))
    pix.clear_with(180)
    pix.save(str(image))

    return [
        ("highlight", {"op": "highlight", "page": 1, "match": "TARGET"}, None),
        ("note", {"op": "note", "page": 1, "at": [180, 120], "text": "review"}, None),
        ("text_box", {"op": "text_box", "page": 1, "rect": [160, 120, 225, 140], "text": "OK"}, None),
        ("fill_field", {"op": "fill_field", "field": "answer", "value": "filled"}, None),
        (
            "place_signature",
            {
                "op": "place_signature",
                "page": 1,
                "at": [160, 90],
                "width": 60,
                "signature": SIGNATURE_NAME,
            },
            None,
        ),
        (
            "ink",
            {"op": "ink", "page": 1, "strokes": [[[160, 40], [175, 55], [190, 40]]]},
            None,
        ),
        ("rotate_pages", {"op": "rotate_pages", "pages": [1], "degrees": 90}, None),
        ("delete_pages", {"op": "delete_pages", "pages": [3]}, None),
        ("move_pages", {"op": "move_pages", "pages": [2], "after": 0}, None),
        (
            "insert_pages",
            {"op": "insert_pages", "after": 1, "source": str(source_insert), "source_pages": [1]},
            None,
        ),
        (
            "extract_pages",
            {"op": "extract_pages", "pages": [2], "to": str(tmp_path / "excerpt.pdf")},
            tmp_path / "excerpt.pdf",
        ),
        ("redact", {"op": "redact", "page": 1, "match": "SECRET"}, None),
        ("delete_annotation", {"op": "delete_annotation", "page": 1, "index": 0}, None),
        (
            "shape",
            {"op": "shape", "page": 1, "shape": "rect", "rect": [160, 40, 220, 75]},
            None,
        ),
        (
            "crop_pages",
            {"op": "crop_pages", "pages": [1], "rect": [10, 10, 230, 150]},
            None,
        ),
    ]


def test_operation_catalog_covers_authoritative_types_and_bounds():
    catalog = operation_catalog()
    assert {entry["name"] for entry in catalog["operations"]} == set(OP_TYPES)
    assert catalog["version"] == 1
    by_name = {entry["name"]: entry for entry in catalog["operations"]}
    assert "after" in by_name["move_pages"]["required"]
    assert "after" not in by_name["move_pages"].get("defaults", {})
    assert "count" not in by_name["insert_pages"]["fields"]["blank"].get("required", [])
    assert by_name["text_box"]["fields"]["size"]["maximum"] == 1000.0
    assert by_name["text_box"]["fields"]["size"]["exclusiveMinimum"] == 0.1
    assert by_name["shape"]["fields"]["width"]["maximum"] == 1000.0
    assert by_name["place_signature"]["fields"]["width"]["maximum"] == 10000.0
    assert by_name["crop_pages"]["fields"]["rect"]["constraints"] == {
        "width_at_least": 1,
        "height_at_least": 1,
    }
    expected_fields = {
        "highlight": {"op", "page", "match", "rect", "style"},
        "note": {"op", "page", "at", "text"},
        "text_box": {"op", "page", "rect", "text", "size"},
        "fill_field": {"op", "field", "value"},
        "place_signature": {"op", "page", "at", "width", "signature", "date"},
        "ink": {"op", "page", "strokes", "color", "width"},
        "rotate_pages": {"op", "pages", "degrees"},
        "delete_pages": {"op", "pages"},
        "move_pages": {"op", "pages", "after"},
        "insert_pages": {"op", "after", "source", "source_pages", "blank", "image"},
        "extract_pages": {"op", "pages", "to"},
        "redact": {"op", "page", "match", "rect", "fill", "apply_now"},
        "delete_annotation": {"op", "page", "index"},
        "shape": {"op", "page", "shape", "from", "to", "rect", "color", "width"},
        "crop_pages": {"op", "pages", "rect"},
    }

    for entry in catalog["operations"]:
        fields = entry["fields"]
        assert set(fields) == expected_fields[entry["name"]]
        assert set(entry["required"]) <= set(fields)
        for example in entry["examples"]:
            assert example["op"] == entry["name"]
            validate(example)
        for variant in entry.get("variants", []):
            required = set(variant["required"])
            candidates = [example for example in entry["examples"] if required <= set(example)]
            if "shape" in variant:
                candidates = [
                    example for example in candidates
                    if example.get("shape") in variant["shape"]
                ]
            assert candidates, f"no catalog example covers {entry['name']} variant {variant}"
        for dotted_path, expected in entry.get("defaults", {}).items():
            path = dotted_path.split(".")
            example = next(
                (example for example in entry["examples"] if path[0] in example),
                entry["examples"][0],
            )
            payload = json.loads(json.dumps(example))
            if path[0] in payload:
                cursor = payload
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor.pop(path[-1], None)
            normalized = validate(payload)
            cursor = normalized
            for key in path:
                if key not in cursor:
                    # The engine reads this default with op.get() instead of
                    # materializing it in the normalized operation.
                    assert (entry["name"], dotted_path, expected) == (
                        "redact", "apply_now", True
                    )
                    break
                cursor = cursor[key]
            else:
                assert cursor == expected
    assert set(by_name["insert_pages"]["fields"]["blank"]["properties"]) == {
        "count", "width", "height"
    }


def test_render_page_has_source_bound_metadata_and_cli_pixel_parity(tmp_path):
    source = _make_pdf(tmp_path / "render.pdf", pages=1, annotation=False)
    doc = pymupdf.open(str(source))
    page = doc[0]
    page.set_cropbox(pymupdf.Rect(10, 15, 230, 145))
    page.set_rotation(90)
    doc.save(str(tmp_path / "render-cropped.pdf"))
    doc.close()
    source = tmp_path / "render-cropped.pdf"
    before = source.read_bytes()

    png, metadata = render_page_bytes(
        source, page=1, scale=1.25, grid=25.5, clip=[10, 5, 100, 40]
    )
    assert source.read_bytes() == before
    assert metadata["source_fingerprint"]["value"] == hashlib.sha256(before).hexdigest()
    assert metadata["pdf_geometry"]["coordinate_space"].startswith("unrotated CropBox")
    assert metadata["pdf_geometry"]["rotation"] == 90
    assert metadata["requested_clip"] == [10, 5, 100, 40]
    assert metadata["grid_labels"]["vertical"] == ["25.5", "51", "76.5"]
    assert metadata["grid_labels"]["horizontal"] == ["25.5"]
    pix = pymupdf.Pixmap(png)
    assert [pix.width, pix.height] == metadata["pixel_dimensions"]
    assert metadata["coordinate_transform"]["to"] == "top-left PNG pixels"

    cli_png = tmp_path / "cli.png"
    report = _json_command(
        [
            str(CLI),
            "snapshot",
            str(source),
            "--page",
            "1",
            "--scale",
            "1.25",
            "--grid",
            "25.5",
            "--clip",
            "10,5,100,40",
            "-o",
            str(cli_png),
        ]
    )
    assert cli_png.read_bytes() == png
    assert report["clip"] == [10.0, 5.0, 100.0, 40.0]
    assert report["pdf_geometry"]["size"] == [220.0, 130.0]


def test_mcp_only_journey_is_discoverable_and_observable(tmp_path, monkeypatch):
    _make_signature(tmp_path, monkeypatch)
    source = _make_pdf(tmp_path / "mcp-source.pdf", pages=1, annotation=False)
    output = tmp_path / "mcp-saved.pdf"

    async def journey() -> dict:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=str(MCP), args=[], env=dict(os.environ)
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"render_page", "operation_schema", "read_pdf", "apply_ops"} <= names
                catalog_result = await session.call_tool("operation_schema", {})
                catalog = _result_data(catalog_result)
                assert {entry["name"] for entry in catalog["operations"]} == set(OP_TYPES)

                source_read_result = await session.call_tool(
                    "read_pdf", {"path": str(source)}
                )
                source_read = _result_data(source_read_result)
                render_result = await session.call_tool(
                    "render_page", {"path": str(source), "page": 1, "scale": 1.0}
                )
                assert not _result_value(render_result, "is_error")
                image = _result_image(render_result)
                (tmp_path / "mcp-source-render.png").write_bytes(image)
                render_metadata = json.loads(_result_text(render_result)[0])
                assert render_metadata["source_fingerprint"] == source_read["source_fingerprint"]
                assert [pymupdf.Pixmap(image).width, pymupdf.Pixmap(image).height] == render_metadata[
                    "pixel_dimensions"
                ]

                proposal_result = await session.call_tool(
                    "apply_ops",
                    {
                        "path": str(source),
                        "ops": [{"op": "note", "page": 1, "at": [180, 120], "text": "MCP note"}],
                        "output": str(output),
                    },
                )
                proposal = _result_data(proposal_result)
                assert proposal["output"] is None
                assert proposal["applied"][0]["applied"] is False
                assert "needs_confirmation" in proposal
                with pytest.raises(AssertionError):
                    _assert_annotation(source_read, kind="Text", content="MCP note")

                applied_result = await session.call_tool(
                    "apply_ops",
                    {
                        "path": str(source),
                        "ops": [{"op": "note", "page": 1, "at": [180, 120], "text": "MCP note"}],
                        "output": str(output),
                        "dry_run": False,
                    },
                )
                applied = _result_data(applied_result)
                assert applied["output"] == str(output)
                saved_read_result = await session.call_tool(
                    "read_pdf", {"path": str(output)}
                )
                saved_read = _result_data(saved_read_result)
                _assert_annotation(saved_read, kind="Text", content="MCP note")
                saved_render_result = await session.call_tool(
                    "render_page", {"path": str(output), "page": 1, "scale": 1.0}
                )
                (tmp_path / "mcp-saved-render.png").write_bytes(_result_image(saved_render_result))
                saved_metadata = json.loads(_result_text(saved_render_result)[0])
                assert saved_metadata["source_fingerprint"] == saved_read["source_fingerprint"]
                assert saved_metadata["source_fingerprint"] != source_read["source_fingerprint"]

                # A deliberately wrong observation is rejected by the same
                # assertion a task recipe uses before reporting completion.
                with pytest.raises(AssertionError):
                    _assert_annotation(saved_read, kind="Text", content="wrong note")
                return {
                    "initialized_protocol": getattr(initialized, "protocol_version", None)
                    or getattr(initialized, "protocolVersion", None),
                    "tool_count": len(names),
                    "source_read": source_read,
                    "render_metadata": render_metadata,
                    "proposal": proposal,
                    "applied": applied,
                    "saved_read": saved_read,
                    "saved_render_metadata": saved_metadata,
                }

    evidence = asyncio.run(journey())
    run_dir = _evidence_dir(tmp_path, "test-mcp-journey")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "journey.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    (run_dir / "source-render.png").write_bytes((tmp_path / "mcp-source-render.png").read_bytes())
    (run_dir / "saved-render.png").write_bytes((tmp_path / "mcp-saved-render.png").read_bytes())


def test_cli_journey_and_consequential_confirmation(tmp_path, monkeypatch):
    _make_signature(tmp_path, monkeypatch)
    source = _make_pdf(tmp_path / "cli-source.pdf", pages=1, annotation=False)
    discovered = _json_command([str(CLI), "operations"])
    assert {entry["name"] for entry in discovered["operations"]} == set(OP_TYPES)
    ops_file = tmp_path / "note.json"
    ops_file.write_text(
        json.dumps([{"op": "note", "page": 1, "at": [180, 120], "text": "CLI note"}]),
        encoding="utf-8",
    )
    output = tmp_path / "cli-saved.pdf"
    proposal = _json_command([str(CLI), "apply", str(source), "--ops", str(ops_file), "--dry-run", "--json"])
    assert proposal["output"] is None
    assert proposal["applied"][0]["applied"] is False
    applied = _json_command(
        [str(CLI), "apply", str(source), "--ops", str(ops_file), "--confirm", "-o", str(output), "--json"]
    )
    saved = _json_command([str(CLI), "read", str(output), "--json"])
    _assert_annotation(saved, kind="Text", content="CLI note")
    png = tmp_path / "cli-saved.png"
    snapshot = _json_command([str(CLI), "snapshot", str(output), "--page", "1", "-o", str(png)])
    assert snapshot["source_fingerprint"]["value"] == saved["source_fingerprint"]["value"]
    assert applied["output"] == str(output)

    redact_ops = tmp_path / "redact.json"
    redact_ops.write_text(
        json.dumps([{"op": "redact", "page": 1, "match": "SECRET"}]), encoding="utf-8"
    )
    redacted = tmp_path / "redacted.pdf"
    dry_redact = _json_command(
        [str(CLI), "apply", str(source), "--ops", str(redact_ops), "-o", str(redacted), "--json"]
    )
    assert dry_redact["output"] is None
    assert not redacted.exists()
    _json_command(
        [str(CLI), "apply", str(source), "--ops", str(redact_ops), "--confirm", "-o", str(redacted), "--json"]
    )
    redacted_read = _json_command([str(CLI), "read", str(redacted), "--json"])
    with pytest.raises(AssertionError):
        _assert_redacted(saved)
    _assert_redacted(redacted_read)

    malformed = subprocess.run(
        [str(CLI), "redact", str(source), "--page", "1", "--match", "SECRET", "--confirm=false"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert malformed.returncode != 0
    assert "ignored explicit argument" in malformed.stderr

    sentinel = tmp_path / "sentinel.pdf"
    sentinel.write_bytes(b"sentinel output")
    for index, payload in enumerate(([None], [42], {"op": "note"})):
        bad_ops = tmp_path / f"bad-{index}.json"
        bad_ops.write_text(json.dumps(payload), encoding="utf-8")
        bad_run = subprocess.run(
            [str(CLI), "apply", str(source), "--ops", str(bad_ops), "--confirm", "-o", str(sentinel), "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert bad_run.returncode != 0
        assert "omepreview:" in bad_run.stderr
        assert sentinel.read_bytes() == b"sentinel output"

    run_dir = _evidence_dir(tmp_path, "test-cli-journey")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "journey.json").write_text(
        json.dumps(
            {
                "source_sha256": _fingerprint(source),
                "output_sha256": _fingerprint(output),
                "proposal": proposal,
                "applied": applied,
                "saved": saved,
                "snapshot": snapshot,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "saved-render.png").write_bytes(png.read_bytes())


@pytest.mark.parametrize("operation", OP_TYPES)
def test_generic_cli_operation_has_saved_observation(tmp_path, monkeypatch, operation):
    _make_signature(tmp_path, monkeypatch)
    cases = {name: (op, target) for name, op, target in _operation_cases(tmp_path)}
    op, special_target = cases[operation]
    source = _make_pdf(
        tmp_path / f"{operation}-source.pdf", annotation=operation == "delete_annotation"
    )
    output = tmp_path / f"{operation}-output.pdf"
    ops_file = tmp_path / f"{operation}.json"
    ops_file.write_text(json.dumps([op]), encoding="utf-8")
    command = [str(CLI), "apply", str(source), "--ops", str(ops_file), "--confirm", "--json"]
    if special_target is None:
        command += ["-o", str(output)]
    report = _json_command(command)
    target = special_target or output
    observed = _json_command([str(CLI), "read", str(target), "--json"])
    before_png = tmp_path / f"{operation}-before.png"
    after_png = tmp_path / f"{operation}-after.png"
    _json_command([str(CLI), "snapshot", str(source), "--page", "1", "-o", str(before_png)])
    _json_command([str(CLI), "snapshot", str(target), "--page", "1", "-o", str(after_png)])
    assert report["applied"][0]["applied"] is True
    if operation == "highlight":
        _assert_annotation(observed, kind="Highlight")
    elif operation == "note":
        _assert_annotation(observed, kind="Text", content="review")
    elif operation == "text_box":
        _assert_annotation(observed, kind="FreeText", content="OK")
    elif operation == "fill_field":
        assert observed["pages"][0]["form_fields"][0]["value"] == "filled"
    elif operation == "place_signature":
        assert report["applied"][0]["rect"] == [160.0, 90.0, 220.0, 110.0]
        _assert_rendered_region(
            before_png.read_bytes(),
            after_png.read_bytes(),
            [157, 87, 223, 113],
            [20, 56, 120, 74],
        )
        wrong_op = dict(op, at=[20, 110])
        wrong_ops_file = tmp_path / "place_signature-wrong.json"
        wrong_ops_file.write_text(json.dumps([wrong_op]), encoding="utf-8")
        wrong_output = tmp_path / "place_signature-wrong.pdf"
        _json_command(
            [
                str(CLI), "apply", str(source), "--ops", str(wrong_ops_file),
                "--confirm", "-o", str(wrong_output), "--json",
            ]
        )
        wrong_png = tmp_path / "place_signature-wrong.png"
        _json_command([str(CLI), "snapshot", str(wrong_output), "--page", "1", "-o", str(wrong_png)])
        with pytest.raises(AssertionError):
            _assert_rendered_region(
                before_png.read_bytes(),
                wrong_png.read_bytes(),
                [157, 87, 223, 113],
                [20, 56, 120, 74],
            )
        with pytest.raises(AssertionError):
            _assert_rendered_region(
                before_png.read_bytes(),
                before_png.read_bytes(),
                [157, 87, 223, 113],
                [20, 56, 120, 74],
            )
    elif operation == "ink":
        _assert_annotation(observed, kind="Ink")
        _assert_rendered_region(
            before_png.read_bytes(),
            after_png.read_bytes(),
            [155, 35, 195, 60],
            [20, 60, 120, 74],
        )
    elif operation == "rotate_pages":
        assert observed["pages"][0]["size"] == [160.0, 240.0]
    elif operation == "delete_pages":
        assert observed["page_count"] == 2
        assert observed["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 2")
    elif operation == "move_pages":
        assert observed["pages"][0]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 2")
        assert observed["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 1")
    elif operation == "insert_pages":
        assert observed["page_count"] == 4
        assert observed["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 1")
        assert observed["pages"][2]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 2")
    elif operation == "extract_pages":
        assert observed["page_count"] == 1
        assert observed["pages"][0]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 2")
    elif operation == "redact":
        _assert_redacted(observed)
        assert "CONTROL 1" in " ".join(block["text"] for block in observed["pages"][0]["text_blocks"])
    elif operation == "delete_annotation":
        assert observed["pages"][0]["annotations"] == []
        assert "TARGET SECRET PAGE 1" in observed["pages"][0]["text_blocks"][0]["text"]
    elif operation == "shape":
        _assert_annotation(observed, kind="Square")
        _assert_shape_geometry(observed, [160, 40, 220, 75])
    elif operation == "crop_pages":
        assert observed["pages"][0]["size"] == [220.0, 140.0]


def test_cli_insert_blank_and_image_variants_have_saved_observations(tmp_path):
    source = _make_pdf(tmp_path / "insert-source.pdf")
    image = tmp_path / "insert.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30))
    pix.clear_with(180)
    pix.save(str(image))

    blank_ops = tmp_path / "blank.json"
    blank_ops.write_text(
        json.dumps([{"op": "insert_pages", "after": 0, "blank": {"count": 1, "width": 50, "height": 60}}]),
        encoding="utf-8",
    )
    blank_output = tmp_path / "blank-output.pdf"
    _json_command([str(CLI), "apply", str(source), "--ops", str(blank_ops), "--confirm", "-o", str(blank_output), "--json"])
    blank_read = _json_command([str(CLI), "read", str(blank_output), "--json"])
    assert blank_read["pages"][0]["size"] == [50.0, 60.0]
    assert blank_read["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 1")

    image_ops = tmp_path / "image.json"
    image_ops.write_text(
        json.dumps([{"op": "insert_pages", "after": 0, "image": str(image)}]),
        encoding="utf-8",
    )
    image_output = tmp_path / "image-output.pdf"
    _json_command([str(CLI), "apply", str(source), "--ops", str(image_ops), "--confirm", "-o", str(image_output), "--json"])
    image_read = _json_command([str(CLI), "read", str(image_output), "--json"])
    assert image_read["pages"][0]["size"] == [40.0, 30.0]
    assert image_read["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 1")
    image_png = tmp_path / "image-output.png"
    _json_command([str(CLI), "snapshot", str(image_output), "--page", "1", "-o", str(image_png)])
    _assert_rendered_image(image_png.read_bytes())


def test_mcp_generic_operations_have_saved_observation(tmp_path, monkeypatch):
    _make_signature(tmp_path, monkeypatch)
    cases = {name: (op, target) for name, op, target in _operation_cases(tmp_path)}
    source_paths = {
        name: _make_pdf(
            tmp_path / f"mcp-{name}-source.pdf", annotation=name == "delete_annotation"
        )
        for name in cases
    }

    async def run_all() -> list[str]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=str(MCP), args=[], env=dict(os.environ))
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                observed_names = []
                for name, (op, special_target) in cases.items():
                    output = tmp_path / f"mcp-{name}-output.pdf"
                    source_render_result = await session.call_tool(
                        "render_page", {"path": str(source_paths[name]), "page": 1}
                    )
                    source_render = _result_image(source_render_result)
                    call_args = {"path": str(source_paths[name]), "ops": [op], "dry_run": False}
                    if special_target is None:
                        call_args["output"] = str(output)
                    result = await session.call_tool("apply_ops", call_args)
                    assert not _result_value(result, "is_error"), _result_text(result)
                    report = _result_data(result)
                    target = special_target or output
                    read_result = await session.call_tool("read_pdf", {"path": str(target)})
                    observed = _result_data(read_result)
                    render_result = await session.call_tool("render_page", {"path": str(target)})
                    saved_render = _result_image(render_result)
                    assert len([b for b in render_result.content if getattr(b, "type", None) == "image"]) == 1
                    assert report["applied"][0]["applied"] is True
                    if name == "highlight":
                        _assert_annotation(observed, kind="Highlight")
                    elif name == "note":
                        _assert_annotation(observed, kind="Text", content="review")
                    elif name == "text_box":
                        _assert_annotation(observed, kind="FreeText", content="OK")
                    elif name == "fill_field":
                        assert observed["pages"][0]["form_fields"][0]["value"] == "filled"
                    elif name == "rotate_pages":
                        pages_result = await session.call_tool(
                            "list_pages", {"path": str(target)}
                        )
                        pages_observed = _result_data(pages_result)
                        assert pages_observed["pages"][0]["rotation"] == 90
                    elif name == "delete_pages":
                        assert observed["page_count"] == 2
                        assert observed["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 2")
                    elif name == "move_pages":
                        assert observed["pages"][0]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 2")
                        assert observed["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 1")
                    elif name == "insert_pages":
                        assert observed["page_count"] == 4
                        assert observed["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 1")
                        assert observed["pages"][2]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 2")
                    elif name == "extract_pages":
                        assert observed["page_count"] == 1
                        assert observed["pages"][0]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 2")
                    elif name == "redact":
                        _assert_redacted(observed)
                        assert "CONTROL 1" in " ".join(block["text"] for block in observed["pages"][0]["text_blocks"])
                    elif name == "delete_annotation":
                        assert observed["pages"][0]["annotations"] == []
                        assert "TARGET SECRET PAGE 1" in observed["pages"][0]["text_blocks"][0]["text"]
                    elif name == "place_signature":
                        _assert_rendered_region(
                            source_render,
                            saved_render,
                            [157, 87, 223, 113],
                            [20, 56, 120, 74],
                        )
                        wrong_output = tmp_path / "mcp-place-signature-wrong.pdf"
                        wrong_result = await session.call_tool(
                            "apply_ops",
                            {
                                "path": str(source_paths[name]),
                                "ops": [dict(op, at=[20, 110])],
                                "output": str(wrong_output),
                                "dry_run": False,
                            },
                        )
                        assert not _result_value(wrong_result, "is_error")
                        wrong_render = _result_image(
                            await session.call_tool(
                                "render_page", {"path": str(wrong_output), "page": 1}
                            )
                        )
                        with pytest.raises(AssertionError):
                            _assert_rendered_region(
                                source_render,
                                wrong_render,
                                [157, 87, 223, 113],
                                [20, 56, 120, 74],
                            )
                        with pytest.raises(AssertionError):
                            _assert_rendered_region(
                                source_render,
                                source_render,
                                [157, 87, 223, 113],
                                [20, 56, 120, 74],
                            )
                    elif name == "ink":
                        _assert_rendered_region(
                            source_render,
                            saved_render,
                            [155, 35, 195, 60],
                            [20, 60, 120, 74],
                        )
                    elif name == "shape":
                        _assert_shape_geometry(observed, [160, 40, 220, 75])
                    elif name == "crop_pages":
                        assert observed["pages"][0]["size"] == [220.0, 140.0]
                    observed_names.append(name)
                return observed_names

    assert asyncio.run(run_all()) == list(OP_TYPES)


def test_mcp_insert_blank_and_image_variants_have_saved_observations(tmp_path):
    source = _make_pdf(tmp_path / "mcp-insert-source.pdf")
    image = tmp_path / "mcp-insert.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30))
    pix.clear_with(180)
    pix.save(str(image))
    blank_output = tmp_path / "mcp-blank-output.pdf"
    image_output = tmp_path / "mcp-image-output.pdf"

    async def run_variants() -> tuple[dict, dict]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=str(MCP), args=[], env=dict(os.environ))
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                blank_result = await session.call_tool(
                    "apply_ops",
                    {
                        "path": str(source),
                        "ops": [{"op": "insert_pages", "after": 0, "blank": {"count": 1, "width": 50, "height": 60}}],
                        "output": str(blank_output),
                        "dry_run": False,
                    },
                )
                image_result = await session.call_tool(
                    "apply_ops",
                    {
                        "path": str(source),
                        "ops": [{"op": "insert_pages", "after": 0, "image": str(image)}],
                        "output": str(image_output),
                        "dry_run": False,
                    },
                )
                blank_read = _result_data(
                    await session.call_tool("read_pdf", {"path": str(blank_output)})
                )
                image_read = _result_data(
                    await session.call_tool("read_pdf", {"path": str(image_output)})
                )
                image_png = _result_image(
                    await session.call_tool("render_page", {"path": str(image_output)})
                )
                assert not _result_value(blank_result, "is_error")
                assert not _result_value(image_result, "is_error")
                assert blank_read["pages"][0]["size"] == [50.0, 60.0]
                assert blank_read["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 1")
                assert image_read["pages"][0]["size"] == [40.0, 30.0]
                assert image_read["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 1")
                _assert_rendered_image(image_png)
                return blank_read, image_read

    asyncio.run(run_variants())


def test_mcp_dedicated_consequential_routes_require_confirmation(tmp_path, monkeypatch):
    _make_signature(tmp_path, monkeypatch)
    redact_source = _make_pdf(tmp_path / "dedicated-redact.pdf", pages=1, annotation=False)
    delete_source = _make_pdf(tmp_path / "dedicated-delete.pdf", pages=3, annotation=False)
    annot_source = _make_pdf(tmp_path / "dedicated-annot.pdf", pages=1, annotation=True)
    sign_source = _make_pdf(tmp_path / "dedicated-sign.pdf", pages=1, annotation=False)
    flat_source = _make_pdf(tmp_path / "dedicated-flat.pdf", pages=1, annotation=True)

    async def check_routes() -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=str(MCP), args=[], env=dict(os.environ))
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                redacted = tmp_path / "dedicated-redacted.pdf"
                dry_redact = await session.call_tool(
                    "redact", {"path": str(redact_source), "page": 1, "match": "SECRET", "output": str(redacted)}
                )
                assert _result_data(dry_redact)["output"] is None
                malformed = await session.call_tool(
                    "redact", {"path": str(redact_source), "page": 1, "match": "SECRET", "confirm": "false"}
                )
                assert _result_value(malformed, "is_error")
                denied = await session.call_tool(
                    "redact", {"path": str(redact_source), "page": 1, "match": "SECRET", "output": str(redacted), "confirm": False}
                )
                assert _result_data(denied)["output"] is None
                committed = await session.call_tool(
                    "redact", {"path": str(redact_source), "page": 1, "match": "SECRET", "output": str(redacted), "confirm": True}
                )
                assert _result_data(committed)["output"] == str(redacted)
                _assert_redacted(_result_data(await session.call_tool("read_pdf", {"path": str(redacted)})))

                deleted = tmp_path / "dedicated-deleted.pdf"
                dry_delete = await session.call_tool(
                    "delete_pages", {"path": str(delete_source), "pages": [2], "output": str(deleted)}
                )
                assert _result_data(dry_delete)["output"] is None
                committed_delete = await session.call_tool(
                    "delete_pages", {"path": str(delete_source), "pages": [2], "output": str(deleted), "confirm": True}
                )
                assert _result_data(committed_delete)["output"] == str(deleted)
                delete_read = _result_data(await session.call_tool("read_pdf", {"path": str(deleted)}))
                assert delete_read["page_count"] == 2
                assert delete_read["pages"][1]["text_blocks"][0]["text"].startswith("TARGET SECRET PAGE 3")

                deleted_annot = tmp_path / "dedicated-annot-deleted.pdf"
                dry_annot = await session.call_tool(
                    "delete_annotation", {"path": str(annot_source), "page": 1, "index": 0, "output": str(deleted_annot)}
                )
                assert _result_data(dry_annot)["output"] is None
                await session.call_tool(
                    "delete_annotation", {"path": str(annot_source), "page": 1, "index": 0, "output": str(deleted_annot), "confirm": True}
                )
                assert _result_data(
                    await session.call_tool("read_pdf", {"path": str(deleted_annot)})
                )["pages"][0]["annotations"] == []

                signed = tmp_path / "dedicated-signed.pdf"
                dry_sign = await session.call_tool(
                    "place_signature",
                    {"path": str(sign_source), "page": 1, "x": 160, "y": 90, "width": 60, "signature_name": SIGNATURE_NAME, "output": str(signed)},
                )
                assert _result_data(dry_sign)["output"] is None
                committed_sign = await session.call_tool(
                    "place_signature",
                    {"path": str(sign_source), "page": 1, "x": 160, "y": 90, "width": 60, "signature_name": SIGNATURE_NAME, "output": str(signed), "confirmed": True},
                )
                assert _result_data(committed_sign)["output"] == str(signed)
                _result_image(await session.call_tool("render_page", {"path": str(signed)}))

                flattened = tmp_path / "dedicated-flat-output.pdf"
                dry_flat = await session.call_tool(
                    "flatten_pdf", {"path": str(flat_source), "output": str(flattened)}
                )
                assert _result_data(dry_flat)["output"] is None
                committed_flat = await session.call_tool(
                    "flatten_pdf", {"path": str(flat_source), "output": str(flattened), "confirm": True}
                )
                assert _result_data(committed_flat)["output"] == str(flattened)
                flat_read = _result_data(await session.call_tool("read_pdf", {"path": str(flattened)}))
                assert flat_read["pages"][0]["annotations"] == []
                assert "TARGET SECRET PAGE 1" in flat_read["pages"][0]["text_blocks"][0]["text"]

    asyncio.run(check_routes())
