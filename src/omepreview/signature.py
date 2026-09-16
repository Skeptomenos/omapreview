"""Signature store: named SVG (or imported PNG) files.

Recorded signatures are SVG. `place_signature` and the Sign tool consume SVG
(and still open a leftover PNG if one exists). New files live in
~/Downloads/omapreview/signature/ (that product spelling — omapreview, not
omepreview). The name "default" is what `place_signature` uses when no name
is given.

Reads also look in the legacy dir ~/.config/omepreview/signatures/ so an
existing default.svg still places.
"""

from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from .fs_privacy import atomic_write_private, ensure_private_dir

_SUFFIXES = (".svg", ".png")


def store_dir(*, create: bool = True) -> Path:
    """Writable store: ``~/Downloads/omapreview/signature/`` (mode 0700).

    ``OMEPREVIEW_SIGNATURE_DIR`` is a narrow portable/test override.  It lets
    isolated CLI and protocol tests use synthetic stores without changing the
    user's HOME or writing to the real Downloads directory.
    """
    override = os.environ.get("OMEPREVIEW_SIGNATURE_DIR")
    if override:
        d = Path(override).expanduser()
        if create:
            ensure_private_dir(d)
        return d

    downloads = Path.home() / "Downloads"
    d = downloads / "omapreview" / "signature"
    if create:
        downloads.mkdir(exist_ok=True)
        ensure_private_dir(d.parent)
        ensure_private_dir(d)
    return d


def legacy_store_dir() -> Path:
    """Read-only fallback: ~/.config/omepreview/signatures/."""
    override = os.environ.get("OMEPREVIEW_SIGNATURE_DIR")
    if override:
        # Test/portable overrides are complete stores.  Do not leak entries
        # from the user's Downloads or XDG config into an isolated run.
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "omepreview" / "signatures"


def _validate_name(name: str) -> None:
    if not name or not name.strip() or len(name) > 128 or any(c in name for c in "/\\\x00") or name.startswith("."):
        raise ValueError(f"invalid signature name {name!r}")


def _file_in(directory: Path, name: str) -> Path | None:
    svg = directory / f"{name}.svg"
    png = directory / f"{name}.png"
    if svg.is_file():
        return svg
    if png.is_file():
        return png
    return None


def _same_file_or_path(first: Path, second: Path) -> bool:
    """Return whether two paths resolve to the same directory entry."""
    if first.absolute().resolve(strict=False) == second.absolute().resolve(strict=False):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _decode_candidate(source: Path, data: bytes, suffix: str) -> None:
    """Decode a signature completely before any stored asset is replaced."""
    import pymupdf

    try:
        if suffix == ".svg":
            root = ET.fromstring(data)
            if root.tag.rsplit("}", 1)[-1] != "svg":
                raise ValueError("root element must be <svg>")
            doc = pymupdf.open(stream=data, filetype="svg")
            try:
                if doc.page_count < 1:
                    raise ValueError("SVG contains no drawable page")
                rect = doc[0].rect
                if not all(
                    math.isfinite(value) and value > 0
                    for value in (rect.width, rect.height)
                ):
                    raise ValueError(
                        f"SVG geometry must be finite and positive, got {rect.width}x{rect.height}"
                    )
                converted = doc.convert_to_pdf()
            finally:
                doc.close()
            rendered = pymupdf.open(stream=converted, filetype="pdf")
            try:
                if rendered.page_count < 1:
                    raise ValueError("SVG conversion produced no page")
                rendered[0].get_pixmap()
            finally:
                rendered.close()
        else:
            pix = pymupdf.Pixmap(data)
            if pix.width < 1 or pix.height < 1:
                raise ValueError("PNG has no pixels")
    except Exception as exc:
        raise ValueError(f"invalid signature {source}: {exc}") from exc


def path_for(name: str) -> Path:
    """Preferred path for `name`.

    Existing file in the Downloads store wins; otherwise a leftover in the
    legacy config dir; otherwise the SVG path where the next save will land.
    """
    _validate_name(name)
    found = _file_in(store_dir(create=False), name)
    if found is not None:
        return found
    found = _file_in(legacy_store_dir(), name)
    if found is not None:
        return found
    return store_dir(create=False) / f"{name}.svg"


def add(source: str | Path, name: str = "default") -> Path:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"no such image: {source}")
    suffix = source.suffix.lower()
    if suffix not in _SUFFIXES:
        raise ValueError(
            "signatures must be SVG (recorder default) or PNG to import"
        )
    _validate_name(name)
    data = source.read_bytes()
    _decode_candidate(source, data, suffix)
    dest_dir = store_dir(create=True)
    dest = dest_dir / f"{name}{suffix}"
    other = ".png" if suffix == ".svg" else ".svg"
    other_path = dest_dir / f"{name}{other}"
    if _same_file_or_path(source, dest):
        if source.suffix.lower() != suffix:
            raise ValueError(
                f"signature source {source} aliases the stored asset with a different format"
            )
        return dest
    atomic_write_private(dest, data)
    try:
        other_path.unlink(missing_ok=True)
    except OSError as exc:
        raise OSError(
            f"saved valid signature to {dest}, but could not remove old {other_path}: {exc}"
        ) from exc
    return dest


def get(name: str = "default") -> Path:
    p = path_for(name)
    if not p.is_file():
        known = ", ".join(list_names()) or "(none saved)"
        raise FileNotFoundError(
            f"no signature named {name!r}. Saved signatures: {known}. "
            f"Add one with: omepreview sig draw --name {name}"
        )
    return p


def list_names() -> list[str]:
    names: set[str] = set()
    for d in (store_dir(create=False), legacy_store_dir()):
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.is_file() and p.suffix.lower() in _SUFFIXES:
                names.add(p.stem)
    return sorted(names)


def remove(name: str) -> None:
    _validate_name(name)
    removed = False
    for d in (store_dir(create=False), legacy_store_dir()):
        for suffix in _SUFFIXES:
            p = d / f"{name}{suffix}"
            if p.is_file():
                p.unlink()
                removed = True
    if not removed:
        known = ", ".join(list_names()) or "(none saved)"
        raise FileNotFoundError(
            f"no signature named {name!r}. Saved signatures: {known}."
        )


def aspect_ratio(path: str | Path) -> float:
    """Height / width of a stored signature (SVG viewBox or PNG pixels)."""
    import pymupdf

    path = Path(path)
    if path.suffix.lower() == ".svg":
        doc = pymupdf.open(path)
        try:
            rect = doc[0].rect
            return rect.height / rect.width if rect.width else 0.4
        finally:
            doc.close()
    pix = pymupdf.Pixmap(str(path))
    return pix.height / pix.width if pix.width else 0.4


def rasterize(path: str | Path, *, dpi: int = 180):
    """Pixmap of an SVG or PNG signature (alpha preserved when the file has it)."""
    import pymupdf

    path = Path(path)
    if path.suffix.lower() == ".svg":
        doc = pymupdf.open(path)
        try:
            return doc[0].get_pixmap(alpha=True, dpi=dpi)
        finally:
            doc.close()
    return pymupdf.Pixmap(str(path))


def insert_on_page(page, rect, path: str | Path) -> None:
    """Stamp a stored signature onto a PDF page, preferring vector SVG."""
    import pymupdf

    path = Path(path)
    if path.suffix.lower() == ".svg":
        src = pymupdf.open(path)
        try:
            pdfbytes = src.convert_to_pdf()
        finally:
            src.close()
        overlay = pymupdf.open("pdf", pdfbytes)
        try:
            page.show_pdf_page(rect, overlay, 0, keep_proportion=True)
        finally:
            overlay.close()
        return
    page.insert_image(rect, filename=str(path), keep_proportion=True)
