"""Exercise editor responses through the local socket protocol."""

import pytest

from omepreview.editor_session import Bridge, request
from omepreview.ops import OpError


@pytest.mark.parametrize(
    ("status", "error"),
    [
        ("complete", None),
        ("failed", {"code": "ocr_failed", "message": "recognition failed"}),
        ("cancelled", {"code": "cancelled", "message": "OCR cancelled"}),
    ],
)
def test_ocr_task_response_survives_socket_round_trip(monkeypatch, tmp_path, status, error):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    task = {
        "task_id": "a" * 32, "status": status, "phase": "complete",
        "completed": 5, "total": 5, "destination": str(tmp_path / "copy.pdf"),
        "output": str(tmp_path / "copy.pdf") if status == "complete" else None,
        "result": {"applied": []} if status == "complete" else None,
        "error": error, "source_revision": "revision", "session_changed": False,
        "cancellation_requested": status == "cancelled",
    }
    bridge = Bridge(object(), lambda callback: callback(), lambda: None, lambda: None)
    monkeypatch.setattr(bridge, "handle", lambda payload: task)
    try:
        assert request(bridge.session, {"action": "ocr_status", "task_id": task["task_id"]}) == task
    finally:
        bridge.close()


def test_protocol_error_remains_an_error_after_socket_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    bridge = Bridge(object(), lambda callback: callback(), lambda: None, lambda: None)

    def fail(_payload):
        raise OpError("unknown editor action; see workflow-schema")

    monkeypatch.setattr(bridge, "handle", fail)
    try:
        with pytest.raises(OpError, match="unknown editor action"):
            request(bridge.session, {"action": "unknown"})
    finally:
        bridge.close()
