"""Workflow parity through actual console entry points and real stdio MCP."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pymupdf
import pytest

from omepreview import workflows
from test_batch_h import _result_data, _result_value

CLI = str(Path(sys.executable).with_name("omapreview"))
MCP = str(Path(sys.executable).with_name("omapreview-mcp"))


@asynccontextmanager
async def client(mode, env):
    if mode == "cli":
        async def call(name, arguments=None, error=False):
            cmd = [CLI, "workflow-schema"] if name == "schema" else [CLI, "workflow", name, "--args", json.dumps(arguments or {})]
            p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
            if error:
                assert p.returncode != 0, p.stdout
                return p.stderr
            assert p.returncode == 0, p.stderr
            return json.loads(p.stdout)
        yield call
    else:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async with stdio_client(StdioServerParameters(command=MCP, env=env)) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert {"workflow_schema", "run_workflow"} <= {t.name for t in listed.tools}
                async def call(name, arguments=None, error=False):
                    tool = "workflow_schema" if name == "schema" else "run_workflow"
                    args = {} if name == "schema" else {"name": name, "arguments": arguments or {}}
                    result = await session.call_tool(tool, args)
                    if error:
                        assert _result_value(result, "is_error"), result
                        return str(result.content)
                    assert not _result_value(result, "is_error"), result
                    return _result_data(result)
                yield call


def fixtures(tmp_path):
    source = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        for n in range(3):
            page = doc.new_page(width=240, height=180)
            page.insert_text((20, 30), f"TARGET {n} CONTROL")
            if n == 1:
                page.set_rotation(90)
            if n == 2:
                page.set_cropbox(pymupdf.Rect(10, 10, 230, 170))
        doc.save(source)
    sig = tmp_path / "synthetic.svg"
    sig.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="60" height="30"><path d="M1 20 L50 5" stroke="black"/></svg>')
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    for name in ("wl-copy", "wl-paste", "xdg-email", "localsend", "xdg-open", "xclip"):
        p = helpers / name
        p.write_text(f'#!{sys.executable}\nimport os,sys,pathlib\np=pathlib.Path(os.environ["WORKFLOW_CLIPBOARD"])\n'
                     'if "paste" in sys.argv[0] or "-o" in sys.argv: sys.stdout.buffer.write(p.read_bytes())\n'
                     'elif "copy" in sys.argv[0] or "xclip" in sys.argv[0]: p.write_bytes(sys.stdin.buffer.read())\n'
                     'else: pathlib.Path(os.environ["WORKFLOW_HANDOFF"]).write_text(" ".join(sys.argv))\n')
        p.chmod(0o700)
    env = dict(os.environ, OMEPREVIEW_SIGNATURE_DIR=str(tmp_path / "signatures"),
               XDG_RUNTIME_DIR=str(tmp_path / "runtime"), PATH=str(helpers),
               WORKFLOW_CLIPBOARD=str(tmp_path / "clipboard"), WORKFLOW_HANDOFF=str(tmp_path / "handoff"), WAYLAND_DISPLAY="synthetic")
    return source, sig, env


@pytest.mark.parametrize("mode", ["cli", "mcp"])
def test_saved_workflows(mode, tmp_path):
    source, sig, env = fixtures(tmp_path)
    before = source.read_bytes()
    async def check():
        async with client(mode, env) as call:
            schema = await call("schema")
            assert set(schema["workflows"]) == {"search", "signatures", "record_signature", "editor", "export", "clipboard"}
            hits = await call("search", {"path": str(source), "query": "TARGET", "limit": 2})
            assert len(hits["hits"]) == 2 and hits["next_offset"] == 2
            rest = await call("search", {"path": str(source), "query": "TARGET", "offset": 2})
            assert len(rest["hits"]) == 1 and rest["hits"][0]["page"] == 3
            with pymupdf.open(source) as doc:
                assert rest["hits"][0]["rect"] == list(doc[2].search_for("TARGET")[0])
            assert not (await call("search", {"path": str(source), "query": "absent"}))["hits"]
            for args in ({"query": ""}, {"query": "TARGET", "pages": [0]}, {"query": "TARGET", "limit": True}, {"query": "TARGET", "unknown": 1}):
                await call("search", {"path": str(source), **args}, error=True)
            await call("search", {"path": str(tmp_path / "missing"), "query": "x"}, error=True)
            await call("signatures", {"action": "add", "source": str(sig), "name": "synthetic"})
            stored = await call("signatures", {"action": "inspect", "name": "synthetic"})
            assert Path(stored["path"]).read_bytes() == sig.read_bytes()
            assert stored["sha256"] == hashlib.sha256(sig.read_bytes()).hexdigest()
            assert (await call("signatures"))["signatures"][0]["name"] == "synthetic"
            assert (await call("signatures", {"action": "import", "source": str(sig), "name": "synthetic"}))["status"] == "needs_confirmation"
            await call("signatures", {"action": "import", "source": str(sig), "name": "synthetic", "confirm": True})
            invalid = tmp_path / "bad.svg"; invalid.write_text("invalid")
            await call("signatures", {"action": "add", "source": str(invalid), "name": "synthetic", "confirm": True}, error=True)
            assert Path(stored["path"]).read_bytes() == sig.read_bytes()
            await call("signatures", {"action": "add", "source": str(sig), "name": "../escape"}, error=True)
            assert (await call("signatures", {"action": "remove", "name": "synthetic"}))["status"] == "needs_confirmation"
            for action in ("copy", "zip", "flatten"):
                out = tmp_path / f"{action}.out"
                args = {"path": str(source), "action": action, "output": str(out)}
                if action == "flatten":
                    assert (await call("export", args))["status"] == "needs_confirmation"
                    assert not out.exists()
                result = await call("export", {**args, "confirm": True})
                assert result["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
                if action == "zip":
                    with zipfile.ZipFile(out) as archive:
                        assert archive.read(source.name) == before
                elif action == "copy":
                    assert out.read_bytes() == before
                else:
                    with pymupdf.open(out) as doc:
                        assert "CONTROL" in doc[0].get_text()
                await call("export", {**args, "confirm": True}, error=True)
            for action in ("email", "localsend", "folder", "clipboard-file", "zip-clipboard"):
                args = {"path": str(source), "action": action}
                if action == "zip-clipboard": args["output"] = str(tmp_path / "out.zip")
                assert (await call("export", args))["status"] == "needs_confirmation"
                result = await call("export", {**args, "confirm": True})
                assert result["delivered"] is False
                assert result["status"] in {"handed_off", "clipboard-owned"}
            args = {"action": "copy-pages", "path": str(source), "pages": [3, 1]}
            assert (await call("clipboard", args))["status"] == "needs_confirmation"
            await call("clipboard", {**args, "confirm": True})
            out = tmp_path / "pasted.pdf"
            await call("clipboard", {"action": "paste-pages", "output": str(out), "confirm": True})
            with pymupdf.open(out) as doc:
                assert len(doc) == 2 and "TARGET 2" in doc[0].get_text() and "TARGET 0" in doc[1].get_text()
            await call("clipboard", {"action": "paste-pages", "output": str(out), "confirm": True}, error=True)
            Path(env["WORKFLOW_CLIPBOARD"]).write_bytes(b"not a PDF")
            await call("clipboard", {"action": "paste-pages", "output": str(tmp_path / "bad.pdf"), "confirm": True}, error=True)
            await call("signatures", {"action": "remove", "name": "synthetic", "confirm": True})
            assert not (await call("signatures"))["signatures"]
            await call("signatures", {"action": "inspect", "name": "synthetic"}, error=True)
            await call("editor", {"action": "status", "session": "bad"}, error=True)
            await call("record_signature", {"action": "status", "job": "bad"}, error=True)
    asyncio.run(check())
    assert source.read_bytes() == before


@pytest.mark.parametrize("mode", ["cli", "mcp"])
def test_unavailable_dependencies(mode, tmp_path):
    source, sig, env = fixtures(tmp_path)
    env["PATH"] = str(tmp_path / "no-tools")
    env.pop("DISPLAY", None); env.pop("WAYLAND_DISPLAY", None)
    async def check():
        async with client(mode, env) as call:
            for action in ("email", "localsend", "folder", "clipboard-file"):
                error = await call("export", {"path": str(source), "action": action, "confirm": True}, error=True)
                assert "unavailable" in error
            await call("record_signature", {}, error=True)
            await call("editor", {"action": "open", "path": str(source)}, error=True)
            await call("clipboard", {"action": "copy-pages", "path": str(source), "pages": [1], "confirm": True}, error=True)
    asyncio.run(check())


def test_editor_bridge_real_model(tmp_path, monkeypatch):
    from omepreview.gui import Editor
    from omepreview.editor_session import Bridge
    source, sig, env = fixtures(tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", env["XDG_RUNTIME_DIR"])
    ed = Editor(str(source), None)
    bridge = Bridge(ed, lambda cb: cb(), lambda: None, lambda: None)
    try:
        def act(action, **kwargs):
            return bridge.handle({"action": action, "revision": bridge.status()["revision"], **kwargs})
        original = source.read_bytes()
        act("stage", ops=[{"op": "note", "page": 1, "at": [80, 80], "text": "review"}])
        with pytest.raises(Exception, match="revision"):
            bridge.handle({"action": "delete", "index": 0, "revision": "stale"})
        assert act("save")["status"] == "needs_confirmation"
        assert source.read_bytes() == original
        act("move", index=0, dx=10, dy=5)
        act("replace", index=0, ops=[{"op": "note", "page": 1, "at": [90, 85], "text": "revised"}])
        assert len(ed.pending) == 1 and ed.to_ops()[0]["text"] == "revised"
        act("undo", confirm=True)
        assert ed.to_ops()[0]["text"] == "review"
        act("redo", confirm=True)
        act("save", confirm=True)
        with pymupdf.open(source) as doc:
            assert next(doc[0].annots()).info["content"] == "revised"
            assert "CONTROL" in doc[0].get_text()
        act("undo", confirm=True)
        assert source.read_bytes() == original
        act("redo", confirm=True)
        act("stage", ops=[{"op": "rotate_pages", "pages": [1], "degrees": 90}])
        act("stage", ops=[{"op": "note", "page": 1, "at": [50, 80], "text": "after page op"}])
        act("save", confirm=True)
        assert ed.doc[0].rotation == 90
        with pytest.raises(Exception):
            act("stage", ops=[{"op": "note", "page": 99, "at": [20, 20], "text": "invalid"}])
        source.write_bytes(original)
        with pytest.raises(Exception, match="changed"):
            act("stage", ops=[{"op": "note", "page": 1, "at": [20, 20], "text": "conflict"}])
    finally:
        bridge.close(); ed.close()


def test_failed_page_stage_keeps_history_and_retained_source(tmp_path, monkeypatch):
    from omepreview.gui import Editor
    from omepreview.editor_session import Bridge
    source, _, env = fixtures(tmp_path)
    inserted = tmp_path / 'insert.pdf'
    inserted.write_bytes(source.read_bytes())
    monkeypatch.setenv('XDG_RUNTIME_DIR', env['XDG_RUNTIME_DIR'])
    ed = Editor(str(source), None)
    bridge = Bridge(ed, lambda cb: cb(), lambda: None, lambda: None)
    try:
        def act(action, **kw):
            return bridge.handle({'action': action, 'revision': bridge.status()['revision'], **kw})
        act('stage', ops=[{'op': 'insert_pages', 'after': 0, 'source': str(inserted)}])
        retained = Path(ed.page_preview.page_ops[0]['source'])
        inserted.unlink()
        act('undo', confirm=True)
        assert retained.is_file() and ed.redo_stack
        with pytest.raises(Exception):
            act('stage', ops=[{'op': 'rotate_pages', 'pages': [99], 'degrees': 90}])
        assert retained.is_file() and ed.redo_stack
        act('redo', confirm=True)
        act('save', confirm=True)
        assert ed.page_count() == 6
    finally:
        bridge.close(); ed.close()


@pytest.mark.parametrize('mode', ['cli', 'mcp'])
def test_public_pending_targets_match_advertised_order(mode, tmp_path, monkeypatch):
    from omepreview.gui import Editor
    from omepreview.editor_session import Bridge
    source, _, env = fixtures(tmp_path)
    with pymupdf.open(source) as doc:
        doc[0].add_text_annot((60, 70), 'keep annotation zero')
        doc[0].add_text_annot((120, 70), 'delete annotation one')
        doc.saveIncr()
    original = source.read_bytes()
    monkeypatch.setenv('XDG_RUNTIME_DIR', env['XDG_RUNTIME_DIR'])
    ed = Editor(str(source), None)
    bridge = Bridge(ed, lambda cb: cb(), lambda: None, lambda: None)
    async def check():
        async with client(mode, env) as call:
            async def act(action, **kwargs):
                state = await call('editor', {'action': 'status', 'session': bridge.session})
                return await call('editor', {'action': action, 'session': bridge.session,
                                            'revision': state['revision'], **kwargs})
            await act('stage', ops=[{'op': 'delete_annotation', 'page': 1, 'index': 0}])
            state = await act('stage', ops=[{'op': 'delete_annotation', 'page': 1, 'index': 1}])
            assert [op['index'] for op in state['pending']] == [0, 1]
            assert [op['index'] for op in ed.to_ops()] == [1, 0]  # safe save order unchanged
            selected = await act('select', index=0)
            assert selected['selected_index'] == 0
            await act('delete', index=0)
            state = await call('editor', {'action': 'status', 'session': bridge.session})
            assert [op['index'] for op in state['pending']] == [1]
            assert source.read_bytes() == original
            await act('save', confirm=True)
            with pymupdf.open(source) as doc:
                assert [a.info['content'] for a in doc[0].annots()] == ['keep annotation zero']
                assert 'CONTROL' in doc[0].get_text()
    try:
        asyncio.run(check())
    finally:
        bridge.close(); ed.close()
