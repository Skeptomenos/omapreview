"""Private local-user IPC to real GTK editor instances; no headless surrogate."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
import uuid

from .fs_privacy import scratch_dir, ensure_private_dir, atomic_write_private
from .ops import OpError, validate_all

MAX_MESSAGE = 1024 * 1024


class OCRTask:
    """One editor-owned OCR request. The worker never reads the GTK model."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = threading.Event()
        self._state = None

    def snapshot(self, revision=None):
        with self._lock:
            if self._state is None:
                return None
            state = copy.deepcopy(self._state)
        state["session_changed"] = revision is not None and revision != state["source_revision"]
        return state

    def start(self, source, op, output, source_revision):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise OpError("OCR is already running in this editor; inspect or cancel its task")
            self._cancel = threading.Event()
            self._state = {
                "task_id": uuid.uuid4().hex, "status": "running", "phase": "queued",
                "completed": 0, "total": 5, "destination": str(output),
                "output": None, "result": None, "error": None,
                "source_revision": source_revision,
                "cancellation_requested": False,
            }
            self._thread = threading.Thread(
                target=self._run, args=(str(source), copy.deepcopy(op), str(output)),
                daemon=True, name="omapreview-ocr",
            )
            self._thread.start()
        return self.snapshot(source_revision)

    def _progress(self, event):
        with self._lock:
            if self._state is not None and self._state["status"] == "running":
                self._state.update({key: event[key] for key in ("phase", "completed", "total") if key in event})

    def _run(self, source, op, output):
        from . import engine
        from .ocr import OCRError
        try:
            proposal = engine.apply(source, [{key: value for key, value in op.items()
                                              if key != "expected_source_sha256"}],
                                    output=output, dry_run=True,
                                    cancel_event=self._cancel, progress=self._progress)
            approved = op["expected_source_sha256"]
            observed = proposal["applied"][0]["ocr"]["source_sha256"]
            if observed != approved:
                raise OCRError("source_conflict", "OCR source changed since approval; inspect the document and run preflight again")
            result = engine.apply(source, [op], output=output, dry_run=False,
                                  cancel_event=self._cancel, progress=self._progress)
            report = result["applied"][0]["ocr"]
            published = Path(result["output"])
            with self._lock:
                self._state.update(output=str(published), result=result)
            with published.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != report["output_sha256"]:
                raise OpError("OCR copy changed after publication; inspect the saved file before opening it")
            import pymupdf
            with pymupdf.open(published) as doc:
                if doc.page_count != len(report["pages"]):
                    raise OpError("OCR copy page count changed after publication; inspect the saved file")
            with self._lock:
                self._state.update(status=report["status"], output=str(published),
                                   result=result, phase="complete", completed=5)
        except Exception as exc:
            code = getattr(exc, "code", "verification_failed" if isinstance(exc, OpError) else "ocr_failed")
            detail = {"code": code, "message": str(exc), "ocr": getattr(exc, "ocr", None)}
            with self._lock:
                self._state.update(
                    status={"cancelled": "cancelled", "worker_timeout": "timed_out"}.get(code, "failed"),
                    error=detail,
                )

    def cancel(self, task_id=None):
        with self._lock:
            if self._state is None or (task_id is not None and task_id != self._state["task_id"]):
                raise OpError("OCR task not found in this editor; read editor status")
            if self._state["status"] == "running":
                self._state["status"] = "cancelling"
                self._state["cancellation_requested"] = True
                self._cancel.set()

    def join(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()


def directory():
    root = scratch_dir() / "sessions"
    # AF_UNIX has a 108-byte pathname limit on Linux. Preserve namespace
    # isolation even when a test/runtime root is deeply nested.
    if len(os.fsencode(root)) > 60:
        import tempfile
        key = hashlib.sha256(os.fsencode(root)).hexdigest()[:16]
        root = Path(tempfile.gettempdir()) / f"omapreview-{os.getuid()}-sessions-{key}"
    if root.is_symlink() or (root.exists() and root.stat().st_uid != os.getuid()):
        raise OpError("unsafe editor session directory; choose a private XDG_RUNTIME_DIR")
    return ensure_private_dir(root)


def _path(session):
    if not re.fullmatch(r"[a-f0-9]{32}", session):
        raise OpError("invalid session ID; use editor list/open")
    return directory() / (session + ".sock")


def _receive(conn):
    data = bytearray()
    while b"\n" not in data:
        chunk = conn.recv(min(65536, MAX_MESSAGE + 1 - len(data)))
        if not chunk:
            raise OpError("editor connection closed; inspect session state before retrying")
        data.extend(chunk)
        if len(data) > MAX_MESSAGE:
            raise OpError("editor message exceeds 1 MiB; split the proposal")
    return json.loads(bytes(data).split(b"\n", 1)[0])


def request(session, payload):
    message = json.dumps(payload).encode() + b"\n"
    if len(message) > MAX_MESSAGE:
        raise OpError("editor request exceeds 1 MiB; split the proposal")
    try:
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(15)
            conn.connect(str(_path(session)))
            conn.sendall(message)
            response = _receive(conn)
    except (OSError, ValueError) as exc:
        raise OpError(f"editor session unavailable or timed out: {session}; list sessions and inspect status before retrying") from exc
    # The protocol error is a single-field envelope. OCR task records also
    # carry an error field, including None for successful tasks.
    if isinstance(response, dict) and set(response) == {"error"}:
        raise OpError(response["error"])
    return response


def list_sessions():
    sessions = []
    unavailable = []
    for p in sorted(directory().glob("*.sock")):
        try:
            sessions.append(request(p.stem, {"action": "status"}))
        except OpError:
            unavailable.append(p.stem)
    return {"sessions": sessions, "unavailable": unavailable}


def desktop_ready():
    import importlib.util
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise OpError("no desktop display; run from a graphical session or use apply for file edits")
    if importlib.util.find_spec("gi") is None or importlib.util.find_spec("cairo") is None:
        raise OpError("GTK dependencies unavailable; install python-gobject/python-cairo/gtk4 and use a --system-site-packages venv")


def open_editor(path=None, ops=None):
    import subprocess
    import sys
    from .workflows import pdf_bytes
    desktop_ready()
    if path:
        path = str(pdf_bytes(path)[0])
    elif ops:
        raise OpError("proposal ops require a PDF path")
    if ops is not None:
        validate_all(ops)
    session = uuid.uuid4().hex
    argv = [sys.executable, "-m", "omepreview.cli", "edit", "--session-id", session]
    if path:
        argv.append(path)
    proposal = directory() / (session + ".json")
    if ops:
        atomic_write_private(proposal, json.dumps(ops).encode())
        argv.extend(["--ops", str(proposal)])
    log = directory() / (session + ".log")
    fd = os.open(log, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as errors:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=errors, stderr=errors, start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _path(session).exists():
                result = request(session, {"action": "status"})
                return {"status": "ready", **result, "saved": False}
            if proc.poll() is not None:
                raise OpError(f"editor failed to open; see {log}; PDF was not saved")
            time.sleep(0.05)
        return {"status": "starting", "session": session, "pid": proc.pid, "log": str(log), "saved": False}
    finally:
        # The editor may still be starting after a timeout; retain its input.
        if _path(session).exists() or proc.poll() is not None:
            proposal.unlink(missing_ok=True)


class Bridge:
    def __init__(self, editor, dispatch, refresh, close, session=None, background_busy=None):
        self.editor = editor
        self.dispatch = dispatch
        self.refresh = refresh
        self.close_window = close
        self.background_busy = background_busy or (lambda: False)
        self.session = session or uuid.uuid4().hex
        self.ocr_task = OCRTask()
        self.path = _path(self.session)
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.sock.listen(4)
        self.sock.settimeout(0.2)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self):
        if self.ocr_task.snapshot() is not None:
            self.ocr_task.cancel()
        self.stopped.set()
        self.sock.close()
        self.path.unlink(missing_ok=True)

    def _serve(self):
        while not self.stopped.is_set():
            try:
                conn, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(15)
                try:
                    payload = _receive(conn)
                    # Only the GTK main loop touches the editor model.
                    done = threading.Event()
                    state = {"cancelled": False}
                    def invoke(payload=payload, state=state, done=done):
                        if state["cancelled"]:
                            return False
                        try:
                            state["response"] = self.handle(payload)
                        except (OpError, ValueError, OSError) as exc:
                            state["response"] = {"error": str(exc)}
                        except Exception:
                            state["response"] = {"error": "editor action failed; inspect status before retrying"}
                        finally:
                            done.set()
                        return False
                    self.dispatch(invoke)
                    if not done.wait(12):
                        state["cancelled"] = True
                        response = {"error": "editor is busy; inspect status before retrying (outcome may be uncertain)"}
                    else:
                        response = state["response"]
                    data = json.dumps(response).encode() + b"\n"
                    if len(data) > MAX_MESSAGE:
                        data = b'{"error":"editor state exceeds 1 MiB; use the GUI to reduce pending work"}\n'
                    conn.sendall(data)
                except (OSError, ValueError, OpError):
                    pass

    def status(self):
        ed = self.editor
        pending = ed.to_ops(execution_order=False) if ed.has_document() else []
        body = {"session": self.session, "pid": os.getpid(), "path": ed.path,
                "source_fingerprint": ed._source_fingerprint,
                "document_epoch": ed._document_epoch,
                "pending": pending, "page_ops": copy.deepcopy(ed.page_preview.page_ops),
                "undo_depth": len(ed.undo_stack), "redo_depth": len(ed.redo_stack),
                "page": ed.page_no + 1 if ed.has_document() else None, "page_count": ed.page_count(),
                "zoom": ed.zoom_pct, "conflict": ed.external_conflict,
                "selected_index": next((i for i, item in enumerate(ed.pending) if item is ed.selected), None),
                "dirty": bool(ed.pending or ed.page_preview.has_changes())}
        body["revision"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        body["ocr"] = self.ocr_task.snapshot(body["revision"])
        return body

    def start_ocr(self, op, output, *, revision, confirm):
        state = self.status()
        ed = self.editor
        if state["revision"] != revision:
            raise OpError("editor revision changed; read status and review the document before OCR")
        if confirm is not True:
            return {"status": "needs_confirmation", "action": "ocr_start", **state}
        if not ed.has_document():
            raise OpError("open a PDF before recognizing text")
        if state["dirty"]:
            raise OpError("save or discard pending edits before recognizing text")
        if ed.external_conflict:
            raise OpError("source changed on disk; reopen it before recognizing text")
        if not isinstance(output, str) or not output or not Path(output).is_absolute():
            raise OpError("OCR needs an absolute path for a new copy")
        validated = validate_all([op])
        if len(validated) != 1 or validated[0]["op"] != "ocr":
            raise OpError("OCR start needs one OCR operation")
        if validated[0].get("expected_source_sha256") != ed._source_fingerprint:
            raise OpError("OCR source hash differs from this editor; preflight the saved source again")
        return self.ocr_task.start(ed.path, validated[0], output, revision)

    def handle(self, payload):
        if not isinstance(payload, dict):
            raise OpError("editor request must be an object")
        action = payload.get("action")
        state = self.status()
        if action == "status":
            return state
        if action == "ocr_start":
            return self.start_ocr(payload.get("op"), payload.get("output"),
                                  revision=payload.get("revision"), confirm=payload.get("confirm"))
        if action in {"ocr_status", "ocr_cancel"}:
            task = self.ocr_task.snapshot(state["revision"])
            if task is None or payload.get("task_id") != task["task_id"]:
                raise OpError("OCR task not found in this editor; read editor status")
            if action == "ocr_cancel":
                self.ocr_task.cancel(task["task_id"])
            return self.ocr_task.snapshot(state["revision"])
        allowed = {"stage", "replace", "select", "move", "delete", "view", "search", "undo", "redo", "save", "close"}
        if action not in allowed:
            raise OpError("unknown editor action; see workflow-schema")
        if payload.get("revision") != state["revision"]:
            raise OpError("editor revision changed or missing; read status and review the current target before retrying")
        ed = self.editor
        if action in {"save", "undo", "redo"} or (action == "close" and state["dirty"]):
            if payload.get("confirm") is not True:
                return {"status": "needs_confirmation", "action": action, **state}
        result = {}
        if action in {"stage", "replace"}:
            index = payload.get("index")
            if action == "replace":
                if type(index) is not int or not 0 <= index < len(ed.pending):
                    raise OpError("replace requires a valid pending index")
                from .gui import SUPPORTED_PROPOSAL_OPS
                ops = validate_all(payload.get("ops"))
                if len(ops) != 1 or ops[0]["op"] not in SUPPORTED_PROPOSAL_OPS:
                    raise OpError("replace needs exactly one markup operation")
            original_count = len(ed.pending)
            self.stage(payload.get("ops"))
            if action == "replace":
                # Keep the operation's position, including multi-hit expansion:
                # field overwrites and overlapping marks depend on this order.
                replacements = ed.pending[original_count:]
                del ed.pending[original_count:]
                ed.pending[index:index + 1] = replacements
        elif action in {"select", "move", "delete"}:
            index = payload.get("index")
            if type(index) is not int or not 0 <= index < len(ed.pending):
                raise OpError("pending index out of range; read status.pending (0-based)")
            ed.selected = ed.pending[index]
            ed.page_no = ed.selected["page"]
            if action == "move":
                dx, dy = payload.get("dx", 0), payload.get("dy", 0)
                import math
                if any(type(v) not in (float, int) or not math.isfinite(v) or abs(v) > 1e6 for v in (dx, dy)):
                    raise OpError("dx/dy must be finite PDF-point offsets within +/-1000000")
                if ed.selected["kind"] in ("field_fill", "delete_annot"):
                    raise OpError("this pending item has a fixed document target; replace it with an exact new target")
                ed.checkpoint()
                ed.move_item(ed.selected, dx, dy)
            elif action == "delete":
                ed.delete_selected()
        elif action == "view":
            page, zoom = payload.get("page"), payload.get("zoom")
            if page is not None and (type(page) is not int or not 1 <= page <= ed.page_count()):
                raise OpError("view page must be within the open document")
            if zoom is not None and (type(zoom) not in (int, float) or not 10 <= zoom <= 800):
                raise OpError("view zoom must be 10..800 percent")
            if page is not None:
                ed.page_no = page - 1
            if zoom is not None:
                ed.zoom_pct = zoom
            if payload.get("fit") is True:
                ed.zoom_pct = None
        elif action == "search":
            query = payload.get("query")
            if not isinstance(query, str) or len(query) > 4096:
                raise OpError("editor search requires query up to 4096 characters")
            doc = ed.page_doc()
            ed.search_term = query
            ed.search_hits = [(n, r) for n in range(doc.page_count) for r in (doc[n].search_for(query) if query else [])]
            hit = payload.get("hit", 0)
            if type(hit) is not int or hit < 0 or (ed.search_hits and hit >= len(ed.search_hits)):
                raise OpError("search hit index is out of range; use 0-based index from returned hits")
            ed.search_pos = hit if ed.search_hits else -1
            if ed.search_hits:
                ed.page_no = ed.search_hits[hit][0]
            result["hits"] = [{"page": p + 1, "rect": list(r)} for p, r in ed.search_hits]
        elif action in {"undo", "redo"}:
            result["changed"] = getattr(ed, action)()
        elif action == "save":
            result["save"] = ed.save_pending()
        elif action == "close":
            active_ocr = bool(state["ocr"] and state["ocr"]["status"] in ("running", "cancelling"))
            if active_ocr:
                self.ocr_task.cancel(state["ocr"]["task_id"])
            closing = active_ocr or self.background_busy()
            self.close_window()
            return {"status": "closing" if closing else "closed", "session": self.session,
                    "discarded_pending": state["dirty"]}
        self.refresh()
        return {"status": "ok", **self.status(), **result}

    def stage(self, ops):
        from .gui import Editor, SUPPORTED_PROPOSAL_OPS
        ed = self.editor
        validated = validate_all(ops)
        if not validated:
            raise OpError("stage needs a nonempty operation list")
        ed._ensure_source_current()
        page_kinds = {"delete_pages", "rotate_pages", "move_pages", "insert_pages", "crop_pages"}
        if len(validated) == 1 and validated[0]["op"] in page_kinds:
            ed.stage_page_operation(validated[0])
            return
        if any(op["op"] not in SUPPORTED_PROPOSAL_OPS for op in validated):
            raise OpError("stage one page operation at a time, or a markup batch; extract_pages uses export/apply")
        import tempfile
        # Resolve against the current page preview, so post-surgery page targets agree.
        with tempfile.TemporaryDirectory(dir=directory()) as tmp:
            source = ed.page_preview.scratch_path or ed.path
            proposal = Path(tmp) / "ops.json"
            atomic_write_private(proposal, json.dumps(validated).encode())
            trial = Editor(source, str(proposal))
            try:
                proposed = copy.deepcopy(trial.pending)
            finally:
                trial.close()
        ed.checkpoint()
        ed.pending.extend(proposed)
        ed.selected = proposed[0] if proposed else None
        if proposed:
            ed.page_no = proposed[0]["page"]
