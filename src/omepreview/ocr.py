"""Optional, synchronous OCR transaction. Core imports are stdlib + PyMuPDF.

Only this module stages and publishes OCR copies. The backend is an isolated
process group; no recognition result is trusted before saved-PDF verification.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata

import pymupdf as fitz

from .ops import OCRError, validate

SUPPORTED_RANGE = ">=17.11.0,<17.12"
MAX_PAGES = 100
MAX_INPUT = 500 * 1024**2
MAX_TEMP = 2 * 1024**3
MAX_PIXELS = 100_000_000
VERIFY_DPI = 100
PHASES = ("preflight", "staging", "recognizing", "verifying", "publishing", "complete")
BOXES = ("mediabox", "cropbox", "trimbox", "bleedbox", "artbox")


class _Control:
    def __init__(self, seconds, cancel_event, progress):
        self.deadline = time.monotonic() + seconds
        self.cancel_event = cancel_event
        self.progress = progress

    def check(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise OCRError("cancelled", "OCR cancelled; the source is unchanged")
        if time.monotonic() >= self.deadline:
            raise OCRError("worker_timeout", "OCR exceeded its total deadline; try fewer pages or a longer timeout")

    def phase(self, name):
        self.check()
        if self.progress:
            try:
                self.progress({"phase": name, "completed": PHASES.index(name), "total": 5})
            except Exception:
                # Client callbacks cannot invalidate a document transaction.
                pass


def _group_alive(pid):
    # Linux /proc lets us distinguish zombies (already stopped) from live work.
    for item in Path('/proc').iterdir():
        if not item.name.isdecimal():
            continue
        try:
            fields = (item / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pid and fields[0] != 'Z':
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False


def _stop(process):
    for sig, grace in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        until = time.monotonic() + grace
        while time.monotonic() < until:
            process.poll()
            if not _group_alive(process.pid):
                break
            time.sleep(.025)
        if not _group_alive(process.pid):
            break
    process.wait(timeout=2)
    if _group_alive(process.pid):
        raise OCRError("worker_failed", "OCR child cleanup could not be verified; inspect the worker process group before retrying")


def _query(command, control=None):
    """Bounded dependency query; cap output too, without creating files."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    assert process.stdout is not None
    os.set_blocking(process.stdout.fileno(), False)
    until = min(time.monotonic() + 10, control.deadline if control else float('inf'))
    chunks = bytearray()
    try:
        while True:
            if control:
                control.check()
            chunk = os.read(process.stdout.fileno(), 65536) if _readable(process.stdout) else b''
            chunks.extend(chunk)
            if len(chunks) > 256 * 1024:
                raise ValueError("Dependency query output exceeded its bound")
            if not chunk and process.poll() is not None:
                if process.returncode:
                    raise ValueError("Dependency query failed")
                return chunks.decode(errors='replace').strip()
            if time.monotonic() >= until:
                raise ValueError("Dependency query exceeded 10 seconds")
            time.sleep(.02)
    finally:
        try:
            _stop(process)
        finally:
            process.stdout.close()


def _readable(pipe):
    import select
    return bool(select.select([pipe], [], [], 0)[0])


def capabilities(*, cancel_event=None, progress=None, _control=None):
    """Read-only dependency/language report. No optional import, download or cache.

    OMAPREVIEW_OCRMYPDF explicitly selects a launcher; otherwise prefer the app's
    interpreter sibling, then PATH. Invalid explicit selection never falls back.
    """
    control = _control or _Control(60, cancel_event, progress)
    explicit = os.environ.get('OMAPREVIEW_OCRMYPDF')
    sibling = Path(sys.executable).absolute().with_name('ocrmypdf')
    executable = explicit or (str(sibling) if sibling.is_file() else shutil.which('ocrmypdf'))
    result = {"available": False, "executable": executable, "interpreter": None,
              "version": None, "supported_range": SUPPORTED_RANGE,
              "pymupdf_version": fitz.VersionBind, "tesseract": None,
              "languages": [], "rasterizer": {"name": "ghostscript", "executable": shutil.which('gs'), "version": None},
              "code": "dependency_missing", "action": "Install the optional OCR extra and Tesseract language data, then recheck OCR capabilities"}
    if sys.platform != 'linux':
        result.update(code="dependency_unsupported", action="This OCR route is accepted on Linux only; use an accepted Linux installation")
        return result
    if not executable or not Path(executable).is_file() or not os.access(executable, os.X_OK):
        return result
    result['executable'] = str(Path(executable).absolute())
    try:
        with open(executable, 'rb') as handle:
            first = handle.readline(4096).decode(errors='replace').strip()
        # Exact interpreter, without guessing which Python an env shebang selects.
        interpreter = first[2:] if first.startswith('#!/') and ' ' not in first else None
        result['interpreter'] = interpreter
        if not interpreter or not Path(interpreter).is_file() or not os.access(interpreter, os.X_OK):
            result.update(code='dependency_unsupported', action='OCR launcher must identify its absolute Python interpreter; reinstall the OCR extra in the application environment')
            return result
        version = _query([executable, '--version'], control)
        metadata_version = _query([interpreter, '-c', 'from importlib.metadata import version; print(version("ocrmypdf"))'], control)
        if not version:
            version = metadata_version
        elif version.removeprefix('ocrmypdf ').strip() != metadata_version:
            result.update(code='dependency_unsupported', action='OCR launcher and interpreter versions disagree; reinstall the OCR extra and recheck')
            return result
        match = re.fullmatch(r'(?:ocrmypdf\s+)?(\d+\.\d+\.\d+)', version, re.I)
        result['version'] = match.group(1) if match else None
        if not match or tuple(map(int, match.group(1).split('.')))[:2] != (17, 11):
            result.update(code='dependency_unsupported', action=f'Install OCRmyPDF {SUPPORTED_RANGE} and recheck its version')
            return result
        tess = shutil.which('tesseract')
        gs = result['rasterizer']['executable']
        if not tess or not gs:
            return result
        version_lines = _query([tess, '--version'], control).splitlines()
        tess_version = version_lines[0] if version_lines else ''
        if not re.fullmatch(r'tesseract [0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[ .+-].*)?', tess_version):
            result.update(code='dependency_unsupported', action='Tesseract did not report a known version; check its installation and recheck')
            return result
        result['tesseract'] = {"executable": tess, "version": tess_version}
        result['languages'] = [s.strip() for s in _query([tess, '--list-langs'], control).splitlines() if re.fullmatch(r'[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)?', s.strip())]
        result['rasterizer']['version'] = _query([gs, '--version'], control)
        if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', result['rasterizer']['version']):
            return result
        result.update(available=True, code=None, action=None)
    except OCRError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        result['action'] = 'OCR dependency discovery failed; check the selected executable and installed versions, then recheck'
    return result


def _identity(path):
    link = path.lstat()
    target = path.stat()
    return (str(path.resolve(strict=True)), link.st_dev, link.st_ino, link.st_mtime_ns,
            target.st_dev, target.st_ino, target.st_size, target.st_mtime_ns, target.st_ctime_ns)


def _read_source(path, control):
    identity = _identity(path)
    if not stat.S_ISREG(path.stat().st_mode) or identity[6] > MAX_INPUT:
        raise OCRError('resource_limit', 'OCR requires a regular PDF of at most 500 MiB')
    data = bytearray()
    with path.open('rb') as handle:
        while chunk := handle.read(1024**2):
            control.check()
            data.extend(chunk)
            if len(data) > MAX_INPUT:
                raise OCRError('resource_limit', 'OCR source exceeds 500 MiB; use a smaller copy')
    if identity != _identity(path):
        raise OCRError('source_conflict', 'OCR source changed while reading; run preflight again')
    return bytes(data), identity, hashlib.sha256(data).hexdigest()


def _check_source(path, identity, digest, control):
    try:
        if _identity(path) != identity:
            raise OCRError('source_conflict', 'OCR source identity changed; run preflight again')
        h = hashlib.sha256()
        with path.open('rb') as handle:
            while chunk := handle.read(1024**2):
                control.check()
                h.update(chunk)
        if h.hexdigest() != digest or _identity(path) != identity:
            raise OCRError('source_conflict', 'OCR source bytes changed; run preflight again')
    except OSError as exc:
        raise OCRError('source_conflict', 'OCR source is no longer readable at its original path; run preflight again') from exc


def _destination(source, output):
    if output is None:
        raise OCRError('destination_conflict', 'OCR requires an explicit new output PDF path')
    path = Path(output).expanduser().absolute()
    if os.path.lexists(path) or path.resolve() == source.resolve():
        raise OCRError('destination_conflict', 'OCR destination already exists or aliases the source; choose a new copy path')
    if not path.parent.is_dir():
        raise OCRError('destination_conflict', 'OCR destination directory is missing; choose an existing directory')
    return path


def _usable(text):
    return any(not c.isspace() and not unicodedata.category(c).startswith('C') for c in text)


def _names(obj):
    """Lex PDF names, skipping literal/hex strings and comments, including nesting.

    xref_object decodes compressed objects; scanning raw PDF bytes would miss them.
    Name # escapes are decoded to cover /S#69g as well as /Sig.
    """
    names = []
    i = 0
    while i < len(obj):
        c = obj[i]
        if c == '(':
            depth = 1
            i += 1
            while i < len(obj) and depth:
                if obj[i] == '\\':
                    i += 2
                    continue
                depth += (obj[i] == '(') - (obj[i] == ')')
                i += 1
            continue
        if c == '<' and not obj.startswith('<<', i):
            i = obj.find('>', i) + 1 or len(obj)
            continue
        if obj.startswith('<<', i):
            i += 2
            continue
        if c == '%':
            i = obj.find('\n', i) + 1 or len(obj)
            continue
        if c == '/':
            match = re.match(r'/([^\s\[\]()<>{}/%]+)', obj[i:])
            if match:
                names.append(re.sub(r'#([0-9a-fA-F]{2})', lambda m: chr(int(m[1], 16)), match[1]))
                i += len(match[0])
                continue
        i += 1
    return set(names)


def _inherited(doc, page, key):
    xref = page.xref
    seen = set()
    while xref and xref not in seen:
        seen.add(xref)
        kind, value = doc.xref_get_key(xref, key)
        if kind != 'null':
            return kind, value
        kind, parent = doc.xref_get_key(xref, 'Parent')
        xref = int(parent.split()[0]) if kind == 'xref' else 0
    return 'null', 'null'


def _raw_box(doc, page, name, required=False):
    kind, value = _inherited(doc, page, name)
    if kind == 'null' and not required:
        return
    if kind == 'xref':
        value = doc.xref_object(int(value.split()[0]))
        kind = 'array' if value.startswith('[') else 'invalid'
    values = value.strip('[]').split() if kind == 'array' else []
    try:
        numbers = [float(v) for v in values]
    except ValueError:
        numbers = []
    if (len(numbers) != 4 or not all(math.isfinite(v) for v in numbers)
            or numbers[2] <= numbers[0] or numbers[3] <= numbers[1]):
        raise OCRError('unsupported_geometry', f'OCR v1 refuses malformed {name} on page {page.number+1}; use an intact supported copy')


def _outline(doc):
    return [[*entry[:3], {k: v for k, v in entry[3].items() if k != 'xref'}]
            for entry in doc.get_toc(simple=False)]


_INFO_KEYS = frozenset(('Title', 'Author', 'Subject', 'Keywords', 'Creator',
                        'Producer', 'CreationDate', 'ModDate'))


def _check_info(doc):
    # PyMuPDF metadata restores these text fields. Refuse representations it
    # cannot round-trip instead of silently dropping custom metadata.
    kind, value = doc.xref_get_key(-1, 'Info')
    if kind == 'null':
        return
    if kind != 'xref':
        raise OCRError('unsupported_content', f'OCR v1 cannot preserve Info metadata kind {kind}; use the original or an explicitly prepared supported copy')
    xref = int(value.split()[0])
    if not doc.xref_object(xref).lstrip().startswith('<<'):
        raise OCRError('unsupported_content', 'OCR v1 requires an Info dictionary; use the original')
    for key in doc.xref_get_keys(xref):
        value_kind, _value = doc.xref_get_key(xref, key)
        if key not in _INFO_KEYS or value_kind != 'string':
            raise OCRError('unsupported_content', f'OCR v1 cannot preserve Info metadata /{key} of kind {value_kind}; use the original or an explicitly prepared supported copy')


def _inspect(doc, selected, control):
    if doc.needs_pass or doc.is_encrypted or doc.metadata.get('encryption'):
        raise OCRError('encrypted', 'OCR v1 cannot preserve encryption; use the original or an explicitly prepared unencrypted copy')
    if not doc.is_pdf or doc.is_repaired:
        raise OCRError('unsupported_content', 'OCR requires an intact PDF; use the original or an explicitly repaired copy')
    if not 1 <= len(doc) <= MAX_PAGES:
        raise OCRError('resource_limit', 'OCR accepts 1 to 100 pages per document; prepare a smaller copy')
    if any(n > len(doc) for n in selected):
        raise OCRError('invalid_request', f'OCR page is out of range; this PDF has {len(doc)} pages')
    _check_info(doc)
    problems = []
    for xref in range(1, doc.xref_length()):
        control.check()
        names = _names(doc.xref_object(xref))
        if names & {'Sig', 'ByteRange', 'DocMDP', 'SigRef'}:
            problems.append(('signature_structure', f'signature structure at object {xref}'))
        elif 'Redact' in names:
            problems.append(('pending_redactions', f'pending redaction at object {xref}'))
        elif names & {'AcroForm', 'XFA', 'Widget', 'Annots', 'EmbeddedFiles', 'Filespec', 'EmbeddedFile', 'JavaScript', 'JS', 'OpenAction', 'AA', 'Launch', 'RichMedia', 'Movie', 'Sound', 'Collection', 'StructTreeRoot', 'MarkInfo'}:
            problems.append(('unsupported_content', f'unsupported structure at object {xref}: {", ".join(sorted(names & {"AcroForm", "XFA", "Widget", "Annots", "EmbeddedFiles", "Filespec", "EmbeddedFile", "JavaScript", "JS", "OpenAction", "AA", "Launch", "RichMedia", "Movie", "Sound", "Collection", "StructTreeRoot", "MarkInfo"}))}'))
    if problems:
        rank = {'signature_structure': 0, 'pending_redactions': 1, 'unsupported_content': 2}
        code, reason = min(problems, key=lambda p: rank[p[0]])
        raise OCRError(code, f'OCR v1 refuses {reason}; use the original or an explicitly prepared copy')
    for item in doc.get_toc(simple=False):
        dest = item[3]
        if dest.get('kind') != fitz.LINK_GOTO or not 0 <= dest.get('page', -1) < len(doc):
            raise OCRError('unsupported_content', 'OCR v1 refuses rich or external outline destinations; use the original')
    if doc.embfile_count():
        raise OCRError('unsupported_content', 'OCR v1 refuses embedded files; use the original')
    pages = []
    for p in doc:
        control.check()
        if list(p.widgets()) or list(p.annots()) or p.get_links():
            raise OCRError('unsupported_content', f'OCR v1 refuses page {p.number+1} annotations, links or forms; use the original')
        unit_type, unit = _inherited(doc, p, 'UserUnit')
        rot_type, rotation = _inherited(doc, p, 'Rotate')
        if (unit_type != 'null' and (unit_type not in ('int', 'float') or float(unit) != 1)
                or rot_type != 'null' and (rot_type != 'int' or int(rotation) not in (0, 90, 180, 270))
                or p.mediabox.x0 != 0 or p.mediabox.y0 != 0):
            raise OCRError('unsupported_geometry', f'OCR v1 refuses page {p.number+1} UserUnit, rotation or MediaBox origin; use a supported copy')
        for name in ('MediaBox', 'CropBox', 'TrimBox', 'BleedBox', 'ArtBox'):
            _raw_box(doc, p, name, required=name == 'MediaBox')
        for box in BOXES:
            rect = getattr(p, box)
            if not all(math.isfinite(v) for v in rect) or rect.is_empty or rect.is_infinite or not p.mediabox.contains(rect):
                raise OCRError('unsupported_geometry', f'OCR v1 refuses invalid {box} on page {p.number+1}')
        if math.ceil(p.rect.width * VERIFY_DPI / 72) * math.ceil(p.rect.height * VERIFY_DPI / 72) > MAX_PIXELS:
            raise OCRError('resource_limit', f'Page {p.number+1} exceeds the 100 MP verification raster limit')
        for img in p.get_image_info():
            if img['width'] * img['height'] > MAX_PIXELS:
                raise OCRError('resource_limit', f'Page {p.number+1} contains an image larger than 100 MP')
        text = p.get_text().strip()
        chosen = p.number + 1 in selected
        status, reason = ('skipped', 'not_selected') if not chosen else ('skipped', 'existing_text') if _usable(text) else ('eligible', None)
        if status == 'eligible':
            pix = p.get_pixmap(dpi=VERIFY_DPI, colorspace=fitz.csRGB, alpha=False)
            control.check()
            if pix.samples.count(255) == len(pix.samples_mv):
                status, reason = 'blank', 'blank'
        page = {"page": p.number+1, "selected": chosen, "status": status, "reason": reason,
                "text_before_chars": len(text), "text_after_chars": None, "word_count": None, "review_required": False}
        if reason == 'existing_text' and p.get_images():
            page['scanned_regions_not_processed'] = True
        pages.append(page)
    return pages


def _temp_bytes(directory):
    total = 0
    for path in directory.rglob('*'):
        try:
            info = path.lstat()
        except FileNotFoundError:
            # Backend cleanup is concurrent with this observational limit.
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def _rss_bytes(group):
    total = 0
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():
            continue
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == group and fields[0] != 'Z':
                total += int(fields[21]) * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, IndexError):
            continue
    return total


def _run_worker(deps, source, output, op, directory, control, metrics):
    command = [sys.executable, str(Path(__file__).with_name('ocr_worker.py')),
               deps['executable'], str(op['timeout_seconds']), '--mode', 'skip',
               '--output-type', 'pdf', '--optimize', '0', '--jobs', '2',
               '--rasterizer', 'ghostscript', '--language', '+'.join(op['languages']),
               '--tesseract-timeout', '30', '--max-image-mpixels', '100',
               '--max-ocr-image-mpixels', '100', '--pages', ','.join(map(str, op['pages'])),
               str(source), str(output)]
    env = os.environ.copy()
    env.update(TMPDIR=str(directory), OMP_THREAD_LIMIT='1')
    # No backend logs are returned: they can contain document text or filenames.
    with (directory / 'worker.log').open('wb') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, cwd=directory,
                                   env=env, start_new_session=True)
        try:
            while process.poll() is None:
                control.check()
                used = _temp_bytes(directory)
                metrics['peak_worker_rss_bytes_observed'] = max(metrics.get('peak_worker_rss_bytes_observed', 0), _rss_bytes(process.pid))
                metrics['peak_temp_bytes_observed'] = max(metrics['peak_temp_bytes_observed'], used)
                if used > MAX_TEMP:
                    raise OCRError('resource_limit', 'OCR temporary storage exceeded 2 GiB; use fewer pages')
                time.sleep(.05)
            control.check()
            if process.returncode in (-signal.SIGXCPU, -signal.SIGXFSZ):
                raise OCRError('resource_limit', 'OCR worker exceeded a CPU or file-size limit; use fewer pages')
            if process.returncode:
                raise OCRError('worker_failed', f'OCR backend exited with code {process.returncode}; check dependencies or try a smaller supported copy')
        finally:
            _stop(process)
    if output.is_symlink() or not output.is_file() or not output.stat().st_size:
        raise OCRError('worker_failed', 'OCR backend produced no PDF; check the backend and try again')


def _restore(raw, target, original, control):
    control.check()
    try:
        with fitz.open(raw) as doc:
            if len(doc) != len(original) or doc.is_repaired or doc.needs_pass or doc.metadata.get('encryption'):
                raise OCRError('verification_failed', 'OCR output is malformed or has changed page count; no copy was published')
            for p, source in zip(doc, original):
                control.check()
                if p.rotation != 0:
                    raise OCRError('verification_failed', 'OCR changed normalized rotation; no copy was published')
                p.set_rotation(source.rotation)
            info_kind, info_value = original.xref_get_key(-1, 'Info')
            if info_kind == 'null':
                doc.xref_set_key(-1, 'Info', 'null')
            else:
                # Preflight proved this dictionary has only known direct strings:
                # no foreign object references can cross into the output. Copy it
                # exactly, avoiding backend-added /Trapped null placeholders.
                info_xref = doc.get_new_xref()
                doc.update_object(info_xref, original.xref_object(int(info_value.split()[0])))
                doc.xref_set_key(-1, 'Info', f'{info_xref} 0 R')
            xml = original.get_xml_metadata()
            if xml:
                doc.set_xml_metadata(xml)
            else:
                doc.del_xml_metadata()
            doc.save(target)
    except OCRError:
        raise
    except Exception as exc:
        raise OCRError('verification_failed', 'OCR backend produced an unreadable PDF; no copy was published') from exc


def _verify(original, staged, pages, control):
    with fitz.open(staged) as after:
        try:
            _inspect(after, [], control)
        except OCRError as exc:
            if exc.code in ('cancelled', 'worker_timeout', 'resource_limit'):
                raise
            raise OCRError('verification_failed', 'OCR introduced unsupported PDF structures; no copy was published') from exc
        if len(after) != len(original):
            raise OCRError('verification_failed', 'OCR changed page count; no copy was published')
        if (after.metadata != original.metadata or after.get_xml_metadata() != original.get_xml_metadata()
                or _outline(after) != _outline(original) or after.get_page_labels() != original.get_page_labels()):
            raise OCRError('verification_failed', 'OCR changed metadata, outlines or page labels; no copy was published')
        for a, b, report in zip(original, after, pages):
            control.check()
            if a.rotation != b.rotation or any(getattr(a, box) != getattr(b, box) for box in BOXES):
                raise OCRError('verification_failed', f'OCR changed page {a.number+1} geometry; no copy was published')
            x = a.get_pixmap(dpi=VERIFY_DPI, colorspace=fitz.csRGB, alpha=False)
            control.check()
            y = b.get_pixmap(dpi=VERIFY_DPI, colorspace=fitz.csRGB, alpha=False)
            control.check()
            if (x.width, x.height, x.samples) != (y.width, y.height, y.samples):
                raise OCRError('verification_failed', f'OCR changed page {a.number+1} appearance; no copy was published')
            before, text = a.get_text().strip(), b.get_text().strip()
            words = b.get_text('words')
            bounds = fitz.Rect(0, 0, b.cropbox.width, b.cropbox.height)
            if any(not all(math.isfinite(v) for v in w[:4]) or not bounds.contains(fitz.Rect(w[:4])) for w in words):
                raise OCRError('verification_failed', f'OCR text on page {a.number+1} has invalid coordinates; no copy was published')
            if report['status'] in ('skipped', 'blank') and text != before:
                raise OCRError('verification_failed', f'OCR changed protected text on page {a.number+1}; no copy was published')
            report.update(text_after_chars=len(text), word_count=len(words))
            if report['status'] == 'eligible':
                if _usable(text) and words:
                    report.update(status='processed', reason=None, review_required=True)
                else:
                    report.update(status='needs_review', reason='existing_text_unusable' if a.get_text('words') else 'no_text', review_required=True)
        # Digest queries after admission inspection can warm MuPDF's image cache
        # differently for rotated pages. Finish every render check first; saved
        # bytes are unchanged, and the separate image checks remain semantic.
        for a, b in zip(original, after):
            control.check()
            image_keys = ('width', 'height', 'bpc', 'colorspace', 'bbox', 'transform', 'digest')
            images_before = [tuple(info[k] for k in image_keys) for info in a.get_image_info(hashes=True)]
            control.check()
            images_after = [tuple(info[k] for k in image_keys) for info in b.get_image_info(hashes=True)]
            if images_before != images_after:
                raise OCRError('verification_failed', f'OCR changed page {a.number+1} image pixels, resolution or placement; no copy was published')
    return {"geometry_equal": True, "renders_equal": True, "text_preserved": True,
            "word_bounds_valid": True, "images_equal": True, "metadata_equal": True, "outlines_equal": True,
            "page_labels_equal": True, "dpi": VERIFY_DPI}


def preflight(source, op, output, *, cancel_event=None, progress=None):
    """Return the same no-write proposal as engine.apply(..., dry_run=True)."""
    normalized = validate(op)
    if normalized["op"] != "ocr":
        raise OCRError("invalid_request", "OCR preflight requires an ocr operation")
    return execute(source, normalized, output, dry_run=True, cancel_event=cancel_event, progress=progress)


def execute(source, op, output, *, dry_run, cancel_event=None, progress=None):
    control = _Control(op['timeout_seconds'], cancel_event, progress)
    report = {"status": "preflight", "source_sha256": None, "output_sha256": None,
              "destination": None, "pages": [], "dependencies": {}, "recognized_pages": 0,
              "verification": {}, "warnings": ["OCR text requires human review; extraction does not prove transcription accuracy"]}
    source = Path(source).expanduser().absolute()
    started = time.monotonic()
    committed = False
    try:
        control.phase('preflight')
        destination = _destination(source, output)
        report['destination'] = str(destination)
        data, identity, digest = _read_source(source, control)
        report['source_sha256'] = digest
        if not dry_run and not op.get('expected_source_sha256'):
            raise OCRError('confirmation_required', 'Run OCR preflight, then supply its expected_source_sha256 and explicitly execute')
        if op.get('expected_source_sha256', digest) != digest:
            raise OCRError('source_conflict', 'OCR source differs from the approved preflight; run preflight again')
        with fitz.open(stream=data, filetype='pdf') as original:
            selected = op.get('pages', list(range(1, len(original)+1)))
            report['pages'] = _inspect(original, selected, control)
            op = {**op, 'pages': selected}
            deps = capabilities(_control=control)
            report['dependencies'] = deps
            if not deps['available']:
                raise OCRError(deps['code'], deps['action'])
            missing = [v for v in op['languages'] if v not in deps['languages']]
            if missing:
                raise OCRError('language_missing', f'OCR language data missing: {", ".join(missing)}; install the language packs and recheck')
            _check_source(source, identity, digest, control)
            if dry_run:
                return {"output": None, "applied": [{**op, "applied": False, "ocr": report}]}
            control.phase('staging')
            parent = destination.parent.resolve(strict=True)
            parent_identity = parent.stat()
            with tempfile.TemporaryDirectory(prefix='.omapreview-ocr-', dir=parent) as temp:
                directory = Path(temp)
                os.chmod(directory, 0o700)
                snapshot = directory / 'snapshot.pdf'
                snapshot.write_bytes(data)
                os.chmod(snapshot, 0o600)
                del data
                _check_source(source, identity, digest, control)
                normalized = directory / 'normalized.pdf'
                with fitz.open(snapshot) as doc:
                    for p in doc:
                        control.check()
                        p.set_rotation(0)
                    doc.save(normalized)
                os.chmod(normalized, 0o600)
                raw, verified = directory / 'recognized.pdf', directory / 'verified.pdf'
                metrics = {
                    'peak_temp_bytes_observed': _temp_bytes(directory),
                    'temp_limit_is_hard_quota': False,
                    'limits': {'pages': MAX_PAGES, 'input_bytes': MAX_INPUT,
                               'temp_bytes_polled': MAX_TEMP, 'raster_pixels': MAX_PIXELS,
                               'worker_address_space_bytes_per_process': 4 * 1024**3,
                               'worker_file_bytes': 2 * 1024**3, 'jobs': 2,
                               'tesseract_seconds_per_page': 30,
                               'total_deadline_seconds': op['timeout_seconds']},
                }
                report['resources'] = metrics
                control.phase('recognizing')
                _run_worker(deps, normalized, raw, op, directory, control, metrics)
                control.phase('verifying')
                _restore(raw, verified, original, control)
                os.chmod(verified, 0o600)
                report['verification'] = _verify(original, verified, report['pages'], control)
                used = _temp_bytes(directory)
                metrics['peak_temp_bytes_observed'] = max(metrics['peak_temp_bytes_observed'], used)
                if used > MAX_TEMP:
                    raise OCRError('resource_limit', 'OCR temporary storage exceeded 2 GiB; use fewer pages')
                control.phase('publishing')
                _destination(source, destination)
                current_parent = destination.parent.stat()
                if destination.parent.resolve() != parent or (current_parent.st_dev, current_parent.st_ino) != (parent_identity.st_dev, parent_identity.st_ino):
                    raise OCRError('destination_conflict', 'OCR destination directory changed; choose the destination again')
                with verified.open('rb') as handle:
                    os.fsync(handle.fileno())
                    output_hash = hashlib.file_digest(handle, 'sha256').hexdigest()
                _check_source(source, identity, digest, control)
                control.check()
                report.update(status='needs_review' if any(p['status'] == 'needs_review' for p in report['pages']) else 'complete',
                              output_sha256=output_hash, recognized_pages=sum(p['status'] == 'processed' for p in report['pages']))
                try:
                    # Atomic creation; a racing occupant is never overwritten.
                    # Bind both directories to open descriptors so a parent-path
                    # retarget cannot redirect creation into another directory.
                    with ExitStack() as descriptors:
                        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                        descriptors.callback(os.close, parent_fd)
                        stage_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                        descriptors.callback(os.close, stage_fd)
                        current = os.fstat(parent_fd)
                        if (current.st_dev, current.st_ino) != (parent_identity.st_dev, parent_identity.st_ino):
                            raise OCRError('destination_conflict', 'OCR destination directory changed; select it again')
                        os.link(verified.name, destination.name, src_dir_fd=stage_fd, dst_dir_fd=parent_fd)
                        committed = True
                except FileExistsError as exc:
                    raise OCRError('destination_conflict', 'OCR destination appeared during recognition; choose a new copy path') from exc
                except OSError as exc:
                    raise OCRError('publication_failed', 'Could not publish OCR copy; check destination permissions and free space') from exc
                # Commit point passed. Cancellation must now return the saved copy.
                metrics['elapsed_seconds'] = round(time.monotonic() - started, 3)
        if progress:
            try:
                progress({'phase': 'complete', 'completed': 5, 'total': 5})
            except Exception:
                pass
        return {"output": str(destination), "applied": [{**op, "applied": True, "ocr": report}]}
    except OCRError as exc:
        if committed:
            report['warnings'].append('The copy was published, but final cleanup failed; inspect temporary files before retrying')
            return {"output": str(destination), "applied": [{**op, "applied": True, "ocr": report}]}
        report['status'] = {'cancelled': 'cancelled', 'worker_timeout': 'timed_out'}.get(exc.code, 'failed')
        report['output_sha256'] = None
        exc.ocr = report
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        if committed:
            report['warnings'].append('The copy was published, but final cleanup failed; inspect temporary files before retrying')
            return {"output": str(destination), "applied": [{**op, "applied": True, "ocr": report}]}
        report['status'] = 'failed'
        report['output_sha256'] = None
        raise OCRError('unsupported_content', 'OCR could not read or stage this PDF; check that it is an intact supported PDF and destination is writable', report) from exc
