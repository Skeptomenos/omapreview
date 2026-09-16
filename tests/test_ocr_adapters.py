"""O3: installed CLI/stdio contracts, cancellation, and independent saved reads."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

import pymupdf as fitz
import pytest

from ocr_fixtures import add_scan, build, fingerprint, assert_ocr_report, SCAN

CLI = Path(sys.executable).with_name('omapreview')
MCP = Path(sys.executable).with_name('omapreview-mcp')


class Wire:
    """Actual installed stdio, including EOF and cancellation notifications."""
    def __init__(self, env, executable=None, args=()):
        self.process = subprocess.Popen([str(executable or MCP), *args], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.frames = []
        self.pending = b''
        self.serial = 0
        self.send({'id': 0, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'O3-acceptance', 'version': '1'}}})
        self.until(lambda: any(f.get('id') == 0 for f in self.frames))
        self.send({'method': 'notifications/initialized'})

    def send(self, frame):
        self.process.stdin.write((json.dumps({'jsonrpc': '2.0', **frame})+'\n').encode())
        self.process.stdin.flush()

    def poll(self):
        if self.selector.select(.025):
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                return
            self.pending += chunk
            while b'\n' in self.pending:
                line, self.pending = self.pending.split(b'\n', 1)
                self.frames.append(json.loads(line))

    def until(self, predicate, timeout=60):
        deadline = time.monotonic()+timeout
        while not predicate():
            assert time.monotonic() < deadline, self.frames[-5:]
            assert self.process.poll() is None, (self.process.returncode, self.process.stderr.read())
            self.poll()

    def start(self, name, arguments, token=None):
        self.serial += 1
        params = {'name': name, 'arguments': arguments}
        if token is not None:
            params['_meta'] = {'progressToken': token}
        self.send({'id': self.serial, 'method': 'tools/call', 'params': params})
        return self.serial

    def call(self, name, arguments, token=None):
        ident = self.start(name, arguments, token)
        self.until(lambda: any(f.get('id') == ident for f in self.frames))
        frame = next(f for f in self.frames if f.get('id') == ident)
        assert 'result' in frame, frame
        return frame['result']

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise
        finally:
            self.selector.close()
            self.process.stdout.close()
            self.process.stderr.close()


def data(result):
    return result.get('structuredContent') or json.loads(next(b['text'] for b in result['content'] if b['type'] == 'text'))


def cli(*args, env=None):
    return subprocess.run([str(CLI), *map(str, args)], capture_output=True, text=True, timeout=90, env=env)


@pytest.fixture
def real_env():
    backend = os.environ.get('OMAPREVIEW_TEST_OCRMYPDF')
    if not backend:
        pytest.skip('Set OMAPREVIEW_TEST_OCRMYPDF for installed real OCR adapter acceptance')
    return {**os.environ, 'OMAPREVIEW_OCRMYPDF': backend}


@pytest.fixture(scope='module')
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp('ocr-adapters') / 'fixtures'
    return root, build(root)


def test_editor_ocr_thin_route(monkeypatch):
    from omepreview import workflows, editor_session
    response = dict(task_id='ocr-test', status='running', phase='preflight', completed=0,
                    total=5, destination='/copy.pdf', output=None, result=None, error=None,
                    source_revision='r1', session_changed=False, cancellation_requested=False)
    calls = []
    monkeypatch.setattr(editor_session, 'request', lambda session, payload: calls.append((session, payload)) or response)
    op = {'op': 'ocr', 'expected_source_sha256': 'a'*64}
    assert workflows.run('editor', {'action': 'ocr_start', 'session': 's1', 'revision': 'r1',
                                   'confirm': True, 'op': op, 'output': '/copy.pdf'}) == response
    assert calls[-1] == ('s1', {'action': 'ocr_start', 'revision': 'r1', 'confirm': True, 'op': op, 'output': '/copy.pdf'})
    for action in ('ocr_status', 'ocr_cancel'):
        assert workflows.run('editor', {'action': action, 'session': 's1', 'task_id': 'ocr-test'}) == response
        assert calls[-1] == ('s1', {'action': action, 'task_id': 'ocr-test'})
    assert set(response) == {'task_id','status','phase','completed','total','destination','output','result','error','source_revision','session_changed','cancellation_requested'}


def test_missing_dependency_errors(corpus, tmp_path):
    env = {**os.environ, 'OMAPREVIEW_OCRMYPDF': str(tmp_path/'absent')}
    source = corpus[0]/'clean.pdf'
    target = tmp_path/'copy.pdf'
    status = cli('ocr-status', env=env)
    assert status.returncode == 0 and json.loads(status.stdout)['available'] is False
    result = cli('ocr', source, '-o', target, '--json', env=env)
    assert result.returncode == 1 and result.stdout == ''
    assert json.loads(result.stderr.splitlines()[-1])['error']['code'] == 'dependency_missing'
    wire = Wire(env)
    try:
        assert data(wire.call('ocr_status', {}))['available'] is False
        result = wire.call('apply_ops', {'path':str(source),'ops':[{'op':'ocr'}],'output':str(target)})
        assert result['isError']
        assert data(result)['error']['code'] == 'dependency_missing'
        assert data(result)['output'] is None and data(result)['applied'] == []
    finally:
        wire.close()
    assert not target.exists()


@pytest.mark.parametrize('surface', ['cli', 'mcp'])
def test_real_saved_journey(corpus, tmp_path, real_env, surface):
    source = corpus[0]/'selected.pdf'
    target = tmp_path/'copy.pdf'
    before = fingerprint(source)
    wire = Wire(real_env) if surface == 'mcp' else None
    try:
        if wire:
            assert data(wire.call('ocr_status', {}))['available']
            assert 'ocr' in {o['name'] for o in data(wire.call('operation_schema',{}))['operations']}
            def apply(ops, output, confirm=False):
                result = wire.call('apply_ops', {'path':str(source),'ops':ops,'output':str(output),'dry_run':not confirm}, token='journey')
                assert not result.get('isError'), result
                return data(result)
        else:
            assert json.loads(cli('ocr-status',env=real_env).stdout)['available']
            def apply(ops, output, confirm=False):
                payload = tmp_path/'ops.json';payload.write_text(json.dumps(ops))
                result = cli('apply',source,'--ops',payload,'-o',output,'--json',*(['--confirm'] if confirm else []),env=real_env)
                assert result.returncode == 0, result.stderr
                return json.loads(result.stdout)
        proposal = apply([{'op':'ocr','pages':[1]}],target)
        assert not target.exists() and proposal['output'] is None
        sha = proposal['applied'][0]['ocr']['source_sha256']
        result = apply([{'op':'ocr','pages':[1],'expected_source_sha256':sha}],target,True)
        assert_ocr_report(source,target,corpus[1]['selected'],result)
        if wire:
            observed = data(wire.call('read_pdf',{'path':str(target)}))
            rendered = wire.call('render_page',{'path':str(target),'page':1,'scale':1})
            png = base64.b64decode(next(c['data'] for c in rendered['content'] if c['type']=='image'))
            hits = data(wire.call('run_workflow',{'name':'search','arguments':{'path':str(target),'query':SCAN}}))['hits']
        else:
            observed = json.loads(cli('read',target,'--json',env=real_env).stdout)
            image = tmp_path/'saved.png'
            assert cli('snapshot',target,'--page','1','--scale','1','-o',image,env=real_env).returncode == 0
            png = image.read_bytes()
            hits = json.loads(cli('workflow','search','--args',json.dumps({'path':str(target),'query':SCAN}),env=real_env).stdout)['hits']
        assert SCAN in json.dumps(observed) and hits
        assert fitz.Pixmap(png).width == 595
        # A wrong expected phrase must not pass the independent read oracle.
        assert 'WRONG EXPECTED TRANSCRIPTION' not in json.dumps(observed)
        marked = tmp_path/'highlight.pdf';redacted=tmp_path/'redacted.pdf'
        rect = hits[0]['rect']
        # Review the scan pixels: OCR word bounds can exclude antialiased fringes.
        redact_rect = [rect[0]-2, rect[1]-2, rect[2]+2, rect[3]+2]
        for operation, dest in (({'op':'highlight','page':1,'rect':rect},marked),
                                ({'op':'redact','page':1,'rect':redact_rect},redacted)):
            if wire:
                saved=wire.call('apply_ops',{'path':str(target),'ops':[operation],'output':str(dest),'dry_run':False})
                assert not saved.get('isError'), saved
                assert data(wire.call('read_pdf',{'path':str(dest)}))
                assert any(c['type']=='image' for c in wire.call('render_page',{'path':str(dest),'page':1})['content'])
            else:
                payload=tmp_path/'post.json';payload.write_text(json.dumps([operation]))
                saved=cli('apply',target,'--ops',payload,'-o',dest,'--confirm','--json',env=real_env)
                assert saved.returncode==0,saved.stderr
                assert cli('read',dest,'--json',env=real_env).returncode==0
                assert cli('snapshot',dest,'-o',tmp_path/(dest.stem+'.png'),env=real_env).returncode==0
        with fitz.open(target) as original,fitz.open(marked) as highlight,fitz.open(redacted) as redaction:
            assert len(list(highlight[0].annots()))==1
            assert not redaction[0].search_for(SCAN)
            clip=fitz.Rect(redact_rect) + (1,1,-1,-1)
            after=redaction[0].get_pixmap(clip=clip,alpha=False)
            assert not any(after.samples), 'interior of redaction must render black'
            # Inspect the saved image itself: an overlay alone would retain text pixels.
            images=redaction[0].get_images()
            assert images
            assert all(min(fitz.Pixmap(redaction, im[0]).samples)==255 for im in images)
            assert original[0].get_pixmap(clip=fitz.Rect(0,350,595,700)).samples==redaction[0].get_pixmap(clip=fitz.Rect(0,350,595,700)).samples
            assert original[1].get_pixmap().samples==redaction[1].get_pixmap().samples
        assert fingerprint(source)==before
    finally:
        if wire:wire.close()


@pytest.mark.parametrize('surface',['cli','mcp'])
def test_real_confirmation_language_conflict(corpus,tmp_path,real_env,surface):
    source=tmp_path/'source.pdf';source.write_bytes((corpus[0]/'clean.pdf').read_bytes())
    target=tmp_path/'copy.pdf';sha=fingerprint(source)
    cases=[({'op':'ocr'},'confirmation_required'),
           ({'op':'ocr','languages':['not_installed'],'expected_source_sha256':sha},'language_missing'),
           ({'op':'ocr','expected_source_sha256':'0'*64},'source_conflict')]
    wire=Wire(real_env) if surface=='mcp' else None
    try:
        for op,code in cases:
            if wire:
                result=wire.call('apply_ops',{'path':str(source),'ops':[op],'output':str(target),'dry_run':False})
                assert result['isError'],result
                error=data(result)['error']
            else:
                payload=tmp_path/'ops.json';payload.write_text(json.dumps([op]))
                result=cli('apply',source,'--ops',payload,'-o',target,'--confirm','--json',env=real_env)
                assert result.returncode==1 and not result.stdout
                error=json.loads(result.stderr.splitlines()[-1])['error']
            assert error['code']==code,error
            assert not target.exists() and fingerprint(source)==sha
    finally:
        if wire:wire.close()


def descendants(pid):
    rows={}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            fields=(p/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0]!='Z':rows[int(p.name)]=(int(fields[1]),(p/'comm').read_text().strip(),int(fields[2]))
        except (OSError,ValueError,IndexError):pass
    found=set([pid]);changed=True
    while changed:
        extra={i for i,r in rows.items() if r[0] in found}-found
        changed=bool(extra);found|=extra
    return {i:rows[i] for i in found if i in rows and i!=pid}


@pytest.mark.parametrize('mode',['sigint','sigterm','cancel','eof','cancel-no-token'])
def test_real_cancel_cleanup(tmp_path,real_env,mode):
    source=tmp_path/'source.pdf';target=tmp_path/'copy.pdf'
    with fitz.open() as doc:
        for _ in range(18):add_scan(doc)
        doc.save(source,deflate=True)
    sha=fingerprint(source)
    op={'op':'ocr','expected_source_sha256':sha}
    wire=None
    if mode.startswith('sig'):
        process=subprocess.Popen([str(CLI),'ocr',str(source),'-o',str(target),'--confirm','--expected-source-sha256',sha,'--json'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=real_env)
    else:
        wire=Wire(real_env);process=wire.process
        ident=wire.start('apply_ops',{'path':str(source),'ops':[op],'output':str(target),'dry_run':False}, token=None if mode=='cancel-no-token' else 0)
    try:
        deadline=time.monotonic()+30
        observed={}
        while time.monotonic()<deadline:
            observed=descendants(process.pid)
            if any(row[1]=='tesseract' for row in observed.values()):break
            if wire:wire.poll()
            else:time.sleep(.025)
        assert any(row[1]=='tesseract' for row in observed.values()),observed
        started=time.monotonic()
        if mode.startswith('sig'):
            process.send_signal(signal.SIGINT if mode=='sigint' else signal.SIGTERM)
            stdout,stderr=process.communicate(timeout=15)
            assert process.returncode==130 and stdout==b'',stderr
            assert json.loads(stderr.splitlines()[-1])['error']['code']=='cancelled'
        elif mode=='eof':
            process.stdin.close();process.wait(timeout=15)
        else:
            wire.send({'method':'notifications/cancelled','params':{'requestId':ident,'reason':'synthetic test'}})
            # Prove the same server remains responsive after owned cleanup.
            wire.call('operation_schema',{})
        until=time.monotonic()+12
        while list(tmp_path.glob('.omapreview-ocr-*')) and time.monotonic()<until:
            if wire and process.poll() is None:wire.poll()
            else:time.sleep(.025)
        assert not list(tmp_path.glob('.omapreview-ocr-*'))
        assert not target.exists() and fingerprint(source)==sha
        for pid in observed:
            stat=Path(f'/proc/{pid}/stat')
            if stat.exists():assert stat.read_text().rsplit(')',1)[1].split()[0]=='Z'
        assert time.monotonic()-started<15
        if wire:
            progress=[f for f in wire.frames if f.get('method')=='notifications/progress']
            assert bool(progress)==(mode!='cancel-no-token')
            if progress:assert all(f['params']['progressToken']==0 for f in progress)
    finally:
        if wire:wire.close()
        elif process.poll() is None:
            process.terminate();process.communicate(timeout=15)


@pytest.mark.parametrize('surface',['cli','mcp'])
@pytest.mark.parametrize('name',['geometry','mixed_page','photo','poor'])
def test_adapter_corpus_outcomes(corpus,tmp_path,real_env,surface,name):
    source=corpus[0]/(name+'.pdf');target=tmp_path/'copy.pdf'
    op={'op':'ocr','expected_source_sha256':fingerprint(source)}
    wire=Wire(real_env) if surface=='mcp' else None
    try:
        if wire:
            result=wire.call('apply_ops',{'path':str(source),'ops':[op],'output':str(target),'dry_run':False},token='corpus')
            assert not result.get('isError'),result
            result=data(result)
            saved_read=data(wire.call('read_pdf',{'path':str(target)}))
            for page in range(1,len(corpus[1][name]['pages'])+1):
                rendered=wire.call('render_page',{'path':str(target),'page':page,'scale':1})
                png=base64.b64decode(next(c['data'] for c in rendered['content'] if c['type']=='image'))
                (tmp_path/f'page-{page}.png').write_bytes(png)
            phases=[f['params']['progress'] for f in wire.frames if f.get('method')=='notifications/progress']
            assert phases==sorted(phases) and phases[-1]==5
            (tmp_path/'wire.json').write_text(json.dumps(wire.frames,indent=2))
        else:
            result=cli('ocr',source,'-o',target,'--confirm','--expected-source-sha256',op['expected_source_sha256'],'--json',env=real_env)
            assert result.returncode==0,result.stderr
            (tmp_path/'progress.jsonl').write_text(result.stderr)
            result=json.loads(result.stdout)
            saved_read=json.loads(cli('read',target,'--json',env=real_env).stdout)
            for page in range(1,len(corpus[1][name]['pages'])+1):
                assert cli('snapshot',target,'--page',str(page),'--scale','1','-o',tmp_path/f'page-{page}.png',env=real_env).returncode==0
        assert_ocr_report(source,target,corpus[1][name],result)
        (tmp_path/'report.json').write_text(json.dumps(result,indent=2))
        (tmp_path/'read.json').write_text(json.dumps(saved_read,indent=2))
        pages=result['applied'][0]['ocr']['pages']
        if name=='photo':assert pages[0]['status']=='needs_review'
        if name=='poor':assert pages[0]['review_required'] and SCAN not in json.dumps(saved_read)
        if name=='mixed_page':assert pages[0]['status']=='skipped' and pages[0]['scanned_regions_not_processed']
    finally:
        if wire:wire.close()


@pytest.mark.parametrize('surface',['cli','mcp'])
def test_installed_deadline_cleanup(tmp_path,real_env,surface):
    source=tmp_path/'source.pdf';target=tmp_path/'copy.pdf'
    with fitz.open() as doc:
        for _ in range(18):add_scan(doc)
        doc.save(source,deflate=True)
    sha=fingerprint(source)
    if surface=='cli':
        result=cli('ocr',source,'-o',target,'--confirm','--expected-source-sha256',sha,
                   '--timeout-seconds','3','--json',env=real_env)
        assert result.returncode==1 and result.stdout==''
        result=json.loads(result.stderr.splitlines()[-1])
    else:
        wire=Wire(real_env)
        try:
            result=wire.call('apply_ops',{'path':str(source),'ops':[{'op':'ocr',
                'expected_source_sha256':sha,'timeout_seconds':3}],
                'output':str(target),'dry_run':False})
            assert result['isError'],result
            result=data(result)
        finally:wire.close()
    assert result['error']['code']=='worker_timeout'
    assert result['error']['ocr']['status']=='timed_out'
    assert result['output'] is None and not target.exists()
    assert fingerprint(source)==sha and not list(tmp_path.glob('.omapreview-ocr-*'))


def test_ordinary_apply_does_not_block_stdio_dispatch(tmp_path):
    """A slow ordinary engine call must not hold up discovery/cancel dispatch."""
    source=tmp_path/'source.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((50,50),'SYNTHETIC CONTROL')
        doc.save(source)
    started=tmp_path/'started';release=tmp_path/'release'
    script='''
import sys,time
from pathlib import Path
from omepreview import mcp_server
original=mcp_server.engine.apply
started,release=map(Path,sys.argv[1:])
def slow(*args,**kwargs):
    started.write_text('started')
    deadline=time.monotonic()+10
    while not release.exists() and time.monotonic()<deadline:
        time.sleep(.01)
    return original(*args,**kwargs)
mcp_server.engine.apply=slow
mcp_server.main()
'''
    wire=Wire(dict(os.environ),sys.executable,('-c',script,str(started),str(release)))
    try:
        ordinary=wire.start('apply_ops',{'path':str(source),'ops':[{'op':'rotate_pages','pages':[1],'degrees':90}]})
        wire.until(started.exists,timeout=5)
        discovery=wire.start('operation_schema',{})
        wire.until(lambda:any(f.get('id')==discovery for f in wire.frames),timeout=3)
        assert not any(f.get('id')==ordinary for f in wire.frames)
        release.touch()
        wire.until(lambda:any(f.get('id')==ordinary for f in wire.frames))
        result=next(f['result'] for f in wire.frames if f.get('id')==ordinary)
        assert not result.get('isError') and data(result)['output'] is None
        with fitz.open(source) as doc:assert doc[0].rotation==0
    finally:
        release.touch()
        wire.close()
