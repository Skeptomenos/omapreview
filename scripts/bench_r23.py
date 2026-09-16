#!/usr/bin/env python3
"""Reproducible R23 backend measurements for omapreview.

The tool creates synthetic text and image-only (scanned-style) PDFs, then
measures the real PyMuPDF/page-preview/editor/recorder paths.  It also times a
representative CLI snapshot subprocess.  It does not open user files and does
not change product code or user state.  Native GTK response measurements are
recorded separately because a driver round trip is not an application-only
latency measurement.

Example::

    .venv/bin/python scripts/bench_r23.py --output /tmp/omapreview-r23

The JSON output is suitable for comparison between commits.  All durations
are wall-clock measurements from ``perf_counter_ns`` on the current host.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, TypeVar


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pymupdf

from omepreview.gui import Editor
from omepreview.page_preview import PagePreviewState
from omepreview.render import raster_page
from omepreview.trackpad_sig import RecorderSession, strokes_to_svg


T = TypeVar("T")
SEARCH_TERM = "R23-SEARCH-TOKEN"


@dataclass(frozen=True)
class Fixture:
    kind: str
    path: Path
    page_count: int
    size_bytes: int
    page_width_points: float
    page_height_points: float
    image_only: bool


def _stats(values: Iterable[float]) -> dict[str, float]:
    numbers = [float(value) for value in values]
    if not numbers:
        raise ValueError("cannot summarize an empty sample set")
    ordered = sorted(numbers)

    def percentile(percent: float) -> float:
        index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
        return ordered[index]

    return {
        "min": min(numbers),
        "median": statistics.median(numbers),
        "p95": percentile(95),
        "max": max(numbers),
    }


def _timed(fn: Callable[[], T]) -> tuple[float, T]:
    started = time.perf_counter_ns()
    value = fn()
    elapsed = time.perf_counter_ns() - started
    return elapsed / 1_000_000, value


def _samples(fn: Callable[[], T], repetitions: int, warmups: int = 1) -> list[tuple[float, T]]:
    for _ in range(warmups):
        fn()
    return [_timed(fn) for _ in range(repetitions)]


def _rss_bytes() -> int | None:
    """Return current RSS on Linux, if the proc status is available."""
    try:
        status = Path("/proc/self/status").read_text(encoding="ascii")
    except OSError:
        return None
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            return int(parts[1]) * 1024
    return None


def _max_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB.  Keep the fallback conservative for other systems.
    return int(value * 1024 if sys.platform == "linux" else value)


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _private(path: Path) -> Path:
    path.chmod(0o600)
    return path


def _make_text_pdf(path: Path, page_count: int) -> Fixture:
    doc = pymupdf.open()
    for page_number in range(1, page_count + 1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((54, 46), f"R23 synthetic text fixture · page {page_number}", fontsize=16)
        for line in range(76):
            y = 78 + line * 9.6
            token = f" {SEARCH_TERM}" if line % 9 == 0 else ""
            page.insert_text(
                (54, y),
                f"Paragraph {line:02d} contains representative searchable content.{token}",
                fontsize=7.5,
            )
        page.draw_rect(pymupdf.Rect(40, 32, 555, 815), color=(0.75, 0.75, 0.75), width=0.6)
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()
    _private(path)
    return Fixture("text", path, page_count, path.stat().st_size, 595.0, 842.0, False)


def _scan_image(page_number: int, width: int = 1400, height: int = 1980) -> bytes:
    """Build a deterministic document-like raster with no text layer."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(247)
    # Text-like horizontal strokes and occasional blocks make raster work
    # representative without introducing random or user-provided content.
    for row in range(120, height - 110, 32):
        left = 90 + ((row * 13 + page_number * 19) % 80)
        right = width - 110 - ((row * 7 + page_number * 11) % 180)
        if row % 160 == 0:
            right = min(width - 110, left + 420 + page_number * 7)
        pix.set_rect(
            pymupdf.IRect(left, row, max(left + 4, right), min(height, row + 3)),
            (62, 67, 76),
        )
    for col in range(130, width - 160, 260):
        pix.set_rect(pymupdf.IRect(col, 86, col + 4, height - 90), (214, 218, 222))
    return pix.tobytes("png")


def _make_scanned_pdf(path: Path, page_count: int) -> Fixture:
    doc = pymupdf.open()
    for page_number in range(1, page_count + 1):
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=_scan_image(page_number))
    doc.save(str(path), garbage=3, deflate=True)
    doc.close()
    _private(path)
    return Fixture("scanned", path, page_count, path.stat().st_size, 595.0, 842.0, True)


def _fixture_metadata(fixture: Fixture) -> dict[str, object]:
    result = asdict(fixture)
    result["path"] = str(fixture.path)
    result["content"] = "image-only" if fixture.image_only else "text-layer"
    result["sha256"] = __import__("hashlib").sha256(fixture.path.read_bytes()).hexdigest()
    return result


def _open_page_count(path: Path) -> int:
    doc = pymupdf.open(path)
    try:
        return doc.page_count
    finally:
        doc.close()


def _thumbnail_open_refresh(path: Path) -> tuple[float, float, int]:
    started = time.perf_counter_ns()
    doc = pymupdf.open(path)
    open_ms = (time.perf_counter_ns() - started) / 1_000_000
    png_bytes = 0
    try:
        refresh_started = time.perf_counter_ns()
        for page in doc:
            for width in (168, 84):
                scale = width / page.rect.width
                pix = raster_page(page, scale)
                png_bytes += len(pix.tobytes("png"))
        refresh_ms = (time.perf_counter_ns() - refresh_started) / 1_000_000
    finally:
        doc.close()
    return open_ms, refresh_ms, png_bytes


def _search(path: Path, term: str) -> tuple[int, int]:
    doc = pymupdf.open(path)
    hits = 0
    pages = doc.page_count
    try:
        for page in doc:
            hits += len(page.search_for(term))
    finally:
        doc.close()
    return hits, pages


def _search_open_doc(path: Path, term: str) -> tuple[int, int]:
    doc = pymupdf.open(path)
    try:
        started = time.perf_counter_ns()
        hits = sum(len(page.search_for(term)) for page in doc)
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        return hits, elapsed_ms
    finally:
        doc.close()


def _page_ops(path: Path) -> dict[str, object]:
    page_count = _open_page_count(path)
    second = min(2, page_count)
    third = min(3, page_count)
    operations = [
        ("rotate-1", lambda state: state.add_rotate_pages([1], 90)),
        ("move-last", lambda state: state.add_move_pages([page_count], 0)),
        ("crop-1", lambda state: state.add_crop_pages([1], [24, 24, 571, 818])),
        ("rotate-2", lambda state: state.add_rotate_pages([second], 180)),
        ("move-3-after-1", lambda state: state.add_move_pages([third], 1)),
        ("rotate-last", lambda state: state.add_rotate_pages([page_count], 270)),
    ]
    state = PagePreviewState(path)
    timings: dict[str, float] = {}
    try:
        for name, operation in operations:
            elapsed, _ = _timed(lambda op=operation: op(state))
            timings[name] = elapsed
        final_count = state.page_count()
    finally:
        state.clear()
    return {"operation_ms": timings, "total_ms": sum(timings.values()), "final_page_count": final_count}


def _history_variant(data: bytes, step: int) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        page = doc[0]
        page.insert_text((54, 780 - (step % 40) * 8), f"history sample {step:03d}", fontsize=7)
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def _history_memory(path: Path, steps: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="omapreview-r23-history-") as temp_dir:
        work_path = Path(temp_dir) / path.name
        shutil.copyfile(path, work_path)
        gc.collect()
        tracemalloc.start()
        rss_before = _rss_bytes()
        max_rss_before = _max_rss_bytes()
        editor = Editor(str(work_path), None)
        current = work_path.read_bytes()
        for step in range(steps):
            before = current
            current = _history_variant(before, step)
            work_path.write_bytes(current)
            editor.record_save(before, [], [])
        current_traced, peak_traced = tracemalloc.get_traced_memory()
        rss_after = _rss_bytes()
        max_rss_after = _max_rss_bytes()
        stored_snapshot_bytes = sum(
            len(entry["file_before"]) + len(entry["file_after"])
            for entry in editor.undo_stack
            if entry.get("kind") == "save"
        )
        entry_count = len(editor.undo_stack)
        editor.close()
        tracemalloc.stop()
    return {
        "steps": steps,
        "entries": entry_count,
        "initial_file_bytes": path.stat().st_size,
        "stored_snapshot_bytes": stored_snapshot_bytes,
        "stored_snapshot_bytes_per_entry": stored_snapshot_bytes / max(entry_count, 1),
        "tracemalloc_current_bytes": current_traced,
        "tracemalloc_peak_bytes": peak_traced,
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
        "rss_delta_bytes": None if rss_before is None or rss_after is None else rss_after - rss_before,
        "max_rss_delta_bytes": max_rss_after - max_rss_before,
        "note": "stored_snapshot_bytes is a lower bound for history retention; RSS includes native PDF allocations",
    }


def _recorder_input(points: int) -> dict[str, object]:
    session = RecorderSession(click_mode=True, pad_size=(516.0, 336.0))
    session.mapper.absolute = True
    session.handle_space()
    latencies_us: list[float] = []
    captured = 0
    for index in range(points):
        phase = index / max(points - 1, 1)
        x = 22.0 + 465.0 * phase
        y = 128.0 + 72.0 * math.sin(phase * math.pi * 3.0)
        started = time.perf_counter_ns()
        if session.motion(x, y, button1=True, on_glass=True):
            captured += 1
        latencies_us.append((time.perf_counter_ns() - started) / 1_000)
    session.end_stroke()
    started = time.perf_counter_ns()
    svg = strokes_to_svg(session.strokes)
    svg_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "points_requested": points,
        "points_captured": captured,
        "point_callback_us": _stats(latencies_us),
        "svg_bytes": len(svg.encode("utf-8")),
        "svg_serialize_ms": svg_ms,
    }


def _isolated_cli_env(root: Path) -> dict[str, str]:
    runtime = root / "cli-runtime"
    (runtime / "config").mkdir(parents=True, exist_ok=True)
    (runtime / "signatures").mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(runtime / "config")
    env["OMEPREVIEW_SIGNATURE_DIR"] = str(runtime / "signatures")
    env["PYTHONPATH"] = os.pathsep.join([str(SRC), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return env


def _cli_snapshot(path: Path, root: Path) -> tuple[float, int, int]:
    output = root / f"{path.stem}-cli-snapshot.png"
    command = [
        sys.executable,
        "-m",
        "omepreview.cli",
        "snapshot",
        str(path),
        "--page",
        "1",
        "--scale",
        "1.0",
        "-o",
        str(output),
    ]
    started = time.perf_counter_ns()
    result = subprocess.run(command, cwd=ROOT, env=_isolated_cli_env(root), capture_output=True, text=True)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    if result.returncode != 0:
        raise RuntimeError(f"CLI snapshot failed: {result.stderr.strip()}")
    return elapsed_ms, result.returncode, output.stat().st_size


def _measure_fixture(
    fixture: Fixture,
    repetitions: int,
    history_steps: int,
    recorder_points: int,
    root: Path,
    profile: str,
) -> dict[str, object]:
    thumb_samples = _samples(lambda: _thumbnail_open_refresh(fixture.path), repetitions)
    thumb_open = [value[0] for _, value in thumb_samples]
    thumb_refresh = [value[1] for _, value in thumb_samples]
    thumb_png_bytes = [value[2] for _, value in thumb_samples]

    search_samples = _samples(lambda: _search(fixture.path, SEARCH_TERM), repetitions)
    search_with_open_samples = _samples(lambda: _search_open_doc(fixture.path, SEARCH_TERM), repetitions)
    ops_samples = (
        _samples(lambda: _page_ops(fixture.path), repetitions)
        if profile == "full"
        else []
    )
    cli_samples = _samples(lambda: _cli_snapshot(fixture.path, root), max(2, min(repetitions, 5)))

    op_names = list(ops_samples[0][1]["operation_ms"]) if ops_samples else []
    per_op = {
        name: _stats(value[1]["operation_ms"][name] for value in ops_samples)
        for name in op_names
    }
    search_hits = search_samples[-1][1][0]
    open_search_hits = search_with_open_samples[-1][1][0]
    return {
        "fixture": fixture.kind,
        "model_probe": {
            "thumbnail_open_ms": _stats(thumb_open),
            "thumbnail_refresh_ms": _stats(thumb_refresh),
            "thumbnail_refresh_png_bytes": _stats(thumb_png_bytes),
            "thumbnail_note": "includes two PyMuPDF rasterizations per page and PNG encoding; excludes Gdk.Texture construction and GTK layout",
            "search_open_and_scan_ms": _stats(sample[0] for sample in search_samples),
            "search_scan_only_ms": _stats(sample[1][1] for sample in search_with_open_samples),
            "search_hits": search_hits,
            "search_scan_pages": search_samples[-1][1][1],
            "search_hits_consistent": all(value[0] == search_hits for _, value in search_samples)
            and all(value[0] == open_search_hits for _, value in search_with_open_samples),
            "page_operation_sequence_total_ms": (
                _stats(sample[1]["total_ms"] for sample in ops_samples)
                if ops_samples
                else None
            ),
            "page_operation_ms": per_op,
            "page_operation_count": len(op_names),
            "page_operation_note": (
                "PagePreviewState.append_op rebuilds the scratch PDF after each operation"
                if ops_samples
                else "not run in the scaling profile; use the full profile for repeated page operations"
            ),
            "cli_snapshot_ms": _stats(sample[0] for sample in cli_samples),
            "cli_snapshot_output_bytes": cli_samples[-1][1][2],
            "cli_snapshot_command": "<venv-python> -m omepreview.cli snapshot <generated-pdf> --page 1 --scale 1.0 -o <generated-png>",
        },
        "history_memory": _history_memory(fixture.path, history_steps),
        "recorder_model_probe": _recorder_input(recorder_points),
        "profile": profile,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="directory for generated fixtures and JSON results")
    parser.add_argument("--pages", type=int, default=12, help="pages per generated fixture (default: 12)")
    parser.add_argument("--scanned-pages", type=int, help="page count for the scanned fixture (default: --pages)")
    parser.add_argument("--repetitions", type=int, default=5, help="timed repetitions per model probe (default: 5)")
    parser.add_argument("--history-steps", type=int, default=8, help="save snapshots retained for history probe (default: 8)")
    parser.add_argument("--recorder-points", type=int, default=800, help="synthetic recorder motion samples (default: 800)")
    parser.add_argument("--profile", choices=("full", "scaling"), default="full", help="full runs every probe; scaling omits repeated page operations for bounded large-document measurements")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    for name, value in (("pages", args.pages), ("repetitions", args.repetitions), ("history-steps", args.history_steps), ("recorder-points", args.recorder_points)):
        if value < 1:
            raise SystemExit(f"{name} must be at least 1")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    scanned_pages = args.pages if args.scanned_pages is None else args.scanned_pages
    if scanned_pages < 1:
        raise SystemExit("scanned-pages must be at least 1")
    text_fixture = _make_text_pdf(output / "r23-text.pdf", args.pages)
    scanned_fixture = _make_scanned_pdf(output / "r23-scanned.pdf", scanned_pages)
    fixtures = [text_fixture, scanned_fixture]
    started = datetime.now(UTC)
    results = {
        "schema": "r23-benchmark-v1",
        "started_utc": started.isoformat(),
        "finished_utc": None,
        "git_commit": _git_commit(),
        "environment": {
            "python": sys.version,
            "python_executable": sys.executable,
            "pymupdf_bind": pymupdf.VersionBind,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "kernel": platform.release(),
            "display_server": os.environ.get("XDG_SESSION_TYPE", "unknown"),
            "gdk_backend": os.environ.get("GDK_BACKEND", "unset"),
            "home_preserved": "HOME" in os.environ,
            "user_state_overrides": {
                "XDG_CONFIG_HOME": "benchmark output runtime only for CLI subprocesses",
                "OMEPREVIEW_SIGNATURE_DIR": "benchmark output runtime only for CLI subprocesses",
            },
        },
        "parameters": {
            "pages": args.pages,
            "repetitions": args.repetitions,
            "history_steps": args.history_steps,
            "recorder_points": args.recorder_points,
            "profile": args.profile,
            "scanned_pages": scanned_pages,
        },
        "fixtures": [_fixture_metadata(fixture) for fixture in fixtures],
        "results": [],
        "scope_limits": [
            "Model probes are not native GTK response times.",
            "CLI timings include process startup and output publication.",
            "MCP was not changed and is not benchmarked by this tool; repository MCP tests remain the applicability check.",
            "RSS includes native library allocations and is therefore directional; exact retained history bytes are reported separately.",
        ],
    }
    for fixture in fixtures:
        results["results"].append(
            _measure_fixture(
                fixture,
                args.repetitions,
                args.history_steps,
                args.recorder_points,
                output,
                args.profile,
            )
        )
    results["finished_utc"] = datetime.now(UTC).isoformat()
    result_path = output / "r23-results.json"
    result_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    _private(result_path)
    print(json.dumps({"result": str(result_path), "fixtures": [str(fixture.path) for fixture in fixtures], "git_commit": results["git_commit"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
