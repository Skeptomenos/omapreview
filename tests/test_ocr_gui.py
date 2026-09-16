"""Editor OCR task and bridge safety without a desktop display."""

import hashlib
import threading

import pytest

from omepreview import engine
from omepreview.editor_session import Bridge, OCRTask
from omepreview.gui import Editor, _ocr_can_auto_open
from omepreview.ops import OpError
from tests.data.make_docs import make_labeled_pdf


TASK_FIELDS = {
    "task_id", "status", "phase", "completed", "total", "destination",
    "output", "result", "error", "source_revision", "session_changed",
    "cancellation_requested",
}


def wait_done(task):
    assert task.join(3)
    return task.snapshot("initial")


def test_task_shape_progress_and_saved_readback(monkeypatch, tmp_path):
    source = make_labeled_pdf(tmp_path / "source.pdf", page_count=2)
    destination = tmp_path / "copy.pdf"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    entered = threading.Event()
    release = threading.Event()

    def apply(path, ops, *, output, dry_run, cancel_event, progress):
        progress({"phase": "preflight", "completed": 0, "total": 5})
        if dry_run:
            return {"applied": [{"ocr": {"source_sha256": digest}}]}
        entered.set()
        assert release.wait(2)
        destination.write_bytes(source.read_bytes())
        progress({"phase": "complete", "completed": 5, "total": 5})
        return {"output": str(destination), "applied": [{"ocr": {
            "status": "complete", "output_sha256": digest, "pages": [{}, {}],
        }}]}

    monkeypatch.setattr(engine, "apply", apply)
    task = OCRTask()
    started = task.start(source, {"op": "ocr", "expected_source_sha256": digest},
                         destination, "initial")
    assert set(started) == TASK_FIELDS
    assert started["output"] is None and started["destination"] == str(destination)
    assert entered.wait(2)
    assert task.snapshot("changed")["session_changed"] is True
    assert task.snapshot("initial")["session_changed"] is False
    release.set()
    finished = wait_done(task)
    assert finished["status"] == "complete"
    assert finished["output"] == str(destination)
    assert finished["result"]["applied"][0]["ocr"]["output_sha256"] == digest
    assert source.read_bytes() == destination.read_bytes()


def test_task_cancel_and_failed_copy_remain_unpublished(monkeypatch, tmp_path):
    source = make_labeled_pdf(tmp_path / "source.pdf")
    destination = tmp_path / "copy.pdf"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    entered = threading.Event()

    def apply(path, ops, *, output, dry_run, cancel_event, progress):
        if dry_run:
            return {"applied": [{"ocr": {"source_sha256": digest}}]}
        entered.set()
        assert cancel_event.wait(2)
        from omepreview.ocr import OCRError
        raise OCRError("cancelled", "OCR cancelled")

    monkeypatch.setattr(engine, "apply", apply)
    task = OCRTask()
    started = task.start(source, {"op": "ocr", "expected_source_sha256": digest},
                         destination, "initial")
    assert entered.wait(2)
    task.cancel(started["task_id"])
    assert task.snapshot()["status"] == "cancelling"
    assert task.snapshot()["cancellation_requested"] is True
    finished = wait_done(task)
    assert finished["status"] == "cancelled" and finished["output"] is None
    assert finished["error"]["code"] == "cancelled"
    assert not destination.exists()


def test_bridge_refuses_dirty_or_stale_start_and_keeps_history(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    source = make_labeled_pdf(tmp_path / "source.pdf", page_count=2)
    ed = Editor(str(source), None)
    bridge = Bridge(ed, lambda callback: callback(), lambda: None, lambda: None)
    try:
        initial = bridge.status()
        op = {"op": "ocr", "expected_source_sha256": initial["source_fingerprint"]}
        output = str(tmp_path / "copy.pdf")
        proposal = bridge.handle({"action": "ocr_start", "revision": initial["revision"],
                                  "op": op, "output": output})
        assert proposal["status"] == "needs_confirmation"
        assert not (tmp_path / "copy.pdf").exists()
        ed.zoom_pct = 125
        with pytest.raises(OpError, match="revision changed"):
            bridge.handle({"action": "ocr_start", "revision": initial["revision"],
                           "op": op, "output": output, "confirm": True})
        before_reopen = bridge.status()
        ed.open_path(str(source))
        after_reopen = bridge.status()
        assert after_reopen["document_epoch"] == before_reopen["document_epoch"] + 1
        assert after_reopen["revision"] != before_reopen["revision"]
        ed.stage_page_operation({"op": "rotate_pages", "pages": [1], "degrees": 90})
        dirty = bridge.status()
        assert dirty["dirty"] and dirty["page_ops"]
        with pytest.raises(OpError, match="save or discard"):
            bridge.handle({"action": "ocr_start", "revision": dirty["revision"],
                           "op": op, "output": output, "confirm": True})
        assert bridge.status()["page_ops"] == dirty["page_ops"]
        assert not (tmp_path / "copy.pdf").exists()
    finally:
        bridge.close()
        ed.close()


def test_bridge_completion_keeps_new_editor_document_until_explicit_open(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    source = make_labeled_pdf(tmp_path / "scan.pdf", page_count=1)
    other = make_labeled_pdf(tmp_path / "other.pdf", page_count=2)
    output = tmp_path / "recognized.pdf"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    entered = threading.Event()
    release = threading.Event()

    def apply(path, ops, *, output, dry_run, cancel_event, progress):
        if dry_run:
            return {"applied": [{"ocr": {"source_sha256": digest}}]}
        entered.set()
        assert release.wait(2)
        (tmp_path / "recognized.pdf").write_bytes(source.read_bytes())
        return {"output": str(output), "applied": [{"ocr": {
            "status": "complete", "output_sha256": digest, "pages": [{}],
        }}]}

    monkeypatch.setattr(engine, "apply", apply)
    ed = Editor(str(source), None)
    bridge = Bridge(ed, lambda callback: callback(), lambda: None, lambda: None)
    try:
        state = bridge.status()
        started = bridge.handle({"action": "ocr_start", "revision": state["revision"],
                                 "confirm": True, "output": str(output),
                                 "op": {"op": "ocr", "expected_source_sha256": digest}})
        assert set(started) == TASK_FIELDS
        assert entered.wait(2)
        ed.open_path(str(other))
        while_running = bridge.handle({"action": "ocr_status", "task_id": started["task_id"]})
        assert while_running["session_changed"] and ed.path == str(other)
        release.set()
        finished = wait_done(bridge.ocr_task)
        assert finished["output"] == str(output)
        assert bridge.handle({"action": "ocr_status", "task_id": started["task_id"]})["session_changed"]
        assert ed.path == str(other) and ed.page_count() == 2
        ed.open_path(str(output))
        assert ed.path == str(output) and ed.page_count() == 1
    finally:
        bridge.close()
        ed.close()


def test_bridge_close_cancels_worker_and_failed_preflight_has_no_output(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    source = make_labeled_pdf(tmp_path / "scan.pdf")
    output = tmp_path / "recognized.pdf"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    entered = threading.Event()

    def apply(path, ops, *, output, dry_run, cancel_event, progress):
        entered.set()
        assert cancel_event.wait(2)
        from omepreview.ocr import OCRError
        raise OCRError("cancelled", "OCR cancelled")

    monkeypatch.setattr(engine, "apply", apply)
    ed = Editor(str(source), None)
    bridge = Bridge(ed, lambda callback: callback(), lambda: None, lambda: None)
    state = bridge.status()
    bridge.handle({"action": "ocr_start", "revision": state["revision"],
                   "confirm": True, "output": str(output),
                   "op": {"op": "ocr", "expected_source_sha256": digest}})
    assert entered.wait(2)
    bridge.close()
    assert bridge.ocr_task.join(3)
    assert bridge.ocr_task.snapshot()["status"] == "cancelled"
    assert not output.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    ed.close()


def test_auto_open_requires_no_history_or_session_change(tmp_path):
    source = make_labeled_pdf(tmp_path / "scan.pdf", page_count=1)
    ed = Editor(str(source), None)
    try:
        assert _ocr_can_auto_open({"session_changed": False}, ed)
        ed.undo_stack.append({"kind": "pending"})
        assert not _ocr_can_auto_open({"session_changed": False}, ed)
        ed.open_path(str(source))
        assert _ocr_can_auto_open({"session_changed": False}, ed)
        assert not _ocr_can_auto_open({"session_changed": True}, ed)
    finally:
        ed.close()
