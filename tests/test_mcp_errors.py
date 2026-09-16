"""MCP protocol errors remain actionable for expected document failures."""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from pathlib import Path

import pymupdf

MCP = Path(sys.executable).with_name("omepreview-mcp")


def _make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=240, height=160)
    page.insert_text((20, 35), "TARGET SECRET")
    widget = pymupdf.Widget()
    widget.field_name = "known_field"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    widget.rect = pymupdf.Rect(20, 90, 150, 108)
    page.add_widget(widget)
    doc.save(path)
    doc.close()
    return path


def _is_error(result) -> bool:
    value = getattr(result, "is_error", None)
    if value is not None:
        return value
    return bool(getattr(result, "isError", False))


def _texts(result) -> list[str]:
    content = getattr(result, "content", result if isinstance(result, list) else [])
    return [block.text for block in content if getattr(block, "type", None) == "text"]


def _error_text(result) -> str:
    assert _is_error(result)
    texts = _texts(result)
    assert texts, result
    return "\n".join(texts)


def _image_bytes(result) -> bytes:
    content = getattr(result, "content", result if isinstance(result, list) else [])
    images = [block for block in content if getattr(block, "type", None) == "image"]
    assert len(images) == 1
    mime = getattr(images[0], "mime_type", None) or getattr(images[0], "mimeType", None)
    assert mime == "image/png"
    png = base64.b64decode(images[0].data)
    assert pymupdf.Pixmap(png).width > 0
    return png


def test_mcp_expected_errors_are_actionable_and_preserve_files(tmp_path):
    source = _make_pdf(tmp_path / "source.pdf")
    before = source.read_bytes()
    sentinel = tmp_path / "sentinel.pdf"
    sentinel.write_bytes(b"sentinel")
    missing = tmp_path / "missing.pdf"

    async def check_protocol() -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=str(MCP), args=[], env=dict(os.environ))
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                positive = await session.call_tool(
                    "render_page", {"path": str(source), "page": 1, "clip": [0, 0, 120, 80]}
                )
                assert not _is_error(positive)
                _image_bytes(positive)

                missing_result = await session.call_tool(
                    "read_pdf", {"path": str(missing)}
                )
                assert "no such PDF" in _error_text(missing_result)

                range_result = await session.call_tool(
                    "render_page", {"path": str(source), "page": 2}
                )
                assert "page 2 out of range" in _error_text(range_result)

                clip_result = await session.call_tool(
                    "render_page", {"path": str(source), "page": 1, "clip": [0, 0, 0, 20]}
                )
                assert "clip must have positive width and height" in _error_text(clip_result)

                field_result = await session.call_tool(
                    "fill_field",
                    {
                        "path": str(source),
                        "field": "missing_field",
                        "value": "x",
                        "output": str(sentinel),
                    },
                )
                field_error = _error_text(field_result)
                assert "no form field named 'missing_field'" in field_error
                assert "known_field" in field_error

                dry_run_result = await session.call_tool(
                    "apply_ops",
                    {
                        "path": str(source),
                        "ops": [{"op": "highlight", "page": 1, "match": "NOT PRESENT"}],
                        "output": str(sentinel),
                        "dry_run": True,
                    },
                )
                assert "text 'NOT PRESENT' not found on page 1" in _error_text(dry_run_result)

        injected_server = (
            "from omepreview import mcp_server\n"
            "@mcp_server._tool()\n"
            "def injected_unexpected():\n"
            "    raise RuntimeError('secret-internal')\n"
            "mcp_server.mcp.run()\n"
        )
        injected_params = StdioServerParameters(
            command=sys.executable,
            args=["-c", injected_server],
            env=dict(os.environ),
        )
        async with stdio_client(injected_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                unexpected = await session.call_tool("injected_unexpected", {})
                message = _error_text(unexpected)
                assert "Error executing tool injected_unexpected" in message
                assert "secret-internal" not in message

    asyncio.run(check_protocol())
    assert source.read_bytes() == before
    assert sentinel.read_bytes() == b"sentinel"
