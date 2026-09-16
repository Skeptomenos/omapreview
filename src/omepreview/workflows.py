"""Shared, discoverable application workflows. PDF edits still use engine.apply."""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import types
import typing
import zipfile

import pymupdf

from . import engine, read, signature
from .ops import OpError

_ROUTES = {}


def route(fn):
    _ROUTES[fn.__name__] = fn
    return fn


def _schema(annotation):
    origin = typing.get_origin(annotation)
    if origin in (types.UnionType, typing.Union):
        return {"anyOf": [_schema(a) for a in typing.get_args(annotation)]}
    if origin is list:
        return {"type": "array", "items": _schema(typing.get_args(annotation)[0])}
    return {"type": {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", type(None): "null"}[annotation]}


def catalog():
    result = {}
    for name, fn in _ROUTES.items():
        params = inspect.signature(fn).parameters
        hints = typing.get_type_hints(fn)
        properties = {}
        required = []
        for key, p in params.items():
            properties[key] = _schema(hints[key])
            if p.default is inspect.Parameter.empty:
                required.append(key)
            else:
                properties[key]["default"] = p.default
        result[name] = {"description": inspect.getdoc(fn), "input_schema": {
            "type": "object", "properties": properties, "required": required,
            "additionalProperties": False,
        }}
    return {"version": 1, "workflows": result}


def _matches(value, schema):
    if "anyOf" in schema:
        return any(_matches(value, s) for s in schema["anyOf"])
    kind = schema["type"]
    if kind == "array":
        return isinstance(value, list) and all(_matches(v, schema["items"]) for v in value)
    if kind == "number":
        import math
        return type(value) in (int, float) and math.isfinite(value)
    return type(value) is {"string": str, "integer": int, "boolean": bool, "object": dict, "null": type(None)}[kind]


def run(name: str, arguments: dict | None = None):
    if name not in _ROUTES:
        raise OpError(f"unknown workflow {name!r}; use workflow-schema to list workflows")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise OpError("workflow arguments must be a JSON object")
    schema = catalog()["workflows"][name]["input_schema"]
    for key, value in arguments.items():
        if key not in schema["properties"] or not _matches(value, schema["properties"][key]):
            raise OpError(f"invalid {name} argument {key!r}; see workflow-schema")
    for key in schema["required"]:
        if key not in arguments:
            raise OpError(f"{name} requires {key!r}; see workflow-schema")
    try:
        return _ROUTES[name](**arguments)
    except OSError as exc:
        raise OpError(f"{name} could not access a file or desktop service: {exc}; check the path, permissions, and installed helper") from exc


def pdf_bytes(path: str):
    source = Path(path).expanduser().resolve(strict=True)
    blob = source.read_bytes()
    try:
        with pymupdf.open(stream=blob, filetype="pdf") as doc:
            if doc.needs_pass:
                raise OpError("password-protected PDF; unlock an authorized copy first")
            if not doc.is_pdf or not doc.page_count:
                raise OpError("expected a nonempty PDF")
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise OpError(f"cannot read PDF {source}: {exc}") from exc
    return source, blob


@route
def search(path: str, query: str, pages: list[int] | None = None, limit: int = 1000, offset: int = 0) -> dict:
    """Search embedded PDF text (no OCR). Returns 1-based pages, unrotated CropBox-local rects/quads and source fingerprint. limit 1..10000; offset >=0; next_offset permits pagination. Same search semantics as GUI/PyMuPDF, including dehyphenation and case-insensitive ASCII."""
    if not query.strip() or len(query) > 4096:
        raise OpError("query must contain 1..4096 characters of searchable text")
    if not 1 <= limit <= 10000 or offset < 0:
        raise OpError("search needs limit 1..10000 and offset >= 0")
    source, blob = pdf_bytes(path)
    hits = []
    seen = 0
    more = False
    with pymupdf.open(stream=blob, filetype="pdf") as doc:
        selected = pages if pages is not None else list(range(1, doc.page_count + 1))
        if not selected or len(set(selected)) != len(selected) or any(p < 1 or p > doc.page_count for p in selected):
            raise OpError(f"pages must be unique 1-based page numbers in 1..{doc.page_count}")
        for p in selected:
            for quad in doc[p - 1].search_for(query, quads=True):
                if seen >= offset:
                    if len(hits) == limit:
                        more = True
                        break
                    hits.append({"page": p, "rect": list(quad.rect), "quad": [list(pt) for pt in quad]})
                seen += 1
            if more:
                break
    return {"path": str(source), "query": query, "source_fingerprint": read._fingerprint(blob), "hits": hits,
            "offset": offset, "next_offset": offset + len(hits) if more else None, "ocr": False}


@route
def signatures(action: str = "list", name: str = "default", source: str | None = None, confirm: bool = False) -> dict:
    """Signature library: list, inspect, add/import (SVG or PNG), remove. Replacing or removing an existing signature requires confirm=true. Results include paths and SHA-256 for independent inspection. Drawing uses record_signature."""
    if action == "list":
        return {"signatures": [signatures("inspect", n) for n in signature.list_names()]}
    signature._validate_name(name)
    if action == "inspect":
        p = signature.get(name)
        return {"name": name, "path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "format": p.suffix[1:]}
    if action in ("add", "import"):
        if not source:
            raise OpError("signature add/import requires source (SVG or PNG)")
        p = Path(source).expanduser().resolve(strict=True)
        if p.suffix.lower() not in (".svg", ".png"):
            raise OpError("signature source must be SVG or PNG")
        signature._decode_candidate(p, p.read_bytes(), p.suffix.lower())
        expected = signature.identity(name)
        if expected is not None and not confirm:
            return {"status": "needs_confirmation", "name": name, "reason": "replace existing signature", "written": False}
        signature.add(p, name, expected=expected)
        return {"status": "saved", **signatures("inspect", name)}
    if action == "remove":
        expected = signature.identity(name)
        result = signatures("inspect", name)
        if not confirm:
            return {"status": "needs_confirmation", **result, "written": False}
        signature.remove(name, expected=expected)
        return {"status": "removed", "name": name, "remaining": signature.list_names()}
    raise OpError("signature action must be list, inspect, add, import or remove")


@route
def record_signature(action: str = "start", name: str = "default", job: str | None = None, click: bool = False, confirm: bool = False) -> dict:
    """Interactive recorder handoff: start opens the real recorder, status reads its completion, cancel requests cancellation. Requires desktop dependencies. No headless drawing. Existing names require confirm=true. A launched job is not a saved signature; poll status for saved/cancelled/failed."""
    from . import handoff
    if action == "start":
        signature._validate_name(name)
        return handoff.start_recording(name, click, confirm=confirm)
    if action in ("status", "cancel") and job:
        return handoff.recording_status(job, cancel=action == "cancel")
    raise OpError("record_signature needs start, or status/cancel with a job ID")


@route
def editor(action: str = "list", session: str | None = None, path: str | None = None,
           ops: list[dict] | None = None, revision: str | None = None, confirm: bool = False,
           index: int | None = None, dx: float = 0, dy: float = 0,
           page: int | None = None, zoom: float | None = None, query: str | None = None,
           fit: bool = False, hit: int = 0) -> dict:
    """Live GUI sessions: list, open, status, stage, replace, select, move, delete, view, search, undo, redo, save, close. Open accepts optional PDF and proposal ops; returns ready only after actual editor status. All mutations require the latest status revision. Save/undo/redo and dirty close require confirm=true. Pending indices are 0-based; pages are 1-based. Stage accepts markup batches or one page operation; no PDF write until Save. Uses actual GTK model, never a simulated session. View zoom is percent (10..800), page is 1-based; fit=true restores fit-page. Search navigates the 0-based hit index in the actual view."""
    from . import editor_session
    if action == "list":
        return editor_session.list_sessions()
    if action == "open":
        return editor_session.open_editor(path, ops)
    if not session:
        raise OpError("editor action needs an exact session ID from list/open")
    return editor_session.request(session, {"action": action, "ops": ops, "revision": revision, "confirm": confirm,
                                           "index": index, "dx": dx, "dy": dy, "page": page, "zoom": zoom, "query": query, "fit": fit, "hit": hit})


@route
def export(path: str, action: str = "copy", output: str | None = None, confirm: bool = False) -> dict:
    """Export saved bytes: copy, zip, flatten, email, localsend, folder, clipboard-file, zip-clipboard. copy/zip require a NEW output path; flatten also requires confirm=true. External-app and clipboard actions require confirm=true, report handoff only (never delivery), and require installed desktop helpers. Source is unchanged. For pending work use editor save first."""
    from .artifacts import publish_new
    allowed = {"copy", "zip", "flatten", "email", "localsend", "folder", "clipboard-file", "zip-clipboard"}
    if action not in allowed:
        raise OpError(f"export action must be one of {', '.join(sorted(allowed))}")
    source, blob = pdf_bytes(path)
    external = action in {"email", "localsend", "folder", "clipboard-file", "zip-clipboard"}
    if action in {"copy", "zip", "flatten", "zip-clipboard"}:
        if not output:
            raise OpError(f"{action} requires an explicit new output path")
        dest = Path(output).expanduser().absolute()
        if dest.exists() or dest.is_symlink():
            raise OpError(f"output exists: {dest}; choose a new path")
        if not dest.parent.is_dir():
            raise OpError(f"output directory does not exist: {dest.parent}; create it first")
    else:
        dest = source
    if (external or action == "flatten") and not confirm:
        return {"status": "needs_confirmation", "action": action, "path": str(source), "output": str(dest), "written": False, "delivered": False}
    helpers = {"email": "xdg-email", "localsend": "localsend", "folder": "xdg-open", "clipboard-file": "wl-copy", "zip-clipboard": "wl-copy"}
    helper = helpers.get(action)
    if helper and not shutil.which(helper):
        raise OpError(f"{helper} is unavailable; install it or use export copy/zip to hand off the file yourself")
    if action == "flatten":
        import tempfile
        # Stage through the shared engine, then publish without clobbering.
        with tempfile.TemporaryDirectory(prefix="omapreview-export-") as tmp:
            from .fs_privacy import atomic_write_private
            snapshot = Path(tmp) / "source.pdf"
            atomic_write_private(snapshot, blob)
            staged = Path(tmp) / "flat.pdf"
            engine.flatten(snapshot, output=staged)
            publish_new(dest, staged.read_bytes())
    elif action == "copy":
        publish_new(dest, blob)
    elif action in {"zip", "zip-clipboard"}:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(source.name, blob)
        publish_new(dest, buf.getvalue())
    result = {"status": "exported", "path": str(dest), "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
              "source_fingerprint": read._fingerprint(blob), "delivered": False}
    if helper:
        if action in {"clipboard-file", "zip-clipboard"}:
            _run_helper([helper, "--type", "text/uri-list"], (dest.resolve().as_uri() + "\n").encode())
            result["status"] = "clipboard-owned"
        else:
            args = {"email": [helper, "--attach", str(source)], "localsend": [helper, str(source)], "folder": [helper, str(source.parent)]}[action]
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            result.update(status="handed_off", pid=proc.pid)
    return result


def _run_helper(argv, data=None):
    try:
        import tempfile
        from .page_clipboard import CLIPBOARD_MAX_BYTES
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(argv, input=data, stdout=output, stderr=subprocess.DEVNULL, timeout=5)
            output.seek(0)
            value = output.read(CLIPBOARD_MAX_BYTES + 1)
        if len(value) > CLIPBOARD_MAX_BYTES:
            raise OpError("clipboard payload exceeds 64 MiB; copy fewer pages")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpError(f"desktop helper failed: {exc}; check the desktop session and installed clipboard tools") from exc
    if result.returncode:
        raise OpError(f"{argv[0]} failed (exit {result.returncode}); check the desktop session")
    return value


@route
def clipboard(action: str, path: str | None = None, pages: list[int] | None = None, output: str | None = None, confirm: bool = False) -> dict:
    """copy-pages publishes a PDF selection to the system clipboard; paste-pages saves clipboard PDF to a NEW output (then insert_pages into the target). Both require confirm=true. Uses wl-copy/wl-paste on Wayland or xclip on X11. Reports actual helper errors. Cut = confirmed copy then separately confirmed delete_pages, never delete on copy failure."""
    from .artifacts import publish_new
    from .page_clipboard import extract_pages_bytes, CLIPBOARD_MAX_BYTES
    if action not in {"copy-pages", "paste-pages"}:
        raise OpError("clipboard action must be copy-pages or paste-pages")
    if action == "copy-pages":
        if not path or not pages:
            raise OpError("copy-pages requires path and pages")
        source, blob = pdf_bytes(path)
        with pymupdf.open(stream=blob, filetype="pdf") as doc:
            data = extract_pages_bytes(doc, pages)
        if len(data) > CLIPBOARD_MAX_BYTES:
            raise OpError("clipboard PDF exceeds 64 MiB; export fewer pages")
    elif not output or Path(output).exists() or Path(output).is_symlink():
        raise OpError("paste-pages requires a new output path")
    if not confirm:
        return {"status": "needs_confirmation", "written": False}
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    helper = ("wl-copy" if action == "copy-pages" else "wl-paste") if wayland else "xclip"
    if not shutil.which(helper):
        raise OpError(f"{helper} unavailable; install a clipboard helper or use extract_pages/insert_pages with files")
    if action == "copy-pages":
        cmd = [helper, "--type", "application/pdf"] if wayland else [helper, "-selection", "clipboard", "-t", "application/pdf"]
        _run_helper(cmd, data)
        return {"status": "clipboard-owned", "pages": pages, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    cmd = [helper, "--no-newline", "--type", "application/pdf"] if wayland else [helper, "-selection", "clipboard", "-t", "application/pdf", "-o"]
    data = _run_helper(cmd)
    if len(data) > CLIPBOARD_MAX_BYTES:
        raise OpError("clipboard PDF exceeds 64 MiB; export fewer pages")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass or not doc.is_pdf or not doc.page_count:
                raise ValueError("clipboard must contain an unlocked, nonempty PDF")
            count = doc.page_count
    except Exception as exc:
        raise OpError(f"clipboard is not a usable PDF: {exc}; copy pages again") from exc
    dest = Path(output).expanduser().absolute()
    publish_new(dest, data)
    return {"status": "saved", "path": str(dest), "pages": count, "sha256": hashlib.sha256(data).hexdigest()}
