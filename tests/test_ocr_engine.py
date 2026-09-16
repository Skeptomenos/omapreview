"""O1 core acceptance: synthetic fixtures, real backend, hostile worker outputs.

Set OMAPREVIEW_TEST_OCRMYPDF to run the real corpus. No optional import in core.
The separate test selection permits installs without OCR to exercise refusals.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pymupdf as fitz
import pytest

from omepreview import ocr
from omepreview.engine import apply
from omepreview.ops import OCRError, operation_catalog, validate
from ocr_fixtures import build, assert_ocr_report, fingerprint, add_scan


@pytest.fixture(scope='module')
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp('ocr-engine')
    return root, build(root / 'fixtures')


@pytest.fixture
def fake_backend(monkeypatch):
    monkeypatch.setattr(ocr, 'capabilities', lambda **kw: {
        'available': True, 'languages': ['eng', 'deu', 'script/Latin'],
        'executable': '/synthetic/backend', 'code': None, 'action': None})


def request(source, output, **extra):
    return apply(source, [{'op': 'ocr', 'expected_source_sha256': fingerprint(source), **extra}],
                 output=output, dry_run=False)


def failure(code, call, output=None):
    with pytest.raises(OCRError) as info:
        call()
    assert info.value.code == code
    if info.value.ocr:
        assert info.value.ocr['output_sha256'] is None
        assert info.value.ocr['status'] in ('failed', 'cancelled', 'timed_out')
    if output is not None:
        assert not output.exists()
        assert not list(output.parent.glob('.omapreview-ocr-*'))
    return info.value


@pytest.mark.parametrize('name', ['clean', 'native', 'mixed_page', 'geometry', 'sideways_pixels', 'blank', 'photo', 'poor', 'selected', 'existing_ocr'])
def test_real_corpus(corpus, tmp_path, monkeypatch, name):
    backend = os.environ.get('OMAPREVIEW_TEST_OCRMYPDF')
    if not backend:
        pytest.skip('Set OMAPREVIEW_TEST_OCRMYPDF for real OCR acceptance')
    monkeypatch.setenv('OMAPREVIEW_OCRMYPDF', backend)
    root, cases = corpus
    case = cases[name]
    source, target = root / 'fixtures' / case['file'], tmp_path / 'copy.pdf'
    options = {'pages': case['selected']} if 'selected' in case else {}
    events = []
    proposal = apply(source, [{'op': 'ocr', **options}], output=target, dry_run=True, progress=events.append)
    assert proposal['output'] is None and not target.exists()
    assert not list(tmp_path.iterdir())
    assert all(p['status'] != 'processed' and p['text_after_chars'] is None for p in proposal['applied'][0]['ocr']['pages'])
    result = apply(source, [{'op': 'ocr', **options, 'expected_source_sha256': proposal['applied'][0]['ocr']['source_sha256']}], output=target, dry_run=False, progress=events.append)
    observations = assert_ocr_report(source, target, case, result)
    assert target.stat().st_mode & 0o777 == 0o600
    assert [x['phase'] for x in events] == ['preflight', *ocr.PHASES]
    evidence = os.environ.get('OMAPREVIEW_OCR_EVIDENCE')
    if evidence:
        folder = Path(evidence) / name
        folder.mkdir(parents=True, exist_ok=False)
        shutil.copy2(source, folder / 'source.pdf')
        shutil.copy2(target, folder / 'output.pdf')
        (folder / 'report.json').write_text(json.dumps(result, indent=2))
        (folder / 'observations.json').write_text(json.dumps(observations, indent=2))
        (folder / 'case.json').write_text(json.dumps(case, indent=2))


@pytest.mark.parametrize('name', ['comment', 'widget', 'link', 'embedded', 'redaction', 'signature_structure', 'unsigned_signature', 'encrypted', 'owner_encrypted'])
def test_refusal_corpus(corpus, tmp_path, fake_backend, name):
    root, cases = corpus
    case = cases[name]
    source, target = root / 'fixtures' / case['file'], tmp_path / 'copy.pdf'
    failure(case['refusal'], lambda: apply(source, [{'op': 'ocr'}], output=target, dry_run=True), target)
    failure(case['refusal'], lambda: request(source, target), target)
    assert fingerprint(source) == case['sha256']


@pytest.mark.parametrize('options', [
    {'pages': []}, {'pages': [True]}, {'pages': [0]}, {'pages': [-1]}, {'pages': [1, 1]},
    {'pages': ['1']}, {'pages': [1.0]}, {'languages': []}, {'languages': ['eng', 'eng']},
    {'languages': ['../eng']}, {'languages': ['osd']}, {'languages': [True]}, {'languages': ''},
    {'languages': ['eng+deu']}, {'force': True}, {'timeout_seconds': True},
    {'timeout_seconds': 0}, {'timeout_seconds': 3601}, {'expected_source_sha256': 'x'}])
def test_invalid_schema(options):
    failure('invalid_request', lambda: validate({'op': 'ocr', **options}))


def test_catalog_and_absence(monkeypatch, corpus, tmp_path):
    monkeypatch.setenv('OMAPREVIEW_OCRMYPDF', str(tmp_path / 'absent'))
    assert next(op for op in operation_catalog()['operations'] if op['name'] == 'ocr')
    status = ocr.capabilities()
    assert not status['available'] and status['code'] == 'dependency_missing'
    failure('dependency_missing', lambda: apply(corpus[0] / 'fixtures/clean.pdf', [{'op': 'ocr'}], output=tmp_path/'out.pdf', dry_run=True))
    assert 'ocrmypdf' not in sys.modules


def test_no_staging_preflight(monkeypatch, fake_backend, corpus, tmp_path):
    monkeypatch.setattr(ocr.tempfile, 'TemporaryDirectory', lambda **kw: pytest.fail('preflight created staging'))
    monkeypatch.setattr(ocr, '_run_worker', lambda *a: pytest.fail('preflight ran OCR'))
    result = apply(corpus[0]/'fixtures/selected.pdf', [{'op':'ocr', 'pages':[3,1], 'languages':['deu','eng']}], output=tmp_path/'out.pdf', dry_run=True)
    op = result['applied'][0]
    assert op['pages'] == [1,3] and op['languages'] == ['deu','eng']
    assert not list(tmp_path.iterdir())


def test_request_errors(corpus, tmp_path, fake_backend):
    source = corpus[0]/'fixtures/clean.pdf'
    target = tmp_path/'out.pdf'
    failure('invalid_request', lambda: apply(source, [{'op':'ocr'}, {'op':'rotate_pages','pages':[1],'degrees':90}], output=target, dry_run=True))
    failure('confirmation_required', lambda: apply(source, [{'op':'ocr'}], output=target, dry_run=False))
    failure('source_conflict', lambda: request(source, target, expected_source_sha256='0'*64))
    failure('invalid_request', lambda: request(source, target, pages=[2]))
    failure('language_missing', lambda: request(source, target, languages=['zzz']))
    failure('destination_conflict', lambda: request(source, None))
    failure('destination_conflict', lambda: request(source, source))


@pytest.mark.parametrize('kind', ['file','hardlink','symlink','dangling','directory'])
def test_destinations(corpus, tmp_path, fake_backend, kind):
    source = corpus[0]/'fixtures/clean.pdf'
    target = tmp_path/'out.pdf'
    if kind == 'file': target.write_bytes(b'SENTINEL')
    elif kind == 'hardlink': os.link(source, target)
    elif kind == 'symlink': target.symlink_to(source)
    elif kind == 'dangling': target.symlink_to(tmp_path/'missing')
    else: target.mkdir()
    before = target.read_bytes() if target.is_file() else None
    failure('destination_conflict', lambda: request(source, target))
    assert os.path.lexists(target)
    if before is not None: assert target.read_bytes() == before


@pytest.fixture
def passthrough(monkeypatch, fake_backend):
    def copy(_deps, source, output, _op, _directory, _control, _metrics):
        shutil.copyfile(source, output)
    monkeypatch.setattr(ocr, '_run_worker', copy)


@pytest.mark.parametrize('damage', ['empty','bad_pdf','count','geometry','render','text','bounds','outline','labels'])
def test_untrusted_worker(corpus, tmp_path, monkeypatch, fake_backend, damage):
    source = corpus[0]/'fixtures/native.pdf'
    target = tmp_path/'out.pdf'
    def corrupt(_deps, staged, output, *args):
        if damage == 'empty': output.write_bytes(b''); return
        if damage == 'bad_pdf': output.write_bytes(b'not a pdf'); return
        with fitz.open(staged) as doc:
            if damage == 'count': doc.new_page()
            elif damage == 'geometry': doc[0].set_cropbox(fitz.Rect(0,0,500,700))
            elif damage == 'render': doc[0].draw_rect(fitz.Rect(10,10,20,20),fill=(0,0,0))
            elif damage == 'text': doc[0].insert_text((20,20),'hidden damage',render_mode=3)
            elif damage == 'bounds': doc[0].insert_text((-5,30),'OUT',render_mode=3)
            elif damage == 'outline': doc.set_toc([[1,'Unexpected',1]])
            elif damage == 'labels': doc.set_page_labels([{'startpage':0,'prefix':'BAD'}])
            doc.save(output)
    monkeypatch.setattr(ocr,'_run_worker',corrupt)
    failure('verification_failed', lambda: request(source,target),target)


@pytest.mark.parametrize('race', ['source_bytes','source_inode','symlink','destination','publication','cancel','late_cancel'])
def test_conflicts(corpus, tmp_path, monkeypatch, passthrough, race):
    source=tmp_path/'source.pdf';shutil.copyfile(corpus[0]/'fixtures/native.pdf',source)
    target=tmp_path/'copy.pdf'; before=fingerprint(source)
    event=threading.Event()
    if race == 'symlink':
        real=source;source=tmp_path/'link.pdf';source.symlink_to(real)
    real_link=os.link
    def publish(a,b,**kwargs):
        if race == 'destination': target.write_bytes(b'SENTINEL')
        if race == 'publication': raise PermissionError('synthetic denied')
        real_link(a,b,**kwargs)
        if race == 'late_cancel': event.set()
    monkeypatch.setattr(ocr.os,'link',publish)
    def progress(p):
        if p['phase'] != 'publishing':return
        if race=='source_bytes': source.write_bytes(source.read_bytes()+b'\nchanged')
        if race=='source_inode':
            other=tmp_path/'other.pdf';shutil.copyfile(source,other);os.replace(other,source)
        if race=='symlink':
            other=tmp_path/'other.pdf';shutil.copyfile(source,other);source.unlink();source.symlink_to(other)
        if race=='cancel':event.set()
    call=lambda: apply(source,[{'op':'ocr','expected_source_sha256':before}],output=target,dry_run=False,progress=progress,cancel_event=event)
    if race=='late_cancel':
        result=call();assert result['output']==str(target) and result['applied'][0]['ocr']['status']=='complete'
    else:
        code={'destination':'destination_conflict','publication':'publication_failed','cancel':'cancelled'}.get(race,'source_conflict')
        failure(code,call)
        if race=='destination': assert target.read_bytes()==b'SENTINEL'
        else: assert not target.exists()
    assert not list(tmp_path.glob('.omapreview-ocr-*'))


@pytest.mark.parametrize('kind', ['userunit','rotation','origin','active','tagged','xfa','nested_signature','escaped_signature'])
def test_structures(tmp_path, fake_backend, kind):
    source=tmp_path/'source.pdf';target=tmp_path/'copy.pdf'
    with fitz.open() as doc:
        page=doc.new_page()
        if kind=='userunit':doc.xref_set_key(page.xref,'UserUnit','2')
        elif kind=='rotation':doc.xref_set_key(page.xref,'Rotate','45')
        elif kind=='origin':doc.xref_set_key(page.xref,'MediaBox','[10 10 600 800]')
        elif kind=='active':doc.xref_set_key(doc.pdf_catalog(),'OpenAction','<< /S /JavaScript /JS (synthetic) >>')
        elif kind=='tagged':doc.xref_set_key(doc.pdf_catalog(),'StructTreeRoot','<< /Type /StructTreeRoot >>')
        elif kind=='xfa':doc.xref_set_key(doc.pdf_catalog(),'AcroForm','<< /XFA (synthetic) >>')
        elif kind=='nested_signature':doc.xref_set_key(doc.pdf_catalog(),'Extra','<< /Deep << /FT /Sig >> >>')
        elif kind=='escaped_signature':doc.xref_set_key(doc.pdf_catalog(),'Extra','<< /FT /S#69g >>')
        doc.save(source, use_objstms=1)
    code='unsupported_geometry' if kind in ('userunit','rotation','origin') else 'signature_structure' if 'signature' in kind else 'unsupported_content'
    failure(code,lambda:request(source,target),target)


def test_name_lexer_ignores_text(tmp_path,fake_backend,passthrough):
    source=tmp_path/'source.pdf';target=tmp_path/'copy.pdf'
    with fitz.open() as doc:
        doc.new_page().insert_text((30,30),'/Sig /ByteRange /JavaScript')
        doc.set_metadata({'title':'/Sig (nested) /ByteRange'})
        doc.save(source)
    assert request(source,target)['applied'][0]['ocr']['status']=='complete'


def test_worker_cancel_timeout_cleanup(tmp_path,monkeypatch):
    # Execute a real process tree through the same shim, without OCR dependencies.
    backend=tmp_path/'backend';pidfile=tmp_path/'pids'
    backend.write_text('#!'+sys.executable+'\nimport os,signal,subprocess,sys,time\nchild=subprocess.Popen([sys.executable,"-c","import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"])\nopen('+repr(str(pidfile))+',"w").write(str(os.getpid())+" "+str(child.pid))\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(60)\n')
    backend.chmod(0o700)
    for cancelled in (True,False):
        directory=tmp_path/str(cancelled);directory.mkdir()
        source=directory/'in.pdf';source.write_bytes(b'synthetic')
        target=directory/'out.pdf'
        event=threading.Event();control=ocr._Control(1,event,None)
        timer=threading.Timer(.3,event.set) if cancelled else None
        if timer:timer.start()
        started=time.monotonic()
        failure('cancelled' if cancelled else 'worker_timeout',lambda:ocr._run_worker({'executable':str(backend)},source,target,{'timeout_seconds':60,'languages':['eng'],'pages':[1]},directory,control,{'peak_temp_bytes_observed':0}))
        assert time.monotonic()-started < 6
        pids=list(map(int,pidfile.read_text().split()))
        assert not ocr._group_alive(pids[0])
        for pid in pids:
            statfile=Path('/proc')/str(pid)/'stat'
            assert not statfile.exists() or statfile.read_text().rsplit(')',1)[1].split()[0]=='Z'


@pytest.mark.parametrize('exit_code', [0, 3])
def test_real_backend_missing_output(tmp_path, exit_code):
    backend=tmp_path/'backend';backend.write_text(f'#!/bin/sh\nexit {exit_code}\n');backend.chmod(0o700)
    failure('worker_failed',lambda:ocr._run_worker({'executable':str(backend)},tmp_path/'in.pdf',tmp_path/'out.pdf',{'timeout_seconds':10,'languages':['eng'],'pages':[1]},tmp_path,ocr._Control(10,None,None),{'peak_temp_bytes_observed':0}))


@pytest.mark.parametrize('key,value', [('MediaBox','[0 0 0 0]'),('CropBox','(oops)'),('TrimBox','[0 0 10]'),('BleedBox','[2 0 1 2]')])
def test_malformed_boxes(tmp_path,fake_backend,key,value):
    source=tmp_path/'source.pdf'
    with fitz.open() as doc:
        page=doc.new_page();doc.xref_set_key(page.xref,key,value);doc.save(source)
    failure('unsupported_geometry',lambda:request(source,tmp_path/'out.pdf'),tmp_path/'out.pdf')


def test_cancelled_discovery(monkeypatch,tmp_path):
    backend=tmp_path/'backend';backend.write_text('#!'+sys.executable+'\nimport time;time.sleep(10)\n');backend.chmod(0o700)
    monkeypatch.setenv('OMAPREVIEW_OCRMYPDF',str(backend))
    event=threading.Event();event.set()
    failure('cancelled',lambda:ocr.capabilities(cancel_event=event))
    control=ocr._Control(0,None,None)
    failure('worker_timeout',lambda:ocr.capabilities(_control=control))


def test_unresolvable_launcher(monkeypatch,tmp_path):
    backend=tmp_path/'backend';backend.write_text('#!/usr/bin/env python\n');backend.chmod(0o700)
    monkeypatch.setenv('OMAPREVIEW_OCRMYPDF',str(backend))
    result=ocr.capabilities()
    assert result['code']=='dependency_unsupported' and not result['available']


def test_cleanup_after_commit(monkeypatch,tmp_path,corpus,passthrough):
    original=ocr.tempfile.TemporaryDirectory
    class BrokenCleanup(original):
        def __exit__(self,*args):
            super().__exit__(*args)
            raise PermissionError('synthetic cleanup failure')
    monkeypatch.setattr(ocr.tempfile,'TemporaryDirectory',BrokenCleanup)
    source=corpus[0]/'fixtures/native.pdf';target=tmp_path/'out.pdf'
    result=request(source,target)
    assert result['applied'][0]['ocr']['status']=='complete'
    assert result['applied'][0]['ocr']['output_sha256']==fingerprint(target)
    assert any('cleanup failed' in w for w in result['applied'][0]['ocr']['warnings'])


def test_explicit_execution_required(corpus,tmp_path,fake_backend):
    source=corpus[0]/'fixtures/clean.pdf'
    failure('confirmation_required',lambda:apply(source,[{'op':'ocr','expected_source_sha256':fingerprint(source)}],output=tmp_path/'out.pdf'))


def test_resource_bounds(corpus,tmp_path,monkeypatch,passthrough):
    source=corpus[0]/'fixtures/clean.pdf';target=tmp_path/'out.pdf'
    with monkeypatch.context() as patch:
        patch.setattr(ocr,'MAX_INPUT',1)
        failure('resource_limit',lambda:request(source,target),target)
    with monkeypatch.context() as patch:
        patch.setattr(ocr,'MAX_PAGES',0)
        failure('resource_limit',lambda:request(source,target),target)
    with monkeypatch.context() as patch:
        patch.setattr(ocr,'MAX_PIXELS',1)
        failure('resource_limit',lambda:request(source,target),target)
    with monkeypatch.context() as patch:
        patch.setattr(ocr,'MAX_TEMP',1)
        failure('resource_limit',lambda:request(source,target),target)


def test_backend_versions_and_language_discovery(monkeypatch,tmp_path):
    backend=tmp_path/'backend';backend.write_text('#!'+sys.executable+'\n');backend.chmod(0o700)
    monkeypatch.setenv('OMAPREVIEW_OCRMYPDF',str(backend))
    def query(cmd,control):
        if cmd[-1]=='--version':return '16.0.0'
        return '16.0.0'
    monkeypatch.setattr(ocr,'_query',query)
    assert ocr.capabilities()['code']=='dependency_unsupported'


def test_no_new_text_is_review(corpus,tmp_path,passthrough):
    result=request(corpus[0]/'fixtures/clean.pdf',tmp_path/'out.pdf')
    report=result['applied'][0]['ocr']
    assert report['status']=='needs_review' and report['recognized_pages']==0
    assert report['pages'][0]['reason']=='no_text'


@pytest.mark.parametrize('surface', ['cli','mcp'])
@pytest.mark.parametrize('enabled', [False,True])
def test_existing_protocol_routes(corpus,tmp_path,monkeypatch,surface,enabled):
    backend=os.environ.get('OMAPREVIEW_TEST_OCRMYPDF')
    if enabled and not backend:
        pytest.skip('Set OMAPREVIEW_TEST_OCRMYPDF for real protocol OCR acceptance')
    monkeypatch.setenv('OMAPREVIEW_OCRMYPDF',backend if enabled else str(tmp_path/'absent'))
    source=corpus[0]/'fixtures/clean.pdf';target=tmp_path/'copy.pdf'
    if surface=='cli':
        cli=Path(sys.executable).with_name('omapreview')
        def run(*args):
            return subprocess.run([str(cli),*map(str,args)],capture_output=True,text=True,timeout=60)
        discovered=run('operations');assert discovered.returncode==0
        assert 'ocr' in {op['name'] for op in json.loads(discovered.stdout)['operations']}
        payload=tmp_path/'ops.json';payload.write_text(json.dumps([{'op':'ocr'}]))
        proposed=run('apply',source,'--ops',payload,'-o',target,'--dry-run','--json')
        assert not target.exists()
        if not enabled:
            assert proposed.returncode!=0 and 'OCR' in proposed.stderr+proposed.stdout
            return
        assert proposed.returncode==0,proposed.stderr
        proposal=json.loads(proposed.stdout)
        payload.write_text(json.dumps([{'op':'ocr','expected_source_sha256':proposal['applied'][0]['ocr']['source_sha256']}]))
        executed=run('apply',source,'--ops',payload,'-o',target,'--confirm','--json')
        assert executed.returncode==0,executed.stderr
        report=json.loads(executed.stdout)
        read=run('read',target,'--json');assert read.returncode==0 and 'SCANNED INVOICE TOTAL' in read.stdout
        png=tmp_path/'readback.png';render=run('snapshot',target,'--page','1','-o',png)
        assert render.returncode==0 and fitz.Pixmap(str(png)).width>0
    else:
        import asyncio
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        from test_batch_h import _result_data,_result_image,_result_value
        async def journey():
            params=StdioServerParameters(command=str(Path(sys.executable).with_name('omapreview-mcp')),args=[],env=dict(os.environ))
            async with stdio_client(params) as (reader,writer):
                async with ClientSession(reader,writer) as session:
                    await session.initialize()
                    catalog=_result_data(await session.call_tool('operation_schema',{}))
                    assert 'ocr' in {op['name'] for op in catalog['operations']}
                    proposed=await session.call_tool('apply_ops',{'path':str(source),'ops':[{'op':'ocr'}],'output':str(target)})
                    assert not target.exists()
                    if not enabled:
                        assert _result_value(proposed,'is_error')
                        return None
                    assert not _result_value(proposed,'is_error')
                    proposal=_result_data(proposed)
                    executed=await session.call_tool('apply_ops',{'path':str(source),'ops':[{'op':'ocr','expected_source_sha256':proposal['applied'][0]['ocr']['source_sha256']}],'output':str(target),'dry_run':False})
                    assert not _result_value(executed,'is_error')
                    observed=_result_data(await session.call_tool('read_pdf',{'path':str(target)}))
                    assert 'SCANNED INVOICE TOTAL' in json.dumps(observed)
                    png=_result_image(await session.call_tool('render_page',{'path':str(target),'page':1}))
                    assert fitz.Pixmap(png).width>0
                    return _result_data(executed)
        report=asyncio.run(journey())
        if not enabled:return
    assert_ocr_report(source,target,corpus[1]['clean'],report)


@pytest.mark.parametrize('tesseract_version', ['', 'unidentified'])
def test_missing_tesseract_version(monkeypatch,tmp_path,tesseract_version):
    backend=tmp_path/'backend';backend.write_text('#!'+sys.executable+'\n');backend.chmod(0o700)
    monkeypatch.setenv('OMAPREVIEW_OCRMYPDF',str(backend))
    answers=iter(['17.11.0','17.11.0',tesseract_version])
    monkeypatch.setattr(ocr,'_query',lambda *a:next(answers))
    status=ocr.capabilities()
    assert not status['available'] and status['code']=='dependency_unsupported'
    assert 'Tesseract' in status['action']


def test_real_cancel_while_tesseract_runs(corpus,tmp_path,monkeypatch):
    backend=os.environ.get('OMAPREVIEW_TEST_OCRMYPDF')
    if not backend:pytest.skip('Set OMAPREVIEW_TEST_OCRMYPDF for real cancellation acceptance')
    monkeypatch.setenv('OMAPREVIEW_OCRMYPDF',backend)
    event=threading.Event();observed=[];groups=[];sample=ocr._rss_bytes
    def cancel_at_tesseract(group):
        groups.append(group)
        for p in Path('/proc').iterdir():
            if not p.name.isdecimal():continue
            try:
                fields=(p/'stat').read_text().rsplit(')',1)[1].split()
                if int(fields[2])==group and (p/'comm').read_text().strip()=='tesseract':
                    observed.append(int(p.name));event.set()
            except (OSError,ValueError,IndexError):pass
        return sample(group)
    monkeypatch.setattr(ocr,'_rss_bytes',cancel_at_tesseract)
    source=corpus[0]/'fixtures/geometry.pdf';target=tmp_path/'out.pdf'
    digest=fingerprint(source)
    failure('cancelled',lambda:apply(source,[{'op':'ocr','expected_source_sha256':digest}],output=target,dry_run=False,cancel_event=event),target)
    assert observed,'test must cancel running Tesseract, not just backend startup'
    assert fingerprint(source)==digest and all(not ocr._group_alive(group) for group in set(groups))


def test_worker_enforces_limits(tmp_path):
    backend=tmp_path/'backend'
    backend.write_text('#!'+sys.executable+'\nimport json,resource,sys\nkeys=[resource.RLIMIT_AS,resource.RLIMIT_FSIZE,resource.RLIMIT_CPU,resource.RLIMIT_NOFILE,resource.RLIMIT_CORE]\nopen(sys.argv[-1],"w").write(json.dumps([resource.getrlimit(k) for k in keys]))\n')
    backend.chmod(0o700);target=tmp_path/'limits.json'
    ocr._run_worker({'executable':str(backend)},tmp_path/'in.pdf',target,{'timeout_seconds':10,'languages':['eng'],'pages':[1]},tmp_path,ocr._Control(10,None,None),{'peak_temp_bytes_observed':0})
    assert json.loads(target.read_text())==[[4*1024**3]*2,[2*1024**3]*2,[12]*2,[256]*2,[0]*2]


@pytest.mark.parametrize('count',[262145,300000])
def test_query_output_bound(count):
    with pytest.raises(ValueError,match='output exceeded'):
        ocr._query([sys.executable,'-c',f'import sys;sys.stdout.write("x"*{count})'])


def test_publication_descriptor_failure_closes_parent(corpus,tmp_path,monkeypatch,passthrough):
    original_open=os.open;parent_fds=[]
    def open_with_stage_failure(path,flags,*args,**kwargs):
        if flags & os.O_DIRECTORY and Path(path).name.startswith('.omapreview-ocr-'):
            raise OSError(24,'synthetic descriptor exhaustion')
        fd=original_open(path,flags,*args,**kwargs)
        if flags & os.O_DIRECTORY:parent_fds.append(fd)
        return fd
    monkeypatch.setattr(ocr.os,'open',open_with_stage_failure)
    failure('publication_failed',lambda:request(corpus[0]/'fixtures/native.pdf',tmp_path/'out.pdf'),tmp_path/'out.pdf')
    assert parent_fds
    for fd in parent_fds:
        with pytest.raises(OSError):os.fstat(fd)


@pytest.mark.parametrize('kind', ['custom','escaped_custom','indirect_value','name_value','direct_dictionary'])
def test_unsupported_info_refuses_before_staging(tmp_path,monkeypatch,fake_backend,kind):
    source=tmp_path/'source.pdf';target=tmp_path/'copy.pdf'
    with fitz.open() as doc:
        doc.new_page();doc.set_metadata({'title':'Synthetic standard'})
        info=int(doc.xref_get_key(-1,'Info')[1].split()[0])
        if kind=='custom':doc.xref_set_key(info,'PrivateField','(synthetic custom scalar)')
        elif kind=='escaped_custom':doc.update_object(info,'<< /Title (normal) /Pri#76ate (synthetic scalar) >>')
        elif kind=='name_value':doc.xref_set_key(info,'Title','/Synthetic')
        elif kind=='indirect_value':
            xref=doc.get_new_xref();doc.update_object(xref,'(synthetic indirect title)');doc.xref_set_key(info,'Title',f'{xref} 0 R')
        else:doc.xref_set_key(-1,'Info','<< /Title (direct dictionary) >>')
        doc.save(source)
    before=fingerprint(source)
    monkeypatch.setattr(ocr.tempfile,'TemporaryDirectory',lambda **kw:pytest.fail('refusal created staging'))
    monkeypatch.setattr(ocr,'_run_worker',lambda *a:pytest.fail('refusal ran worker'))
    error=failure('unsupported_content',lambda:apply(source,[{'op':'ocr'}],output=target,dry_run=True),target)
    assert 'Info metadata' in str(error)
    assert fingerprint(source)==before


def test_image_resolution_damage_fails(tmp_path,monkeypatch,fake_backend):
    source=tmp_path/'source.pdf';target=tmp_path/'copy.pdf'
    with fitz.open() as doc:
        # All-white rasters at different resolutions have identical 100-DPI renders.
        pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,1000,1000),False);pix.clear_with(255)
        p=doc.new_page(width=72,height=72);p.insert_image(p.rect,pixmap=pix);doc.save(source)
    def damage(_deps,_source,output,*args):
        with fitz.open() as doc:
            pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,100,100),False);pix.clear_with(255)
            p=doc.new_page(width=72,height=72);p.insert_image(p.rect,pixmap=pix);doc.save(output)
    monkeypatch.setattr(ocr,'_run_worker',damage)
    failure('verification_failed',lambda:request(source,target),target)
