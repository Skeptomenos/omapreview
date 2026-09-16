"""Bounded Linux/MCP 2.2 experiment; not an application service or OCR writer.

python tests/ocr_lifecycle_probe.py NEW_DIRECTORY /path/to/ocrmypdf
Tests real stdio request cancellation, EOF and timeout against a real OCR child.
"""

import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time


def group_members(pgid):
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            data = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(data[2]) == pgid and data[0] != 'Z':
                found.append(int(entry.name))
        except (OSError, ValueError):
            continue
    return found


def server(root, executable):
    import anyio
    from mcp.server.mcpserver import MCPServer, Context
    api = MCPServer('ocr-o0-lifecycle-probe')

    @api.tool()
    async def probe(ctx: Context, mode: str) -> dict:
        log = (root / f'{mode}-worker.log').open('w')
        p = subprocess.Popen([executable, '--mode', 'skip', '--output-type', 'pdf', '--optimize', '0', '--jobs', '2', '-l', 'eng', str(root / 'source.pdf'), str(root / f'{mode}-staged.pdf')], stdout=log, stderr=log, start_new_session=True, env={**os.environ, 'TMPDIR': str(root / 'temp')})
        (root / f'{mode}-pid').write_text(str(p.pid))
        start = time.monotonic()
        reason = mode
        observed_children = []
        try:
            while p.poll() is None and time.monotonic() - start < 10:
                observed_children = []
                for pid in group_members(p.pid):
                    try:
                        observed_children.append(Path(f'/proc/{pid}/comm').read_text().strip())
                    except OSError:
                        pass
                if 'tesseract' in observed_children:
                    break
                await anyio.sleep(.01)
            assert 'tesseract' in observed_children, observed_children
            recognition_started = time.monotonic()
            await ctx.report_progress(1, 5, 'recognizing')
            while p.poll() is None:
                if mode == 'timeout' and time.monotonic() - recognition_started > .2:
                    reason = 'timeout'
                    break
                await anyio.sleep(.05)
        finally:
            # Synchronous bounded cleanup deliberately runs even in a cancelled scope.
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while group_members(p.pid) and time.monotonic() < deadline:
                time.sleep(.025)
            if group_members(p.pid):
                os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=3)
            deadline = time.monotonic() + 2
            while group_members(p.pid) and time.monotonic() < deadline:
                time.sleep(.025)
            (root / f'{mode}-staged.pdf').unlink(missing_ok=True)
            shutil.rmtree(root / 'temp')
            (root / 'temp').mkdir()
            record = {'observed_children': observed_children, 'temporary_files_remaining': list((root / 'temp').iterdir()), 'reason': reason, 'seconds': time.monotonic()-start, 'worker_returncode': p.returncode, 'live_group_members': group_members(p.pid)}
            (root / f'{mode}-cleanup.json').write_text(json.dumps(record))
            log.close()
        return record

    api.run()


def main(root, executable):
    from ocr_fixtures import add_scan, fingerprint
    import pymupdf
    root.mkdir(parents=True, exist_ok=False)
    (root / 'temp').mkdir()
    with pymupdf.open() as d:
        for _ in range(24):
            add_scan(d)
        d.save(root / 'source.pdf', deflate=True)
    original = fingerprint(root / 'source.pdf')
    results = {}
    for mode in ('cancel', 'disconnect', 'timeout'):
        with (root / f'{mode}-server.log').open('w') as log:
            p = subprocess.Popen([sys.executable, __file__, '--server', str(root), executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
            sel = selectors.DefaultSelector()
            sel.register(p.stdout, selectors.EVENT_READ)
            frames = []
            pending = bytearray()

            def send(frame):
                p.stdin.write((json.dumps({'jsonrpc': '2.0', **frame})+'\n').encode())
                p.stdin.flush()

            def until(predicate):
                deadline = time.monotonic() + 15
                while not predicate():
                    assert time.monotonic() < deadline, (mode, frames)
                    if sel.select(.1):
                        chunk = os.read(p.stdout.fileno(), 65536)
                        assert chunk, (mode, p.poll(), frames)
                        pending.extend(chunk)
                        while b'\n' in pending:
                            line, _, rest = pending.partition(b'\n')
                            pending[:] = rest
                            frames.append(json.loads(line))

            try:
                send({'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'O0-probe', 'version': '1'}}})
                until(lambda: any(f.get('id') == 1 for f in frames))
                send({'method': 'notifications/initialized'})
                send({'id': 2, 'method': 'tools/call', 'params': {'name': 'probe', 'arguments': {'mode': mode}, '_meta': {'progressToken': 'o0'}}})
                until(lambda: any(f.get('method') == 'notifications/progress' for f in frames))
                if mode == 'cancel':
                    # Cancellation arrives while the real child is running.
                    send({'method': 'notifications/cancelled', 'params': {'requestId': 2, 'reason': 'synthetic cancellation'}})
                elif mode == 'disconnect':
                    p.stdin.close()
                else:
                    until(lambda: any(f.get('id') == 2 for f in frames))
                deadline = time.monotonic()+8
                while not (root/f'{mode}-cleanup.json').exists():
                    assert time.monotonic() < deadline, mode
                    time.sleep(.05)
                cleanup = json.loads((root/f'{mode}-cleanup.json').read_text())
                assert not cleanup['live_group_members'], cleanup
                assert fingerprint(root / 'source.pdf') == original
                results[mode] = {'wire_frames': frames, 'cleanup': cleanup, 'source_preserved': True}
            finally:
                if not p.stdin.closed:
                    p.stdin.close()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                sel.close()
    (root/'results.json').write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    if sys.argv[1] == '--server':
        server(Path(sys.argv[2]), sys.argv[3])
    else:
        main(Path(sys.argv[1]), sys.argv[2])
