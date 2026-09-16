"""Batch I: portable skill routing and live CLI/MCP contract smoke checks."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf

SKILL = Path(__file__).parents[1] / "skill"
CLI = Path(sys.executable).with_name("omapreview")
MCP = Path(sys.executable).with_name("omapreview-mcp")
SCENARIOS = {
    "read-review.md",
    "ocr.md",
    "desktop-workflows.md",
    "markup.md",
    "forms-signatures.md",
    "redaction.md",
    "pages-export.md",
}


def _make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=240, height=160)
    page.insert_text((20, 35), "TARGET SECRET")
    page.insert_text((20, 65), "CONTROL")
    doc.save(str(path))
    doc.close()
    return path


def _result_data(result) -> dict:
    value = getattr(result, "structured_content", None)
    if value is not None:
        return value
    texts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
    assert texts
    return json.loads(texts[0])


def test_skill_is_small_portable_and_progressively_disclosed(tmp_path):
    entry = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert len(entry.splitlines()) < 100
    assert "name: omepreview" in entry
    assert "/home/" not in entry
    assert "../" not in entry

    for reference in SCENARIOS | {"cli.md", "mcp.md", "verification.md"}:
        path = SKILL / "references" / reference
        assert path.is_file(), reference
        body = path.read_text(encoding="utf-8")
        assert "/home/" not in body, reference
        assert "../" not in body, reference
    forms = (SKILL / "references" / "forms-signatures.md").read_text(encoding="utf-8")
    assert "--page N" in forms and "--rect X0,Y0,X1,Y1" in forms
    assert "0.01" in forms and "when the live operation catalog offers" not in forms
    assert SCENARIOS <= {
        path.name for path in (SKILL / "references").glob("*.md")
    }

    # Copying only the skill bundle must retain every linked target.
    copied = tmp_path / "skill"
    import shutil

    shutil.copytree(SKILL, copied)
    for target in copied.rglob("*.md"):
        for link in target.read_text(encoding="utf-8").split("("):
            if ")" not in link or not link.startswith("references/"):
                continue
            assert (target.parent / link.split(")", 1)[0]).is_file()


def test_skill_cli_examples_match_live_catalog():
    help_run = subprocess.run([str(CLI), "--help"], capture_output=True, text=True, check=False)
    assert help_run.returncode == 0
    for command in ("read", "fields", "operations", "apply", "pages", "snapshot"):
        assert command in help_run.stdout

    catalog_run = subprocess.run([str(CLI), "operations"], capture_output=True, text=True, check=False)
    assert catalog_run.returncode == 0, catalog_run.stderr
    catalog = json.loads(catalog_run.stdout)
    names = {operation["name"] for operation in catalog["operations"]}
    assert {
        "highlight", "note", "text_box", "fill_field", "place_signature",
        "ink", "rotate_pages", "delete_pages", "move_pages", "insert_pages",
        "extract_pages", "redact", "delete_annotation", "shape", "crop_pages",
    } <= names


def test_skill_mcp_route_initializes_reads_and_renders(tmp_path):
    source = _make_pdf(tmp_path / "source.pdf")

    async def run() -> tuple[dict, dict, bytes, set[str]]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=str(MCP), args=[], env=dict(os.environ))
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"operation_schema", "read_pdf", "render_page", "apply_ops"} <= names
                read_result = await session.call_tool("read_pdf", {"path": str(source)})
                render_result = await session.call_tool(
                    "render_page", {"path": str(source), "page": 1, "scale": 1.0}
                )
                image_blocks = [
                    block for block in render_result.content if getattr(block, "type", None) == "image"
                ]
                assert len(image_blocks) == 1
                image = base64.b64decode(image_blocks[0].data)
                assert image.startswith(b"\x89PNG\r\n\x1a\n")
                return _result_data(read_result), _result_data(render_result), image, names

    read_data, render_data, image, names = asyncio.run(run())
    assert render_data["source_fingerprint"] == read_data["source_fingerprint"]
    assert render_data["pixel_dimensions"][0] > 0
    assert render_data["pixel_dimensions"][1] > 0
    assert len(names) >= 19
    assert image
