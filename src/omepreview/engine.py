"""Apply operation lists to PDFs.

This is the single write-path for the whole project: the CLI, the MCP server,
and (eventually) the GUI all funnel edits through apply(). Every applied op is
echoed back in a report with its resolved geometry, so callers — human or
agent — can show exactly what changed and where.
"""

from __future__ import annotations

import datetime
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pymupdf

from . import ops as ops_mod
from . import redact_scope
from . import signature as sig_store
from .fs_privacy import chmod_private_file
from .ops import OpError


def _page(doc: pymupdf.Document, number: int) -> pymupdf.Page:
    if number < 1 or number > doc.page_count:
        raise OpError(f"page {number} out of range (document has {doc.page_count})")
    return doc[number - 1]


@contextmanager
def _unrotated_content_write(page: pymupdf.Page) -> Iterator[None]:
    """Keep content writers in the public unrotated, crop-local frame.

    PyMuPDF content insertion and redaction fill can offset cropped pages when
    /Rotate is nonzero. Annotation geometry and extracted text already use
    the unrotated frame. Clear only rotation while writing content; never
    change boxes or compensate with a hard-coded crop offset.
    """
    rotation = page.rotation
    try:
        if rotation:
            page.set_rotation(0)
        yield
    finally:
        if rotation:
            page.set_rotation(rotation)


def _apply_highlight(doc, op) -> dict:
    page = _page(doc, op["page"])
    if "match" in op:
        rects = page.search_for(op["match"])
        if not rects:
            raise OpError(
                f"text {op['match']!r} not found on page {op['page']}; "
                "run `omepreview read` to see the page's actual text"
            )
    else:
        rects = [pymupdf.Rect(op["rect"])]

    add = {
        "highlight": page.add_highlight_annot,
        "underline": page.add_underline_annot,
        "strikeout": page.add_strikeout_annot,
        "squiggly": page.add_squiggly_annot,
    }[op["style"]]
    for rect in rects:
        annot = add(rect)
        annot.update()
    return {"rects": [list(r) for r in rects]}


def _apply_note(doc, op) -> dict:
    page = _page(doc, op["page"])
    annot = page.add_text_annot(pymupdf.Point(op["at"]), op["text"])
    annot.update()
    return {"at": op["at"]}


def _apply_text_box(doc, op) -> dict:
    page = _page(doc, op["page"])
    rect = pymupdf.Rect(op["rect"])
    annot = page.add_freetext_annot(
        rect, op["text"], fontsize=op["size"], fontname="helv", text_color=(0, 0, 0)
    )
    annot.update()
    return {"rect": list(rect)}


def _apply_fill_field(doc, op) -> dict:
    name, value = op["field"], op["value"]
    for page in doc:
        for widget in page.widgets():
            if widget.field_name != name:
                continue
            if widget.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
                widget.field_value = str(value).lower() in ("true", "yes", "on", "1")
            else:
                widget.field_value = str(value)
            widget.update()
            return {"field": name, "page": page.number + 1}
    known = sorted(
        w.field_name for p in doc for w in p.widgets() if w.field_name
    )
    raise OpError(
        f"no form field named {name!r}. Fields in this document: "
        f"{', '.join(known) if known else '(none — this PDF has no form)'}"
    )


def _apply_place_signature(doc, op) -> dict:
    page = _page(doc, op["page"])
    image = sig_store.get(op["signature"])
    try:
        ratio = sig_store.aspect_ratio(image)
    except Exception as exc:
        raise OpError(f"signature {image} could not be read: {exc}") from exc
    if ratio <= 0:
        raise OpError(f"signature image {image} is empty")
    width = op["width"]
    height = width * ratio
    x, y = op["at"]
    rect = pymupdf.Rect(x, y, x + width, y + height)
    result = {"rect": list(rect), "signature": op["signature"]}
    with _unrotated_content_write(page):
        sig_store.insert_on_page(page, rect, image)
        if op["date"]:
            date_str = datetime.date.today().isoformat()
            page.insert_text(
                pymupdf.Point(x, y + height + 12), date_str, fontsize=10, fontname="helv"
            )
            result["date"] = date_str
    return result


def _apply_ink(doc, op) -> dict:
    page = _page(doc, op["page"])
    annot = page.add_ink_annot(op["strokes"])
    annot.set_colors(stroke=op["color"])
    annot.set_border(width=op["width"])
    annot.update()
    return {"rect": list(annot.rect)}


def _apply_shape(doc, op) -> dict:
    page = _page(doc, op["page"])
    shape = op["shape"]
    if shape in ("line", "arrow"):
        p1 = pymupdf.Point(op["from"])
        p2 = pymupdf.Point(op["to"])
        annot = page.add_line_annot(p1, p2)
        if shape == "arrow":
            annot.set_line_ends(
                pymupdf.PDF_ANNOT_LE_NONE,
                pymupdf.PDF_ANNOT_LE_CLOSED_ARROW,
            )
        report = {"shape": shape, "from": op["from"], "to": op["to"]}
    else:
        rect = pymupdf.Rect(op["rect"])
        if shape == "rect":
            annot = page.add_rect_annot(rect)
        else:
            annot = page.add_circle_annot(rect)
        report = {"shape": shape, "rect": list(rect)}
    annot.set_colors(stroke=op["color"])
    annot.set_border(width=op["width"])
    annot.update()
    report["rect"] = list(annot.rect)
    return report


def _validate_page_indices(doc: pymupdf.Document, pages: list[int], label: str = "page") -> None:
    for p in pages:
        if p < 1 or p > doc.page_count:
            raise OpError(
                f"{label} {p} out of range (document has {doc.page_count} pages)"
            )


def _normalize_rotation(degrees: int) -> int:
    return degrees % 360 if degrees >= 0 else (360 + degrees) % 360


def _crop_rect_to_absolute(page: pymupdf.Page, rect: list[float]) -> pymupdf.Rect:
    """Map a crop rect in current page (CropBox) space to absolute PDF coordinates."""
    user = pymupdf.Rect(rect)
    base = page.cropbox
    abs_rect = pymupdf.Rect(
        base.x0 + user.x0,
        base.y0 + user.y0,
        base.x0 + user.x1,
        base.y0 + user.y1,
    )
    clipped = abs_rect & page.mediabox
    if clipped.is_empty or clipped.width < 1 or clipped.height < 1:
        raise OpError(
            f"crop_pages rect {rect} is empty or outside the page mediabox "
            f"(page size {page.rect.width:.0f}×{page.rect.height:.0f} pt)"
        )
    return clipped


def _apply_crop_pages(doc, op) -> dict:
    pages = op["pages"]
    _validate_page_indices(doc, pages)
    resolved: list[dict] = []
    for p in pages:
        page = doc[p - 1]
        before = [page.rect.width, page.rect.height]
        abs_rect = _crop_rect_to_absolute(page, op["rect"])
        page.set_cropbox(abs_rect)
        after = [page.rect.width, page.rect.height]
        resolved.append({
            "page": p,
            "cropbox": list(abs_rect),
            "size_before": before,
            "size_after": after,
        })
    return {"pages": pages, "rect": op["rect"], "resolved": resolved}


def _apply_rotate_pages(doc, op) -> dict:
    pages = op["pages"]
    _validate_page_indices(doc, pages)
    delta = _normalize_rotation(op["degrees"])
    for p in pages:
        page = doc[p - 1]
        page.set_rotation((page.rotation + delta) % 360)
    return {"pages": pages, "degrees": op["degrees"]}


def _apply_delete_pages(doc, op) -> dict:
    pages = sorted(set(op["pages"]), reverse=True)
    _validate_page_indices(doc, pages)
    for p in pages:
        doc.delete_page(p - 1)
    return {"pages": op["pages"]}


def _apply_move_pages(doc, op) -> dict:
    pages_to_move = op["pages"]
    after = op["after"]
    n = doc.page_count
    _validate_page_indices(doc, pages_to_move)
    if after != 0 and (after < 1 or after > n):
        raise OpError(f"after {after} out of range (document has {n} pages)")
    move_set = set(pages_to_move)
    if len(move_set) != len(pages_to_move):
        raise OpError("move_pages pages must be unique")
    remaining = [p for p in range(1, n + 1) if p not in move_set]
    if after == 0:
        new_order = list(pages_to_move) + remaining
    else:
        if after not in remaining:
            raise OpError(f"after page {after} is among the pages being moved")
        insert_at = remaining.index(after) + 1
        new_order = remaining[:insert_at] + list(pages_to_move) + remaining[insert_at:]
    doc.select([p - 1 for p in new_order])
    return {"pages": pages_to_move, "after": after}


def _insert_count(op: dict) -> int:
    if "blank" in op:
        return op["blank"]["count"]
    if "image" in op:
        return 1
    src = Path(op["source"])
    if not src.is_file():
        raise FileNotFoundError(f"no such PDF: {src}")
    src_doc = pymupdf.open(str(src))
    try:
        if op.get("source_pages"):
            _validate_page_indices(src_doc, op["source_pages"], "source page")
            return len(op["source_pages"])
        return src_doc.page_count
    finally:
        src_doc.close()


def _apply_insert_pages(doc, op) -> dict:
    after = op["after"]
    if after > doc.page_count:
        raise OpError(
            f"after {after} out of range (document has {doc.page_count} pages)"
        )
    start_at = after  # 0-based insertion index

    if "blank" in op:
        blank = op["blank"]
        inserted = 0
        for i in range(blank["count"]):
            doc.new_page(
                pno=start_at + i,
                width=blank["width"],
                height=blank["height"],
            )
            inserted += 1
        return {"after": after, "inserted": inserted, "blank": blank}

    if "image" in op:
        image = Path(op["image"])
        if not image.is_file():
            raise FileNotFoundError(f"no such image: {image}")
        pix = pymupdf.Pixmap(str(image))
        if pix.width == 0 or pix.height == 0:
            raise OpError(f"image {image} is empty")
        if after >= 1:
            ref = doc[after - 1]
            width, height = ref.rect.width, ref.rect.height
        else:
            width, height = float(pix.width), float(pix.height)
        page = doc.new_page(pno=start_at, width=width, height=height)
        page.insert_image(page.rect, filename=str(image), keep_proportion=True)
        return {"after": after, "inserted": 1, "image": str(image)}

    source = Path(op["source"])
    if not source.is_file():
        raise FileNotFoundError(f"no such PDF: {source}")
    src_doc = pymupdf.open(str(source))
    try:
        if src_doc.needs_pass:
            raise OpError(f"{source} is password-protected; decrypt it first")
        source_pages = op.get("source_pages") or list(range(1, src_doc.page_count + 1))
        _validate_page_indices(src_doc, source_pages, "source page")
        for i, sp in enumerate(source_pages):
            doc.insert_pdf(
                src_doc,
                from_page=sp - 1,
                to_page=sp - 1,
                start_at=start_at + i,
            )
        return {
            "after": after,
            "inserted": len(source_pages),
            "source": str(source),
            "source_pages": source_pages,
        }
    finally:
        src_doc.close()


def _verify_redact(page: pymupdf.Page, match: str | None, rects: list[pymupdf.Rect]) -> dict:
    remnant = redact_scope.leftover_text_detail(page, match, rects)
    leftovers = redact_scope.intersecting_payloads(page, rects)
    return {
        "text_still_present": remnant is not None,
        "text_remnant": remnant,
        "payloads_still_present": leftovers,
    }


def _verify_redact_ops_serialized(doc: pymupdf.Document, applied: list[dict]) -> None:
    """Reopen a garbage-collected serialization so leftover annot payloads fail closed."""
    redact_ops = [
        op
        for op in applied
        if op.get("op") == "redact"
        and op.get("apply_now", True)
        and op.get("applied")
    ]
    if not redact_ops:
        return
    blob = doc.tobytes(garbage=3, deflate=True)
    probe = pymupdf.open("pdf", blob)
    try:
        for op in redact_ops:
            redact_scope.verify_serialized(probe, op)
    finally:
        probe.close()


def _apply_delete_annotation(doc, op, *, dry_run: bool) -> dict:
    page = _page(doc, op["page"])
    annots = list(page.annots() or [])
    index = op["index"]
    if index >= len(annots):
        raise OpError(
            f"annotation index {index} out of range on page {op['page']} "
            f"(page has {len(annots)} annotation(s); run `omepreview read` to list them)"
        )
    annot = annots[index]
    result = {
        "index": index,
        "type": annot.type[1],
        "rect": list(annot.rect),
    }
    if dry_run:
        return result
    page.delete_annot(annot)
    return result


def _redact_annots(page) -> list:
    return [
        annot
        for annot in (page.annots() or [])
        if annot.type[0] == pymupdf.PDF_ANNOT_REDACT
    ]


def _apply_redact(doc, op, *, dry_run: bool) -> dict:
    page = _page(doc, op["page"])
    fill = tuple(op.get("fill", [0, 0, 0]))
    apply_now = op.get("apply_now", True)

    if "match" in op:
        match = op["match"]
        rects = page.search_for(match)
        if not rects:
            raise OpError(
                f"text {match!r} not found on page {op['page']}; "
                "run `omepreview read` to see the page's actual text"
            )
        verify_match = match
    else:
        rects = [pymupdf.Rect(op["rect"])]
        verify_match = None

    rects = redact_scope.expand_rects_to_glyphs(page, rects)
    result = {"rects": [list(r) for r in rects]}

    if dry_run:
        return result

    if apply_now:
        pending = _redact_annots(page)
        if pending:
            raise OpError(
                f"page {op['page']} already has {len(pending)} pending "
                "redaction annotation(s); apply or delete them first so this "
                "redact does not also remove unapproved regions"
            )
        redact_scope.fail_closed_if_unsupported(page, rects, page_no=op["page"])
        redact_scope.strip_known_in_rects(page, rects)

    if not apply_now:
        for rect in rects:
            page.add_redact_annot(rect, fill=fill)
        return result

    with _unrotated_content_write(page):
        for rect in rects:
            page.add_redact_annot(rect, fill=fill)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)

    verify = _verify_redact(page, verify_match, rects)
    result["verify"] = verify
    if verify["text_still_present"]:
        remnant = verify.get("text_remnant") or (
            "text still present after redaction — widen the region or "
            "check for overlapping content"
        )
        raise OpError(f"redact verify failed on page {op['page']}: {remnant}")
    leftovers = verify.get("payloads_still_present") or []
    if leftovers:
        raise OpError(
            f"redact verify failed on page {op['page']}: payloads still present "
            f"in the region: {', '.join(leftovers)}"
        )
    return result


def _stage_output_path(destination: Path) -> Path:
    """Create a private sibling that can later be atomically published."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(fd)
    staged = Path(name)
    try:
        chmod_private_file(staged)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _publish_staged(staged: Path, destination: Path) -> None:
    """Publish one completed output without opening the destination for write."""
    os.replace(staged, destination)


def _stage_extract_pages(doc, op) -> tuple[dict, Path]:
    pages = op["pages"]
    _validate_page_indices(doc, pages)
    dest = Path(op["to"]).expanduser().absolute()
    display_dest = str(Path(op["to"]))
    staged = _stage_output_path(dest)
    out_doc = pymupdf.open()
    try:
        for p in pages:
            out_doc.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
        out_doc.save(str(staged), garbage=3, deflate=True)
        chmod_private_file(staged)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    finally:
        out_doc.close()
    return {"pages": pages, "to": display_dest}, staged


def _apply_extract_pages(doc, op, *, dry_run: bool) -> dict:
    pages = op["pages"]
    _validate_page_indices(doc, pages)
    dest = Path(op["to"]).expanduser().absolute()
    if dry_run:
        return {"pages": pages, "to": str(Path(op["to"]))}
    resolution, staged = _stage_extract_pages(doc, op)
    try:
        _publish_staged(staged, dest)
    finally:
        staged.unlink(missing_ok=True)
    return resolution


def _project_page_count(doc: pymupdf.Document, ops: list[dict]) -> int:
    count = doc.page_count
    for op in ops:
        kind = op["op"]
        if kind == "delete_pages":
            count -= len(set(op["pages"]))
        elif kind == "insert_pages":
            count += _insert_count(op)
    return count


def _ensure_pages_remain(doc: pymupdf.Document, ops: list[dict]) -> None:
    if _project_page_count(doc, ops) < 1:
        raise OpError(
            "cannot delete every page; the document must keep at least one page "
            "(use insert_pages in the same batch to replace removed pages)"
        )


_APPLIERS = {
    "highlight": _apply_highlight,
    "note": _apply_note,
    "text_box": _apply_text_box,
    "fill_field": _apply_fill_field,
    "place_signature": _apply_place_signature,
    "ink": _apply_ink,
    "shape": _apply_shape,
    "crop_pages": _apply_crop_pages,
    "rotate_pages": _apply_rotate_pages,
    "delete_pages": _apply_delete_pages,
    "move_pages": _apply_move_pages,
    "insert_pages": _apply_insert_pages,
}


def _stage_save(doc: pymupdf.Document, output: Path) -> Path:
    """Serialize a document to a private sibling without publishing it."""
    staged = _stage_output_path(output)
    try:
        # PDF_ENCRYPT_KEEP preserves the input's owner/user password and
        # permission bits, including an empty user password.  It is also a
        # no-op for an unencrypted input.
        doc.save(
            str(staged),
            garbage=3,
            deflate=True,
            encryption=pymupdf.PDF_ENCRYPT_KEEP,
            permissions=doc.permissions,
        )
        chmod_private_file(staged)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _save(doc: pymupdf.Document, source: Path, output: Path) -> None:
    # PyMuPDF cannot do a full (garbage-collected) save over the file it has
    # open, so route every save through a sibling temp file.  The source arg
    # remains part of this helper's internal API for callers that distinguish
    # an in-place save from a copy.  Do not replace a different directory entry
    # that happens to refer to the source (for example, a hard link).
    if _same_file_or_path(source, output) and source != output:
        raise OpError(
            f"output {output} aliases source {source}; use the source path "
            "for an in-place save or choose a separate output"
        )
    staged: Path | None = None
    try:
        staged = _stage_save(doc, output)
        if not doc.is_closed:
            doc.close()
    except BaseException:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise
    try:
        _publish_staged(staged, output)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def _absolute_path(value: str | Path) -> Path:
    # Normalize only lexical components.  Keep symlinks unresolved so the
    # same-file checks can reject a different directory entry safely.
    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _canonical_path(value: str | Path) -> Path:
    return _absolute_path(value).resolve(strict=False)


def _same_file_or_path(first: Path, second: Path) -> bool:
    if _canonical_path(first) == _canonical_path(second):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _plan_outputs(
    source: Path,
    operations: list[dict],
    output: Path,
    *,
    explicit_output: bool,
    save_main: bool,
) -> list[Path]:
    """Validate all output relationships before opening a writable target."""
    extraction_targets: list[Path] = []
    for op in operations:
        if op["op"] != "extract_pages":
            continue
        target = _absolute_path(op["to"])
        if _same_file_or_path(source, target):
            raise OpError(
                f"extract_pages destination {target} aliases the source {source}; "
                "choose a different output file"
            )
        if any(_same_file_or_path(target, previous) for previous in extraction_targets):
            raise OpError(
                f"extract_pages destination {target} duplicates another extraction "
                "target; choose one destination per extraction"
            )
        extraction_targets.append(target)

    if save_main and explicit_output:
        # In-place output is the supported explicit alias.  A hard link or a
        # different symlink/path to the source would replace an unexpected
        # directory entry, so reject it before any document work starts.
        if _same_file_or_path(source, output) and source != output:
            raise OpError(
                f"output {output} aliases source {source}; use the source path "
                "for an in-place save or choose a separate output"
            )

    if save_main:
        for target in extraction_targets:
            if _same_file_or_path(output, target):
                raise OpError(
                    f"output {output} collides with extraction destination {target}; "
                    "choose separate paths"
                )
    return extraction_targets


def _order_ops(ops: list[dict]) -> list[dict]:
    """Delete annotations high-to-low per page so indices stay valid in a batch."""
    deletes = [op for op in ops if op["op"] == "delete_annotation"]
    if not deletes or len(deletes) == 1:
        return ops
    rest = [op for op in ops if op["op"] != "delete_annotation"]
    deletes.sort(key=lambda op: (op["page"], op["index"]), reverse=True)
    return rest + deletes


def apply(
    pdf: str | Path,
    op_list: list[dict],
    output: str | Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Validate and apply ops to `pdf`, writing `output` (default: in place).

    Returns {"output": path|None, "applied": [op ⊕ resolution, ...]}.
    With dry_run=True nothing is written; the report still resolves geometry
    (text matches, signature rects) so callers can preview placements.
    """
    input_path = _absolute_path(pdf)
    if not input_path.is_file():
        raise FileNotFoundError(f"no such PDF: {input_path}")
    pdf = input_path.resolve()
    validated = _order_ops(ops_mod.validate_all(op_list))
    explicit_output = output is not None
    main_output = _absolute_path(output) if explicit_output else pdf
    output_display = (
        str(Path(output).expanduser()) if explicit_output else str(input_path)
    )
    extract_only = bool(validated) and all(
        op["op"] == "extract_pages" for op in validated
    )
    save_main = not (extract_only and not explicit_output)
    extraction_targets = _plan_outputs(
        pdf,
        validated,
        main_output,
        explicit_output=explicit_output,
        save_main=save_main,
    )

    doc = pymupdf.open(str(pdf))
    staged_extractions: list[tuple[Path, Path]] = []
    staged_main: Path | None = None
    try:
        if doc.needs_pass:
            raise OpError(f"{pdf} is password-protected; decrypt it first")
        if extraction_targets and doc.metadata.get("encryption"):
            raise OpError(
                "extract_pages cannot preserve the source PDF's encryption and "
                "permissions; decrypt the source before extracting"
            )
        _ensure_pages_remain(doc, validated)
        applied = []
        for op in validated:
            if op["op"] == "extract_pages":
                if dry_run:
                    resolution = _apply_extract_pages(doc, op, dry_run=True)
                else:
                    resolution, staged = _stage_extract_pages(doc, op)
                    staged_extractions.append(
                        (Path(op["to"]).expanduser().absolute(), staged)
                    )
            elif op["op"] == "redact":
                resolution = _apply_redact(doc, op, dry_run=dry_run)
            elif op["op"] == "delete_annotation":
                resolution = _apply_delete_annotation(doc, op, dry_run=dry_run)
            else:
                resolution = _APPLIERS[op["op"]](doc, op)
            applied.append({**op, **resolution, "applied": not dry_run})
        if dry_run:
            doc.close()
            return {"output": None, "applied": applied}
        _verify_redact_ops_serialized(doc, applied)
        if save_main:
            staged_main = _stage_save(doc, main_output)
        doc.close()
        # Publish excerpts before the main document so a later main-output
        # failure cannot leave an in-place source half-committed.  Several
        # independent renames cannot form one filesystem transaction; the
        # operation stage is all-or-nothing, while publication is best-effort
        # across those separate destination paths.
        for destination, staged in staged_extractions:
            _publish_staged(staged, destination)
        if staged_main is not None:
            _publish_staged(staged_main, main_output)
    except BaseException:
        if not doc.is_closed:
            doc.close()
        for _destination, staged in staged_extractions:
            staged.unlink(missing_ok=True)
        if staged_main is not None:
            staged_main.unlink(missing_ok=True)
        raise
    return {"output": output_display if save_main else None, "applied": applied}


def _pages_with_redact_annots(doc: pymupdf.Document) -> list[int]:
    """1-based pages that still carry unapplied PDF redaction annotations."""
    pages: list[int] = []
    for i in range(doc.page_count):
        if _redact_annots(doc[i]):
            pages.append(i + 1)
    return pages


def flatten(pdf: str | Path, output: str | Path | None = None) -> dict:
    """Bake annotations and form fields into page content.

    Use before sending to recipients whose viewers mishandle annotations, or
    to make filled forms and placed marks non-editable.

    Refuses when any page still has a PDF redaction annotation: baking the
    black appearance does not apply the redaction, so the underlying text
    stays extractable. Apply a reviewed ``redact`` (``apply_now``) or delete
    the annotations first. Flatten never silently ``apply_redactions()``.
    """
    input_path = _absolute_path(pdf)
    if not input_path.is_file():
        raise FileNotFoundError(f"no such PDF: {input_path}")
    pdf = input_path.resolve()
    explicit_output = output is not None
    main_output = _absolute_path(output) if explicit_output else pdf
    output_display = (
        str(Path(output).expanduser()) if explicit_output else str(input_path)
    )
    doc = pymupdf.open(str(pdf))
    try:
        pending_pages = _pages_with_redact_annots(doc)
        if pending_pages:
            listed = ", ".join(str(p) for p in pending_pages)
            raise OpError(
                f"flatten refused: pending PDF redaction annotations on page(s) "
                f"{listed}. Apply those redactions with a reviewed redact "
                "(apply_now) or delete the annotations first. Flattening would "
                "bake the redaction appearance while leaving the underlying "
                "text extractable."
            )
        doc.bake(annots=True, widgets=True)
        _save(doc, pdf, main_output)
    except BaseException:
        if not doc.is_closed:
            doc.close()
        raise
    return {"output": output_display}
