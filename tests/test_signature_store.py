"""Signature store: SVG is the recorded format; PNG import still works."""

from pathlib import Path
import stat

import pymupdf
import pytest

from omepreview import signature
from omepreview.draw import render_pad_png
from omepreview.trackpad_sig import RecorderSession


@pytest.fixture(autouse=True)
def isolated_signature_environment(tmp_path, monkeypatch):
    """Keep store and legacy lookup inside this test's synthetic directory."""
    monkeypatch.setenv(
        "OMEPREVIEW_SIGNATURE_DIR",
        str(tmp_path / "Downloads" / "omapreview" / "signature"),
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "Downloads" / "omapreview" / "signature"


def test_add_and_list_svg(tmp_path, monkeypatch):
    svg = tmp_path / "jane.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="30">'
        '<path d="M 2 20 L 70 8" fill="none" stroke="#000" stroke-width="3"/>'
        "</svg>",
        encoding="utf-8",
    )
    dest = signature.add(svg, "jane")
    assert dest.suffix == ".svg"
    assert dest.parent.name == "signature"
    assert dest.parent.parent.name == "omapreview"
    assert dest.parent.parent.parent.name == "Downloads"
    assert dest == tmp_path / "Downloads" / "omapreview" / "signature" / "jane.svg"
    assert dest.is_file()
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700
    assert signature.list_names() == ["jane"]
    assert signature.get("jane") == dest


def test_png_import_still_accepted(tmp_path, monkeypatch):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 16), True)
    png = tmp_path / "old.png"
    pix.save(str(png))
    dest = signature.add(png, "legacy")
    assert dest.suffix == ".png"
    assert dest.parent.name == "signature"
    assert signature.get("legacy") == dest


def test_get_falls_back_to_legacy_config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("OMEPREVIEW_SIGNATURE_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    legacy = tmp_path / "cfg" / "omepreview" / "signatures"
    legacy.mkdir(parents=True)
    svg = legacy / "default.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="4"/>',
        encoding="utf-8",
    )
    found = signature.get("default")
    assert found == svg
    assert found.parent.name == "signatures"
    assert "default" in signature.list_names()
    assert signature.path_for("default") == svg


def test_downloads_store_wins_over_legacy(tmp_path, monkeypatch):
    monkeypatch.delenv("OMEPREVIEW_SIGNATURE_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    store = tmp_path / "Downloads" / "omapreview" / "signature"
    store.mkdir(parents=True)
    monkeypatch.setattr(signature, "store_dir", lambda *, create=True: store)
    legacy = tmp_path / "cfg" / "omepreview" / "signatures"
    legacy.mkdir(parents=True)
    (legacy / "default.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" id="old"/>',
        encoding="utf-8",
    )
    src = tmp_path / "new.svg"
    src.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" id="new"/>',
        encoding="utf-8",
    )
    dest = signature.add(src, "default")
    assert signature.get("default") == dest
    assert b'id="new"' in dest.read_bytes()
    assert dest.parent.parent.name == "omapreview"


def test_remove_deletes_legacy_copy(tmp_path, monkeypatch):
    monkeypatch.delenv("OMEPREVIEW_SIGNATURE_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    store = tmp_path / "Downloads" / "omapreview" / "signature"
    monkeypatch.setattr(signature, "store_dir", lambda *, create=True: store)
    legacy = tmp_path / "cfg" / "omepreview" / "signatures"
    legacy.mkdir(parents=True)
    (legacy / "old.svg").write_text("<svg/>", encoding="utf-8")
    signature.remove("old")
    assert not (legacy / "old.svg").is_file()
    assert signature.list_names() == []
    with pytest.raises(FileNotFoundError):
        signature.get("old")


def test_render_pad_png_is_png(tmp_path):
    session = RecorderSession(pad_size=(516.0, 336.0))
    session.handle_space()
    for x in range(20, 180, 4):
        session.add_point(x, 80 + 18 * ((x // 8) % 3 - 1), button1=False)
    path = tmp_path / "pad.png"
    render_pad_png(path, session)
    assert path.is_file()
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
