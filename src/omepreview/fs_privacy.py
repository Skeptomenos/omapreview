"""Restrictive modes for saved PDFs, scratch copies, and signature files.

PyMuPDF ``Document.save(path)`` and ordinary ``Path.write_bytes`` follow the
process umask (typically creating ``0644``). Confidential documents must not
become group/other-readable after a save or preview rebuild.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

FILE_MODE = 0o600
DIR_MODE = 0o700


def chmod_private_file(path: str | Path) -> None:
    os.chmod(path, FILE_MODE)


def atomic_write_private(path: str | Path, data: bytes) -> None:
    """Replace *path* with private ``data`` after a complete staged write.

    The destination is not opened for writing.  A short-lived sibling is
    written and fsynced first, then one rename publishes it.  This keeps a
    failed write from truncating the existing document and also gives the
    replacement the application's private file mode.
    """
    destination = Path(path)
    data = bytes(data)
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"no such directory: {destination.parent}")
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), FILE_MODE)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        staged.unlink(missing_ok=True)


def ensure_private_dir(path: str | Path) -> Path:
    """Create *path* if needed and set mode ``0700`` (existing dirs included)."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, DIR_MODE)
    return p


def scratch_dir() -> Path:
    """Application scratch directory that other local accounts cannot traverse."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        base = Path(runtime) / "omepreview"
    else:
        base = Path(tempfile.gettempdir()) / f"omepreview-{os.getuid()}"
    return ensure_private_dir(base)
