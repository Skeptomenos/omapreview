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


@pytest.mark.parametrize('existing', [False, True])
def test_recorder_refuses_a_signature_changed_while_drawing(tmp_path, monkeypatch, existing):
    from omepreview import handoff, signature, draw, editor_session
    source, candidate, env = fixtures(tmp_path)
    monkeypatch.setenv('OMEPREVIEW_SIGNATURE_DIR', env['OMEPREVIEW_SIGNATURE_DIR'])
    monkeypatch.setenv('XDG_RUNTIME_DIR', env['XDG_RUNTIME_DIR'])
    monkeypatch.setattr(editor_session, 'desktop_ready', lambda: None)
    monkeypatch.setattr(handoff.subprocess, 'Popen', lambda *a, **kw: type('P', (), {'pid': 99999999})())
    if existing:
        signature.add(candidate, 'race')
    started = handoff.start_recording('race', True, confirm=existing)
    concurrent = tmp_path / 'concurrent.svg'
    concurrent.write_text(candidate.read_text().replace('M1 20 L50 5', 'M2 20 L40 8'))
    def record(path, **kw):
        signature.add(concurrent, 'race')
        Path(path).write_bytes(candidate.read_bytes())
        return 0
    monkeypatch.setattr(draw, '_gtk_main', record)
    handoff.main(started['job'])
    status = handoff.recording_status(started['job'])
    assert status['status'] == 'failed' and 'changed since review' in status['error']
    assert signature.get('race').read_bytes() == concurrent.read_bytes()


def test_recorder_startup_death_and_fast_child_state(tmp_path, monkeypatch):
    from omepreview import handoff, editor_session
    _, _, env = fixtures(tmp_path)
    monkeypatch.setenv('OMEPREVIEW_SIGNATURE_DIR', env['OMEPREVIEW_SIGNATURE_DIR'])
    monkeypatch.setenv('XDG_RUNTIME_DIR', env['XDG_RUNTIME_DIR'])
    monkeypatch.setattr(editor_session, 'desktop_ready', lambda: None)
    monkeypatch.setattr(handoff.subprocess, 'Popen', lambda *a, **kw: type('P', (), {'pid': 99999999})())
    started = handoff.start_recording('startup', True)
    assert handoff.recording_status(started['job'])['status'] == 'failed'
    def fast_child(argv, **kw):
        directory = handoff._directory(argv[-1])
        status = json.loads((directory / 'status.json').read_text())
        status.update(status='cancelled', saved=False)
        handoff._write(directory, status)
        return type('P', (), {'pid': 99999999})()
    monkeypatch.setattr(handoff.subprocess, 'Popen', fast_child)
    fast = handoff.start_recording('fast', True)
    assert handoff.recording_status(fast['job'])['status'] == 'cancelled'


@pytest.mark.parametrize('mode', ['cli', 'mcp'])
def test_public_crop_rebases_pending_targets_and_rolls_back_failure(mode, tmp_path, monkeypatch):
    from omepreview.gui import Editor
    from omepreview.editor_session import Bridge
    source, _, env = fixtures(tmp_path)
    source.unlink()
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=200)
        page.insert_text((80, 65), 'SECRET')
        page.insert_text((80, 160), 'CONTROL')
        widget = pymupdf.Widget(); widget.field_name = 'field'
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(80, 80, 160, 100); page.add_widget(widget)
        doc.save(source)
    original = source.read_bytes()
    monkeypatch.setenv('XDG_RUNTIME_DIR', env['XDG_RUNTIME_DIR'])
    ed = Editor(str(source), None)
    bridge = Bridge(ed, lambda cb: cb(), lambda: None, lambda: None)
    async def check():
        async with client(mode, env) as call:
            async def state():
                return await call('editor', {'action': 'status', 'session': bridge.session})
            async def act(action, **kwargs):
                s = await state()
                return await call('editor', {'action': action, 'session': bridge.session, 'revision': s['revision'], **kwargs})
            await act('stage', ops=[
                {'op': 'fill_field', 'field': 'field', 'value': 'filled', 'page': 1, 'rect': [80,80,160,100]},
                {'op': 'note', 'page': 1, 'at': [200,120], 'text': 'target note'},
                {'op': 'text_box', 'page': 1, 'rect': [170,85,270,105], 'text': 'textbox', 'size': 10},
                {'op': 'redact', 'page': 1, 'match': 'SECRET'},
            ])
            before = await state()
            await call('editor', {'action': 'stage', 'session': bridge.session, 'revision': before['revision'],
                                  'ops': [{'op': 'crop_pages', 'pages': [1], 'rect': [400,400,500,500]}]}, error=True)
            after = await state()
            assert after == before
            assert source.read_bytes() == original
            cropped = await act('stage', ops=[{'op':'crop_pages','pages':[1],'rect':[40,40,280,180]}])
            assert cropped['pending'][0]['rect'] == [40,40,120,60]
            assert cropped['pending'][1]['at'] == [160,80]
            assert cropped['pending'][2]['rect'] == [130,45,230,65]
            saved = await act('save', confirm=True)
            with pymupdf.open(saved['path']) as doc:
                assert next(doc[0].widgets()).field_value == 'filled'
                assert 'SECRET' not in doc[0].get_text() and 'CONTROL' in doc[0].get_text()
                assert 'textbox' in doc[0].get_text()
                assert any(a.info['content'] == 'target note' for a in doc[0].annots())
            assert source.read_bytes() == original  # redaction copy-save preserves source
    try:
        asyncio.run(check())
    finally:
        bridge.close(); ed.close()


@pytest.mark.parametrize('mode', ['cli', 'mcp'])
def test_public_replace_preserves_position_and_history(mode, tmp_path, monkeypatch):
    from omepreview.gui import Editor
    from omepreview.editor_session import Bridge
    source, _, env = fixtures(tmp_path)
    with pymupdf.open(source) as doc:
        w = pymupdf.Widget(); w.field_name = 'repeat'
        w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT; w.rect = pymupdf.Rect(20,80,160,100)
        doc[0].add_widget(w); doc.saveIncr()
    monkeypatch.setenv('XDG_RUNTIME_DIR', env['XDG_RUNTIME_DIR'])
    ed = Editor(str(source), None)
    bridge = Bridge(ed, lambda cb: cb(), lambda: None, lambda: None)
    async def check():
        async with client(mode, env) as call:
            async def act(action, **kw):
                s = await call('editor', {'action':'status','session':bridge.session})
                return await call('editor', {'action':action,'session':bridge.session,'revision':s['revision'],**kw})
            def fill(value): return {'op':'fill_field','field':'repeat','value':value,'page':1}
            await act('stage', ops=[fill('FIRST'), fill('LAST')])
            changed = await act('replace', index=0, ops=[fill('FIRST EDITED')])
            assert [p['value'] for p in changed['pending']] == ['FIRST EDITED','LAST']
            reverted = await act('undo', confirm=True)
            assert [p['value'] for p in reverted['pending']] == ['FIRST','LAST']
            await act('redo', confirm=True)
            await act('save', confirm=True)
            with pymupdf.open(source) as doc:
                assert next(doc[0].widgets()).field_value == 'LAST'
            await act('stage', ops=[{'op':'note','page':1,'at':[40,120],'text':'replace me'},
                                    {'op':'note','page':1,'at':[80,120],'text':'last note'}])
            replaced = await act('replace', index=0, ops=[{'op':'highlight','page':1,'match':'TARGET'}])
            assert replaced['pending'][0]['op']=='highlight' and replaced['pending'][-1]['text']=='last note'
    try:
        asyncio.run(check())
    finally:
        bridge.close(); ed.close()


def test_flatten_export_uses_the_fingerprinted_snapshot(tmp_path, monkeypatch):
    source, _, _ = fixtures(tmp_path)
    original = source.read_bytes()
    replacement = tmp_path / 'replacement.pdf'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72,72),'CONCURRENT REPLACEMENT')
        doc.save(replacement)
    real_read = workflows.pdf_bytes
    def capture_then_replace(path):
        snapshot = real_read(path)
        os.replace(replacement, source)
        return snapshot
    monkeypatch.setattr(workflows, 'pdf_bytes', capture_then_replace)
    out = tmp_path / 'flattened.pdf'
    result = workflows.run('export', {'path':str(source),'action':'flatten','output':str(out),'confirm':True})
    assert result['source_fingerprint']['value'] == hashlib.sha256(original).hexdigest()
    with pymupdf.open(out) as doc:
        assert len(doc) == 3 and 'TARGET 0 CONTROL' in doc[0].get_text()
        assert 'CONCURRENT' not in doc[0].get_text()
    with pymupdf.open(source) as doc:
        assert 'CONCURRENT REPLACEMENT' in doc[0].get_text()
