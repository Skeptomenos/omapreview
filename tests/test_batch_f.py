"""Batch F regressions for safe signature and image/archive publication."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pymupdf
import pytest

from omepreview import gui, render, signature

CLI = Path(sys.executable).with_name("omepreview")
MCP = Path(sys.executable).with_name("omepreview-mcp")
FILE_MODE = 0o600


def _svg(path: Path, *, marker: str = "working") -> Path:
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="30">'
        f'<path id="{marker}" d="M 2 20 L 70 8" fill="none" '
        'stroke="#000" stroke-width="3"/></svg>',
        encoding="utf-8",
    )
    return path


def _png(path: Path) -> Path:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 16), True)
    pix.clear_with(180)
    pix.save(str(path))
    return path


def _pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page(width=160, height=120).insert_text((20, 40), "SYNTHETIC")
    doc.save(str(path))
    doc.close()
    return path


def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store = tmp_path / "signature-store"
    config = tmp_path / "config"
    monkeypatch.setenv("OMEPREVIEW_SIGNATURE_DIR", str(store))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    env = dict(os.environ)
    env["OMEPREVIEW_SIGNATURE_DIR"] = str(store)
    env["XDG_CONFIG_HOME"] = str(config)
    return env


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_invalid_svg_and_png_preserve_valid_signature(tmp_path, monkeypatch):
    _isolated_env(tmp_path, monkeypatch)
    valid = _svg(tmp_path / "valid.svg")
    stored = signature.add(valid, "default")
    before = stored.read_bytes()

    malformed_svg = tmp_path / "malformed.svg"
    malformed_svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><path')
    malformed_png = tmp_path / "truncated.png"
    malformed_png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    for candidate in (malformed_svg, malformed_png):
        with pytest.raises(ValueError, match="invalid signature"):
            signature.add(candidate, "default")
        assert signature.get("default") == stored
        assert stored.read_bytes() == before
        assert signature.aspect_ratio(stored) > 0


def test_non_svg_and_zero_geometry_svg_are_rejected_before_replacement(tmp_path, monkeypatch):
    _isolated_env(tmp_path, monkeypatch)
    stored = signature.add(_svg(tmp_path / "valid.svg"), "default")
    before = stored.read_bytes()
    html = tmp_path / "html.svg"
    html.write_text("<html><body>not an SVG</body></html>", encoding="utf-8")
    zero = tmp_path / "zero.svg"
    zero.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0"/>',
        encoding="utf-8",
    )
    for candidate in (html, zero):
        with pytest.raises(ValueError, match="invalid signature"):
            signature.add(candidate, "default")
        assert stored.read_bytes() == before
        assert signature.aspect_ratio(stored) > 0


def test_signature_same_path_and_valid_cross_format_replacement(tmp_path, monkeypatch):
    _isolated_env(tmp_path, monkeypatch)
    svg = signature.add(_svg(tmp_path / "working.svg"), "working")
    assert signature.add(svg, "working") == svg

    png = signature.add(_png(tmp_path / "replacement.png"), "working")
    assert png.suffix == ".png"
    assert png.is_file()
    assert not svg.exists()
    assert signature.get("working") == png
    assert signature.aspect_ratio(png) == pytest.approx(16 / 40)


def test_signature_publication_failure_keeps_existing_asset(tmp_path, monkeypatch):
    _isolated_env(tmp_path, monkeypatch)
    stored = signature.add(_svg(tmp_path / "before.svg"), "default")
    before = stored.read_bytes()
    candidate = _svg(tmp_path / "candidate.svg", marker="candidate")

    def fail(*_args, **_kwargs):
        raise OSError("injected signature publication failure")

    monkeypatch.setattr(signature, "atomic_write_private", fail)
    with pytest.raises(OSError, match="injected signature publication failure"):
        signature.add(candidate, "default")
    assert stored.read_bytes() == before
    assert signature.aspect_ratio(stored) > 0


def test_signature_override_is_a_complete_isolated_store(tmp_path, monkeypatch):
    env = _isolated_env(tmp_path, monkeypatch)
    legacy = tmp_path / "config" / "omepreview" / "signatures"
    legacy.mkdir(parents=True)
    _svg(legacy / "leak.svg", marker="must-not-leak")
    _svg(tmp_path / "new.svg")

    assert signature.list_names() == []
    assert signature.path_for("leak") == tmp_path / "signature-store" / "leak.svg"
    assert env["HOME"] == os.environ.get("HOME", "")


def test_snapshot_is_private_and_preserves_existing_output_on_failure(tmp_path, monkeypatch):
    source = _pdf(tmp_path / "source.pdf")
    output = tmp_path / "page.png"
    result = render.snapshot(source, output=output, scale=1)
    assert result["output"] == str(output)
    assert _mode(output) == FILE_MODE

    sentinel = b"existing snapshot"
    output.write_bytes(sentinel)

    def fail(*_args, **_kwargs):
        raise OSError("injected snapshot publication failure")

    monkeypatch.setattr(render, "atomic_write_private", fail)
    with pytest.raises(OSError, match="injected snapshot publication failure"):
        render.snapshot(source, output=output, scale=1)
    assert output.read_bytes() == sentinel


def test_snapshot_rejects_exact_symlink_and_hardlink_aliases(tmp_path):
    source = _pdf(tmp_path / "source.pdf")
    before = source.read_bytes()
    for alias in (source, tmp_path / "hardlink.pdf", tmp_path / "symlink.pdf"):
        if alias != source:
            if alias.name.startswith("hardlink"):
                os.link(source, alias)
            else:
                try:
                    alias.symlink_to(source)
                except OSError:
                    continue
        with pytest.raises(ValueError, match="aliases source"):
            render.snapshot(source, output=alias, scale=1)
        assert source.read_bytes() == before


def test_zip_export_is_private_collision_safe_and_atomic(tmp_path, monkeypatch):
    source = _pdf(tmp_path / "report.pdf")
    first = source.with_suffix(".zip")
    second = source.with_name("report-2.zip")
    first.write_bytes(b"unrelated archive")
    second.write_bytes(b"another unrelated archive")

    archive = gui.zip_file_private(source)
    assert archive == tmp_path / "report-3.zip"
    assert first.read_bytes() == b"unrelated archive"
    assert second.read_bytes() == b"another unrelated archive"
    assert _mode(archive) == FILE_MODE
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == [source.name]
        assert zf.read(source.name) == source.read_bytes()

    sentinel = b"preserve this archive"
    first.write_bytes(sentinel)

    def fail(*_args, **_kwargs):
        raise OSError("injected ZIP publication failure")

    monkeypatch.setattr(gui, "_publish_private_new", fail)
    with pytest.raises(OSError, match="injected ZIP publication failure"):
        gui.zip_file_private(source)
    assert first.read_bytes() == sentinel


def test_zip_export_retries_no_clobber_race_and_dangling_symlink(tmp_path, monkeypatch):
    source = _pdf(tmp_path / "race.pdf")
    first = source.with_suffix(".zip")
    injected = b"created by another exporter"
    calls = 0

    real_publish = gui._publish_private_new

    def first_race_then_publish(path, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            Path(path).write_bytes(injected)
            raise FileExistsError(path)
        return real_publish(path, data)

    monkeypatch.setattr(gui, "_publish_private_new", first_race_then_publish)
    archive = gui.zip_file_private(source)
    assert archive == tmp_path / "race-2.zip"
    assert first.read_bytes() == injected
    assert _mode(archive) == FILE_MODE

    dangling_source = _pdf(tmp_path / "dangling.pdf")
    dangling = dangling_source.with_suffix(".zip")
    dangling.symlink_to(tmp_path / "missing-target.zip")
    archive = gui.zip_file_private(dangling_source)
    assert archive == tmp_path / "dangling-2.zip"
    assert dangling.is_symlink()


def test_zip_export_link_failure_leaves_no_partial_or_staging_file(tmp_path, monkeypatch):
    source = _pdf(tmp_path / "atomic.pdf")

    def fail_link(*_args, **_kwargs):
        raise OSError("injected final-link failure")

    monkeypatch.setattr(gui.os, "link", fail_link)
    with pytest.raises(OSError, match="injected final-link failure"):
        gui.zip_file_private(source)
    assert not (tmp_path / "atomic.zip").exists()
    assert list(tmp_path.glob(".atomic.zip.*.tmp")) == []


def test_cli_signature_import_and_snapshot_routes(tmp_path, monkeypatch):
    env = _isolated_env(tmp_path, monkeypatch)
    valid = _svg(tmp_path / "cli.svg")
    malformed = tmp_path / "cli-bad.svg"
    malformed.write_bytes(b"<svg><path")
    home_before = os.environ.get("HOME")

    added = subprocess.run(
        [str(CLI), "sig", "add", str(valid), "--name", "cli"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert added.returncode == 0, added.stderr
    stored = tmp_path / "signature-store" / "cli.svg"
    before = stored.read_bytes()
    rejected = subprocess.run(
        [str(CLI), "sig", "add", str(malformed), "--name", "cli"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 1
    assert "invalid signature" in rejected.stderr
    assert stored.read_bytes() == before

    source = _pdf(tmp_path / "cli.pdf")
    output = tmp_path / "cli.png"
    snapshot = subprocess.run(
        [str(CLI), "snapshot", str(source), "--scale", "1", "-o", str(output)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert snapshot.returncode == 0, snapshot.stderr
    assert json.loads(snapshot.stdout)["output"] == str(output)
    assert _mode(output) == FILE_MODE

    original = source.read_bytes()
    aliased = subprocess.run(
        [str(CLI), "snapshot", str(source), "--scale", "1", "-o", str(source)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert aliased.returncode == 1
    assert "aliases source" in aliased.stderr
    assert source.read_bytes() == original
    assert os.environ.get("HOME") == home_before


def _result_value(result, name):
    value = getattr(result, name, None)
    if value is not None:
        return value
    alias = {"structured_content": "structuredContent", "is_error": "isError"}.get(name)
    return getattr(result, alias, None) if alias else None


def _result_data(result):
    value = _result_value(result, "structured_content")
    if value is not None:
        if isinstance(value, dict) and set(value) == {"result"}:
            return value["result"]
        return value
    text_blocks = [block.text for block in result.content if getattr(block, "type", None) == "text"]
    assert text_blocks
    return json.loads(text_blocks[0])


def test_mcp_signature_listing_and_read_only_render_protocol(tmp_path, monkeypatch):
    env = _isolated_env(tmp_path, monkeypatch)
    signature.add(_svg(tmp_path / "mcp.svg"), "mcp")
    source = _pdf(tmp_path / "mcp.pdf")

    async def journey():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=str(MCP), args=[], env=env)
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"list_signatures", "place_signature", "render_page"} <= names
                listed = await session.call_tool("list_signatures", {})
                assert _result_data(listed) == ["mcp"]
                placement = await session.call_tool(
                    "place_signature",
                    {"path": str(source), "page": 1, "x": 20, "y": 40, "signature_name": "mcp"},
                )
                report = _result_data(placement)
                assert report["output"] is None
                assert report["applied"][0]["applied"] is False
                rendered = await session.call_tool(
                    "render_page", {"path": str(source), "page": 1, "scale": 1.0}
                )
                assert not _result_value(rendered, "is_error")
                assert any(
                    getattr(block, "type", None) == "image"
                    and (getattr(block, "mime_type", None) or getattr(block, "mimeType", None))
                    == "image/png"
                    for block in rendered.content
                )
                metadata = json.loads(
                    next(block.text for block in rendered.content if getattr(block, "type", None) == "text")
                )
                assert metadata["source_path"] == str(source)

    asyncio.run(journey())
