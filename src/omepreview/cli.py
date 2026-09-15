"""omepreview command-line interface.

Designed to be equally pleasant for humans and agents: every command that
reports state supports --json, errors are one actionable line on stderr, and
all edits go through the same op engine the MCP server uses.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__, engine, pages as pages_mod, read, signature
from .ops import (
    MAX_COORDINATE,
    MAX_DIMENSION,
    MAX_SIGNATURE_WIDTH,
    MAX_STROKE_WIDTH,
    OpError,
)
from .render import MAX_GRID_STEP, MAX_SNAPSHOT_SCALE, MIN_GRID_STEP, MIN_SNAPSHOT_SCALE


def _cli_float(
    value: str,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be a finite number — got {value!r}") from exc
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f"{label} must be finite — got {value!r}")
    if minimum is not None and (
        number <= minimum if strict_minimum else number < minimum
    ):
        relation = "greater than" if strict_minimum else "at least"
        raise argparse.ArgumentTypeError(
            f"{label} must be {relation} {minimum} — got {value!r}"
        )
    if maximum is not None and number > maximum:
        raise argparse.ArgumentTypeError(
            f"{label} must be at most {maximum} — got {value!r}"
        )
    return number


def _point(value: str) -> list[float]:
    try:
        x, y = value.split(",")
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected X,Y — got {value!r}")
    return [
        _cli_float(x, "X", minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE),
        _cli_float(y, "Y", minimum=-MAX_COORDINATE, maximum=MAX_COORDINATE),
    ]


def _rect(value: str) -> list[float]:
    parts = value.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"expected X0,Y0,X1,Y1 — got {value!r}")
    return [
        _cli_float(
            part,
            f"rectangle coordinate {i}",
            minimum=-MAX_COORDINATE,
            maximum=MAX_COORDINATE,
        )
        for i, part in enumerate(parts)
    ]


def _rgb(value: str) -> list[float]:
    parts = value.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"expected R,G,B in 0..1 — got {value!r}")
    return [
        _cli_float(part, f"RGB component {i}", minimum=0, maximum=1)
        for i, part in enumerate(parts)
    ]


def _signature_width(value: str) -> float:
    return _cli_float(
        value, "signature width", minimum=0, maximum=MAX_SIGNATURE_WIDTH, strict_minimum=True
    )


def _stroke_width(value: str) -> float:
    return _cli_float(
        value, "stroke width", minimum=0, maximum=MAX_STROKE_WIDTH, strict_minimum=True
    )


def _dimension(value: str) -> float:
    return _cli_float(
        value, "page dimension", minimum=0, maximum=MAX_DIMENSION, strict_minimum=True
    )


def _grid_step(value: str) -> float:
    return _cli_float(
        value, "grid step", minimum=MIN_GRID_STEP, maximum=MAX_GRID_STEP
    )


def _snapshot_scale(value: str) -> float:
    return _cli_float(
        value,
        "snapshot scale",
        minimum=MIN_SNAPSHOT_SCALE,
        maximum=MAX_SNAPSHOT_SCALE,
    )


def _emit(data, as_json: bool):
    if as_json:
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _emit_human(data)


def _emit_human(data):
    if isinstance(data, dict) and "applied" in data:
        for op in data["applied"]:
            label = op["op"]
            where = f"p{op['page']}" if "page" in op else ""
            detail = op.get("match") or op.get("field") or op.get("text") or op.get("signature") or ""
            print(f"  ✓ {label} {where} {detail}".rstrip())
        if data.get("output"):
            print(f"saved: {data['output']}")
        else:
            print("(dry run — nothing written)")
    else:
        json.dump(data, sys.stdout, indent=2)
        sys.stdout.write("\n")


def _out_args(p: argparse.ArgumentParser):
    p.add_argument("-o", "--output", help="write result here (default: edit in place)")
    p.add_argument("--dry-run", action="store_true", help="resolve and report, write nothing")
    p.add_argument("--json", action="store_true", help="machine-readable report")


def _run_edit(args, op_list):
    result = engine.apply(args.pdf, op_list, output=args.output, dry_run=args.dry_run)
    _emit(result, args.json)


def cmd_read(args):
    pages = [int(p) for p in args.page] if args.page else None
    data = read.extract(args.pdf, pages=pages, text_only=args.text_only)
    if args.text_only and not args.json:
        for page in data["pages"]:
            print(page["text"])
    else:
        _emit(data, True)


def cmd_fields(args):
    _emit(read.form_fields(args.pdf), True)


def cmd_apply(args):
    payload = json.load(sys.stdin) if args.ops == "-" else json.loads(Path(args.ops).read_text())
    _run_edit(args, payload)


def cmd_annotate(args):
    op = {"op": "highlight", "page": args.page, "style": args.style}
    if args.match:
        op["match"] = args.match
    else:
        op["rect"] = args.rect
    _run_edit(args, [op])


def cmd_note(args):
    _run_edit(args, [{"op": "note", "page": args.page, "at": args.at, "text": args.text}])


def cmd_fill(args):
    op_list = [{"op": "fill_field", "field": name, "value": value} for name, value in args.field]
    _run_edit(args, op_list)


def cmd_sign(args):
    op = {
        "op": "place_signature",
        "page": args.page,
        "at": args.at,
        "width": args.width,
        "signature": args.sig,
        "date": args.date,
    }
    _run_edit(args, [op])


def cmd_delete_annotation(args):
    _run_edit(
        args,
        [{"op": "delete_annotation", "page": args.page, "index": args.index}],
    )


def cmd_redact(args):
    op = {"op": "redact", "page": args.page}
    if args.match:
        op["match"] = args.match
    else:
        op["rect"] = args.rect
    if args.fill is not None:
        op["fill"] = args.fill
    _run_edit(args, [op])


def cmd_crop(args):
    if args.pages:
        pages = pages_mod.parse_page_ranges(args.pages)
    elif args.page:
        pages = [args.page]
    else:
        raise OpError("crop needs --page or --pages")
    _run_edit(
        args,
        [{"op": "crop_pages", "pages": pages, "rect": args.rect}],
    )


def cmd_shape(args):
    op = {"op": "shape", "page": args.page, "shape": args.shape}
    if args.shape in ("line", "arrow"):
        op["from"] = args.from_point
        op["to"] = args.to_point
    else:
        op["rect"] = args.rect
    if args.color:
        op["color"] = args.color
    if args.width is not None:
        op["width"] = args.width
    _run_edit(args, [op])


def cmd_flatten(args):
    result = engine.flatten(args.pdf, output=args.output)
    _emit(result, args.json)


def cmd_sig(args):
    if args.sig_cmd == "add":
        dest = signature.add(args.image, args.name)
        print(f"saved signature {args.name!r} -> {dest}")
    elif args.sig_cmd == "draw":
        from . import draw

        if draw.run_and_save(args.name, trackpad=not args.click):
            dest = signature.path_for(args.name)
            print(f"saved signature {args.name!r} -> {dest}")
        else:
            print("cancelled — no signature saved", file=sys.stderr)
            raise SystemExit(1)
    elif args.sig_cmd == "list":
        names = signature.list_names()
        print(
            "\n".join(names)
            if names
            else (
                "(no signatures saved — omepreview sig draw writes "
                "~/Downloads/omapreview/signature/)"
            )
        )
    elif args.sig_cmd == "remove":
        signature.remove(args.name)
        print(f"removed signature {args.name!r}")


def cmd_open(args):
    # Opening a PDF means the omepreview editor. OMEPREVIEW_VIEWER (legacy
    # OMAPDF_VIEWER) forces an external viewer instead — but never xdg-open:
    # omepreview may itself be the desktop's default PDF handler, and
    # xdg-open would loop straight back to us.
    override = os.environ.get("OMEPREVIEW_VIEWER") or os.environ.get("OMAPDF_VIEWER")
    if override:
        cmd = [*shlex.split(override), args.pdf]
    else:
        cmd = [sys.executable, "-m", "omepreview.cli", "edit", args.pdf]
    subprocess.Popen(cmd, start_new_session=True)


def cmd_edit(args):
    from . import gui

    pdf = (args.pdf or "").strip() or None
    raise SystemExit(gui.run(pdf, args.ops))


def cmd_pages(args):
    if args.list:
        _emit(pages_mod.list_pages(args.pdf), args.json or True)
        return

    op_list: list[dict] = []
    if args.delete:
        op_list.append(
            {"op": "delete_pages", "pages": pages_mod.parse_page_ranges(args.delete)}
        )
    if args.rotate is not None:
        if not args.pages:
            raise OpError("--rotate requires --pages")
        op_list.append(
            {
                "op": "rotate_pages",
                "pages": pages_mod.parse_page_ranges(args.pages),
                "degrees": args.rotate,
            }
        )
    if args.move:
        if args.after is None:
            raise OpError("--move requires --after")
        op_list.append(
            {
                "op": "move_pages",
                "pages": pages_mod.parse_page_ranges(args.move),
                "after": args.after,
            }
        )
    if args.insert or args.blank or args.image:
        if args.after is None:
            raise OpError("--insert/--blank/--image requires --after")
        op: dict = {"op": "insert_pages", "after": args.after}
        if args.blank:
            op["blank"] = {
                "count": args.blank_count,
                "width": args.blank_width,
                "height": args.blank_height,
            }
        elif args.image:
            op["image"] = args.image
        else:
            op["source"] = args.insert
            if args.src_pages:
                op["source_pages"] = pages_mod.parse_page_ranges(args.src_pages)
        op_list.append(op)
    if args.extract:
        if not args.output:
            raise OpError("--extract requires -o/--output for the excerpt path")
        op_list.append(
            {
                "op": "extract_pages",
                "pages": pages_mod.parse_page_ranges(args.extract),
                "to": args.output,
            }
        )

    if not op_list:
        raise OpError(
            "specify an action: --list, --delete, --rotate, --move, --insert, "
            "--blank, --image, or --extract"
        )

    extract_only = len(op_list) == 1 and op_list[0]["op"] == "extract_pages"
    output = None if extract_only else args.output
    result = engine.apply(args.pdf, op_list, output=output, dry_run=args.dry_run)
    _emit(result, args.json)


def cmd_snapshot(args):
    from . import render

    result = render.snapshot(
        args.pdf, page=args.page, output=args.output, grid=args.grid, scale=args.scale
    )
    _emit(result, True)


def _cli_prog() -> str:
    name = Path(sys.argv[0]).name if sys.argv else "omepreview"
    if name.startswith("omapreview"):
        return "omapreview"
    return "omepreview"


def build_parser() -> argparse.ArgumentParser:
    prog = _cli_prog()
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Agent-native PDF annotation and signing.",
    )
    parser.add_argument("--version", action="version", version=f"{prog} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("read", help="structured document read (JSON)")
    p.add_argument("pdf")
    p.add_argument("--page", action="append", help="limit to page N (repeatable)")
    p.add_argument("--text-only", action="store_true", help="plain text instead of layout")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("fields", help="list form fields (JSON)")
    p.add_argument("pdf")
    p.set_defaults(func=cmd_fields)

    p = sub.add_parser("apply", help="apply an ops JSON file (or - for stdin)")
    p.add_argument("pdf")
    p.add_argument("--ops", required=True, help="path to ops JSON, or - for stdin")
    _out_args(p)
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("annotate", help="highlight/underline/strikeout text")
    p.add_argument("pdf")
    p.add_argument("--page", type=int, required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--match", help="highlight every occurrence of this text")
    group.add_argument("--rect", type=_rect, help="X0,Y0,X1,Y1 in points, top-left origin")
    p.add_argument("--style", choices=["highlight", "underline", "strikeout", "squiggly"], default="highlight")
    _out_args(p)
    p.set_defaults(func=cmd_annotate)

    p = sub.add_parser("note", help="attach a sticky-note comment")
    p.add_argument("pdf")
    p.add_argument("--page", type=int, required=True)
    p.add_argument("--at", type=_point, required=True, help="X,Y in points")
    p.add_argument("--text", required=True)
    _out_args(p)
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("fill", help="fill form fields")
    p.add_argument("pdf")
    p.add_argument("--field", nargs=2, action="append", metavar=("NAME", "VALUE"), required=True)
    _out_args(p)
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser("sign", help="place a saved signature image")
    p.add_argument("pdf")
    p.add_argument("--page", type=int, required=True)
    p.add_argument("--at", type=_point, required=True, help="top-left X,Y in points")
    p.add_argument("--width", type=_signature_width, default=180)
    p.add_argument("--sig", default="default", help="saved signature name")
    p.add_argument("--date", action="store_true", help="stamp today's date below")
    _out_args(p)
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser(
        "delete-annotation",
        help="remove an annotation by page and 0-based index from omepreview read",
    )
    p.add_argument("pdf")
    p.add_argument("--page", type=int, required=True)
    p.add_argument("--index", type=int, required=True, help="0-based annotation index on the page")
    _out_args(p)
    p.set_defaults(func=cmd_delete_annotation)

    p = sub.add_parser("redact", help="permanently remove text or image pixels in a region")
    p.add_argument("pdf")
    p.add_argument("--page", type=int, required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--match", help="redact every occurrence of this text on the page")
    group.add_argument("--rect", type=_rect, help="X0,Y0,X1,Y1 in points, top-left origin")
    p.add_argument(
        "--fill",
        type=_rgb,
        help="optional fill RGB as R,G,B in 0..1 (default 0,0,0)",
    )
    _out_args(p)
    p.set_defaults(func=cmd_redact)

    p = sub.add_parser("crop", help="set page CropBox (visible region) for one or more pages")
    p.add_argument("pdf")
    p.add_argument("--page", type=int, help="single page to crop (1-based)")
    p.add_argument("--pages", metavar="PAGES", help="page list/ranges, e.g. 1,3-4")
    p.add_argument(
        "--rect",
        type=_rect,
        required=True,
        help="crop region X0,Y0,X1,Y1 in current page points (top-left origin)",
    )
    _out_args(p)
    p.set_defaults(func=cmd_crop)

    p = sub.add_parser("shape", help="add line, arrow, rectangle, or oval annotation")
    p.add_argument("pdf")
    p.add_argument("--page", type=int, required=True)
    p.add_argument(
        "--shape",
        choices=["line", "arrow", "rect", "oval"],
        required=True,
        help="annotation shape type",
    )
    p.add_argument(
        "--from",
        dest="from_point",
        type=_point,
        help="start X,Y for line or arrow",
    )
    p.add_argument(
        "--to",
        dest="to_point",
        type=_point,
        help="end X,Y for line or arrow",
    )
    p.add_argument("--rect", type=_rect, help="X0,Y0,X1,Y1 for rect or oval")
    p.add_argument("--color", type=_rgb, help="stroke RGB as R,G,B in 0..1 (default black)")
    p.add_argument("--width", type=_stroke_width, help="stroke width in points (default 2)")
    _out_args(p)
    p.set_defaults(func=cmd_shape)

    p = sub.add_parser(
        "flatten",
        help="bake annotations and form fields into the page (refuses pending redactions)",
    )
    p.add_argument("pdf")
    p.add_argument("-o", "--output")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_flatten)

    p = sub.add_parser("sig", help="manage saved signatures")
    sig_sub = p.add_subparsers(dest="sig_cmd", required=True)
    sp = sig_sub.add_parser(
        "add",
        help="save a signature SVG (or PNG to import) to ~/Downloads/omapreview/signature/",
    )
    sp.add_argument("image")
    sp.add_argument("--name", default="default")
    sp = sig_sub.add_parser(
        "draw",
        help=(
            "record a signature (Space to start, finger-on-pad abs mapping, "
            "Enter saves SVG to ~/Downloads/omapreview/signature/; --click for mouse)"
        ),
    )
    sp.add_argument("--name", default="default")
    sp.add_argument(
        "--click",
        action="store_true",
        help="require mouse click-and-drag instead of trackpad finger-glide",
    )
    sig_sub.add_parser("list", help="list saved signatures")
    sp = sig_sub.add_parser("remove", help="delete a saved signature")
    sp.add_argument("name")
    p.set_defaults(func=cmd_sig)

    p = sub.add_parser("open", help="open in the desktop PDF viewer")
    p.add_argument("pdf")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("edit", help="open the omepreview editor (annotate, sign, drag)")
    p.add_argument(
        "pdf",
        nargs="?",
        help="PDF to open (omit for an empty editor; Open from the app)",
    )
    p.add_argument("--ops", help="ops JSON to load as draggable proposals")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("pages", help="list, delete, rotate, move, insert, or extract pages")
    p.add_argument("pdf")
    p.add_argument("--list", action="store_true", help="list pages (JSON)")
    p.add_argument("--delete", metavar="PAGES", help="delete pages, e.g. 1,4-6")
    p.add_argument("--rotate", type=int, choices=[90, 180, 270, -90], help="rotate degrees")
    p.add_argument("--pages", metavar="PAGES", help="pages for --rotate, e.g. 2,3")
    p.add_argument("--move", metavar="PAGES", help="pages to move, e.g. 5-6")
    p.add_argument("--after", type=int, help="insert/move position (0 = beginning)")
    p.add_argument("--insert", metavar="PDF", help="insert pages from another PDF")
    p.add_argument("--src-pages", metavar="PAGES", help="source pages for --insert")
    p.add_argument("--blank", action="store_true", help="insert blank page(s)")
    p.add_argument("--blank-count", type=int, default=1, help="blank pages to insert")
    p.add_argument("--blank-width", type=_dimension, default=595)
    p.add_argument("--blank-height", type=_dimension, default=842)
    p.add_argument("--image", metavar="PATH", help="insert an image as a new page")
    p.add_argument("--extract", metavar="PAGES", help="extract pages to -o path")
    _out_args(p)
    p.set_defaults(func=cmd_pages)

    p = sub.add_parser("snapshot", help="render a page to PNG (with optional coordinate grid)")
    p.add_argument("pdf")
    p.add_argument("--page", type=int, default=1)
    p.add_argument(
        "--grid",
        type=_grid_step,
        help=f"overlay labeled grid lines every N points ({MIN_GRID_STEP:g}..{MAX_GRID_STEP:g})",
    )
    p.add_argument(
        "--scale",
        type=_snapshot_scale,
        default=2.0,
        help=f"raster scale ({MIN_SNAPSHOT_SCALE:g}..{MAX_SNAPSHOT_SCALE:g}; 2 = 144 dpi)",
    )
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_snapshot)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (OpError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"omepreview: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
