"""Persistent, truthful result records for the interactive signature recorder."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from .fs_privacy import atomic_write_private, ensure_private_dir, scratch_dir
from .ops import OpError


def _directory(job):
    if not re.fullmatch(r"[a-f0-9]{32}", job):
        raise OpError("invalid recorder job ID; use the ID returned by start")
    return scratch_dir() / "recordings" / job


def _write(directory, value):
    atomic_write_private(directory / "status.json", json.dumps(value).encode())


def start_recording(name, click):
    from .editor_session import desktop_ready
    desktop_ready()
    job = uuid.uuid4().hex
    directory = ensure_private_dir(_directory(job))
    config = {"job": job, "name": name, "click": click, "status": "starting"}
    _write(directory, config)
    log = directory / "log.txt"
    fd = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as errors:
        proc = subprocess.Popen([sys.executable, "-m", "omepreview.handoff", job],
                                stdin=subprocess.DEVNULL, stdout=errors, stderr=errors, start_new_session=True)
    return {**config, "pid": proc.pid, "saved": False, "instructions": "Human: draw in the recorder and press Enter to save; Escape cancels. Poll record_signature status."}


def recording_status(job, cancel=False):
    directory = _directory(job)
    path = directory / "status.json"
    if not path.is_file():
        raise OpError("unknown recorder job; use the ID returned by start")
    status = json.loads(path.read_text())
    terminal = {"saved", "cancelled", "failed"}
    if cancel and status["status"] not in terminal:
        atomic_write_private(directory / "cancel", b"cancel")
        return {**status, "status": "cancellation_requested", "saved": False}
    if status["status"] not in terminal and status.get("pid"):
        # Read-only liveness check, never signal a possibly reused PID.
        try:
            cmdline = Path(f'/proc/{status["pid"]}/cmdline').read_bytes().split(b"\0")
            alive = job.encode() in cmdline and b"omepreview.handoff" in cmdline
        except OSError:
            alive = False
        if not alive:
            status.update(status="failed", saved=False, error="recorder exited without a completion record; inspect the signature library before retrying")
    return status


def main(job):
    from . import draw, signature
    directory = _directory(job)
    status = json.loads((directory / "status.json").read_text())
    status.update(pid=os.getpid(), status="awaiting_human", saved=False)
    _write(directory, status)
    candidate = directory / "candidate.svg"
    try:
        result = draw._gtk_main(str(candidate), trackpad=not status["click"], cancel_file=directory / "cancel")
        if result != 0 or (directory / "cancel").exists():
            status["status"] = "cancelled"
        else:
            dest = signature.add(candidate, status["name"])
            import hashlib
            status.update(status="saved", saved=True, path=str(dest), sha256=hashlib.sha256(dest.read_bytes()).hexdigest())
    except BaseException as exc:
        status.update(status="failed", error=f"recorder failed: {exc}; check the desktop dependencies and retry")
    finally:
        candidate.unlink(missing_ok=True)
        _write(directory, status)


if __name__ == "__main__":
    main(sys.argv[1])
