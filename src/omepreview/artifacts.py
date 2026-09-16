"""Private, non-overwriting artifact publication shared by desktop and workflows."""
import io
import os
import tempfile
from pathlib import Path
from .fs_privacy import FILE_MODE

def _unused_archive(path: str | Path) -> Path:
    """Choose a free sibling archive path without replacing an existing file."""
    source = Path(path)
    candidate = source.with_suffix(".zip")
    if candidate != source and not candidate.exists() and not candidate.is_symlink():
        return candidate
    n = 2
    while True:
        candidate = source.with_name(f"{source.stem}-{n}.zip")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        n += 1


def _publish_private_new(path: str | Path, data: bytes) -> None:
    """Create private bytes without replacing any existing directory entry."""
    destination = Path(path)
    fd, staged_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    staged = Path(staged_name)
    try:
        os.fchmod(fd, FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(bytes(data))
            handle.flush()
            os.fsync(handle.fileno())
        # link(2) publishes the complete staged inode without replacing any
        # existing directory entry.  EEXIST is retried by zip_file_private.
        os.link(staged, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if fd is not None:
            os.close(fd)
        staged.unlink(missing_ok=True)


def zip_file_private(path: str | Path) -> Path:
    """Create a private, collision-safe ZIP containing one file."""
    import zipfile

    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"no such file to zip: {source}")
    destination = _unused_archive(source)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(source, source.name)
    data = archive.getvalue()
    while True:
        try:
            _publish_private_new(destination, data)
            return destination
        except FileExistsError:
            # Another exporter claimed the candidate after the free-name scan.
            destination = _unused_archive(source)


publish_new = _publish_private_new
