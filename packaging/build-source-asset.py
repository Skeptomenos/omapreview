#!/usr/bin/env python3
"""Build the deterministic application source asset used by the release."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile


# 2026-09-16T00:00:00Z. Keep this fixed so Git metadata cannot change bytes.
SOURCE_MTIME = 1_789_516_800
SOURCE_PATHS = (
    "pyproject.toml",
    "src",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "share",
    "skill",
    "bin",
    "shell-plugin",
    "docs",
)
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?\Z")


def build_source_asset(
    repo: Path,
    output: Path,
    *,
    version: str,
    treeish: str = "HEAD",
    mtime: int = SOURCE_MTIME,
) -> str:
    """Archive selected paths from a Git tree and return the SHA-256 digest."""

    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"invalid release version for archive prefix: {version!r}")

    tree_result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{treeish}^{{tree}}"],
        check=False,
        text=True,
        capture_output=True,
    )
    if tree_result.returncode:
        detail = tree_result.stderr.strip()
        raise RuntimeError(f"git tree resolution failed ({tree_result.returncode}): {detail}")
    tree_object = tree_result.stdout.strip()

    command = [
        "git",
        "-C",
        str(repo),
        "archive",
        "--format=tar",
        f"--prefix=omapreview-{version}/",
        f"--mtime=@{mtime}",
        tree_object,
        *SOURCE_PATHS,
    ]
    result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git archive failed ({result.returncode}): {detail}")

    compressed = gzip.compress(result.stdout, compresslevel=9, mtime=0)
    digest = hashlib.sha256(compressed).hexdigest()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(compressed)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return digest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release version in the archive root name")
    parser.add_argument("--output", required=True, type=Path, help="gzip-compressed tarball to create")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--treeish", default="HEAD", help="committed Git tree or commit to archive")
    parser.add_argument("--mtime", type=int, default=SOURCE_MTIME, help="fixed tar member mtime")
    return parser


def main() -> int:
    args = _parser().parse_args()
    digest = build_source_asset(
        args.repo.resolve(),
        args.output,
        version=args.version,
        treeish=args.treeish,
        mtime=args.mtime,
    )
    print(f"asset={args.output.resolve()}")
    print(f"sha256={digest}")
    print(f"treeish={args.treeish}")
    print(f"mtime={args.mtime}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
