"""In-memory scratch preview for pending page operations (GUI + tests)."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import pymupdf

from . import engine
from .fs_privacy import chmod_private_file, scratch_dir


def index_after_move(page_count: int, pages: list[int], after: int) -> int:
    """0-based destination of the first moved page after ``move_pages``.

    Mirrors ``engine._apply_move_pages`` so the canvas can open the page
    that was dragged instead of staying on (or snapping to) page 1.
    """
    if not pages:
        raise ValueError("pages must be non-empty")
    move_set = set(pages)
    remaining = [p for p in range(1, page_count + 1) if p not in move_set]
    if after == 0:
        new_order = list(pages) + remaining
    else:
        insert_at = remaining.index(after) + 1
        new_order = remaining[:insert_at] + list(pages) + remaining[insert_at:]
    return new_order.index(pages[0])


def apply_identity_op(
    identities: list[int], op: dict, next_id: int
) -> tuple[list[int], int]:
    """Return the page-id list after one surgery op. Rotate/crop are no-ops."""
    ids = list(identities)
    kind = op.get("op")
    if kind == "delete_pages":
        for p in sorted(set(op["pages"]), reverse=True):
            idx = p - 1
            if 0 <= idx < len(ids):
                del ids[idx]
    elif kind == "insert_pages":
        n = engine._insert_count(op)
        after = int(op["after"])
        fresh = list(range(next_id, next_id + n))
        ids[after:after] = fresh
        next_id += n
    elif kind == "move_pages":
        pages_to_move = list(op["pages"])
        after = int(op["after"])
        n = len(ids)
        move_set = set(pages_to_move)
        remaining = [p for p in range(1, n + 1) if p not in move_set]
        if after == 0:
            new_order = list(pages_to_move) + remaining
        else:
            insert_at = remaining.index(after) + 1
            new_order = remaining[:insert_at] + list(pages_to_move) + remaining[insert_at:]
        ids = [ids[p - 1] for p in new_order]
    return ids, next_id


def identities_from_ops(original_count: int, ops: list[dict]) -> list[int]:
    ids = list(range(original_count))
    next_id = original_count
    for op in ops:
        ids, next_id = apply_identity_op(ids, op, next_id)
    return ids


def remap_page_index(page_0: int, old_ids: list[int], new_ids: list[int]) -> int | None:
    """New 0-based index for a page, or None if that page was deleted."""
    if page_0 < 0 or page_0 >= len(old_ids):
        return None
    sid = old_ids[page_0]
    try:
        return new_ids.index(sid)
    except ValueError:
        return None


def rebind_items_to_identities(
    items: list[dict], old_ids: list[int], new_ids: list[int]
) -> int:
    """Rewrite 0-based ``page`` keys so markup stays on the same logical page.

    Items whose page was deleted are removed (not silently retargeted).
    Remaining dict objects are kept so editor selection identity is preserved.
    Returns the number of dropped items.
    """
    kept: list[dict] = []
    dropped = 0
    for it in items:
        if "page" not in it:
            kept.append(it)
            continue
        try:
            page_0 = int(it["page"])
        except (TypeError, ValueError):
            kept.append(it)
            continue
        new_page = remap_page_index(page_0, old_ids, new_ids)
        if new_page is None:
            dropped += 1
            continue
        it["page"] = new_page
        kept.append(it)
    items[:] = kept
    return dropped


def _pdf_page_count(path: str) -> int:
    doc = pymupdf.open(path)
    try:
        return doc.page_count
    finally:
        doc.close()


class PagePreviewState:
    """Tracks page-op ghosts and a scratch PDF that reflects them."""

    def __init__(self, source: str | Path | None = None):
        self.page_ops: list[dict] = []
        self.scratch_path: str | None = None
        self.inserted_pages: set[int] = set()  # 1-based pages in scratch view
        self._temp_sources: list[Path] = []
        self.on_identities_changed: Callable[[list[int], list[int]], None] | None = None
        if source is None:
            self.source = ""
            self._original_count = 0
        else:
            self.source = str(Path(source).resolve())
            self._original_count = _pdf_page_count(self.source)

    def identities(self) -> list[int]:
        """Stable ids for the current page order (index 0 = first visible page)."""
        return identities_from_ops(self._original_count, self.page_ops)

    def retarget(self, source: str | Path):
        """Point the preview at a different file (e.g. after save-as-copy)."""
        self._drop_scratch()
        self._drop_temp_sources()
        self.page_ops.clear()
        self.inserted_pages.clear()
        self.scratch_path = None
        self.source = str(Path(source).resolve())
        self._original_count = _pdf_page_count(self.source)

    def has_changes(self) -> bool:
        return bool(self.page_ops)

    def clear(self, *, preserve_temp_sources: bool = False):
        self.page_ops.clear()
        self._drop_scratch()
        self.inserted_pages.clear()
        if not preserve_temp_sources:
            self._drop_temp_sources()
        if self.source and os.path.isfile(self.source):
            self._original_count = _pdf_page_count(self.source)
        elif not self.source:
            self._original_count = 0

    def _drop_scratch(self):
        if self.scratch_path and os.path.exists(self.scratch_path):
            os.unlink(self.scratch_path)
        self.scratch_path = None

    def _drop_temp_sources(self):
        for path in self._temp_sources:
            path.unlink(missing_ok=True)
        self._temp_sources.clear()

    def take_temp_sources_from(self, other: PagePreviewState) -> None:
        """Transfer owned inputs when a prepared preview replaces its state."""
        self._temp_sources.extend(other._temp_sources)
        other._temp_sources.clear()

    def prune_temp_sources(self, referenced: set[str]) -> None:
        """Remove only owned inputs no longer used by edits or history."""
        kept = []
        for path in self._temp_sources:
            if str(path) in referenced:
                kept.append(path)
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Cleanup must not turn a committed history step into a
                # retryable failure. Keep ownership so cleanup can retry.
                kept.append(path)
        self._temp_sources = kept

    def _remember_source(self, path: str | Path) -> str:
        p = Path(path)
        self._temp_sources.append(p)
        return str(p)

    def rebuild(self) -> pymupdf.Document:
        """Apply page_ops to a temp copy; return the scratch document."""
        self._drop_scratch()
        self.inserted_pages.clear()
        if not self.source:
            return pymupdf.open()
        # Undo can restore a different on-disk page count while retaining
        # pending insertions. Identities must start from that restored base.
        self._original_count = _pdf_page_count(self.source)
        if not self.page_ops:
            return pymupdf.open(self.source)
        fd, path = tempfile.mkstemp(suffix=".pdf", dir=str(scratch_dir()))
        os.close(fd)
        shutil.copy(self.source, path)
        chmod_private_file(path)
        for op in self.page_ops:
            engine.apply(path, [op], output=path)
            chmod_private_file(path)
        self.scratch_path = path
        doc = pymupdf.open(path)
        self.inserted_pages = self._mark_inserted_pages(doc)
        return doc

    def open_view(self) -> pymupdf.Document:
        if not self.source:
            return pymupdf.open()
        if self.scratch_path and os.path.exists(self.scratch_path):
            return pymupdf.open(self.scratch_path)
        if self.page_ops:
            return self.rebuild()
        return pymupdf.open(self.source)

    def page_count(self) -> int:
        if not self.source:
            return 0
        doc = self.open_view()
        try:
            return doc.page_count
        finally:
            doc.close()

    def append_op(self, op: dict):
        old_ids = self.identities()
        self.page_ops.append(op)
        try:
            self.rebuild()
        except BaseException:
            self.page_ops.pop()
            raise
        new_ids = self.identities()
        cb = self.on_identities_changed
        if cb is not None:
            cb(old_ids, new_ids)

    def add_delete_pages(self, pages: list[int]):
        self.append_op({"op": "delete_pages", "pages": sorted(set(pages))})

    def add_rotate_pages(self, pages: list[int], degrees: int):
        self.append_op({"op": "rotate_pages", "pages": pages, "degrees": degrees})

    def add_crop_pages(self, pages: list[int], rect: list[float]):
        self.append_op({"op": "crop_pages", "pages": pages, "rect": rect})

    def add_move_pages(self, pages: list[int], after: int):
        self.append_op({"op": "move_pages", "pages": pages, "after": after})

    def add_insert_blank(self, after: int, count: int = 1, width: float = 595, height: float = 842):
        self.append_op(
            {
                "op": "insert_pages",
                "after": after,
                "blank": {"count": count, "width": width, "height": height},
            }
        )

    def add_insert_pdf(
        self,
        after: int,
        source: str,
        source_pages: list[int] | None = None,
        *,
        retain_source: bool = False,
    ):
        src = self._remember_source(source) if retain_source else source
        op: dict = {"op": "insert_pages", "after": after, "source": src}
        if source_pages:
            op["source_pages"] = source_pages
        self.append_op(op)

    def add_insert_image(self, after: int, image: str):
        self.append_op({"op": "insert_pages", "after": after, "image": image})

    def move_selection_to_after(self, selected_1based: list[int], after: int):
        if not selected_1based:
            return
        if after != 0 and after in selected_1based:
            return
        self.add_move_pages(selected_1based, after)

    def _mark_inserted_pages(self, doc: pymupdf.Document) -> set[int]:
        """Pages whose text does not match the original PAGE N labels are inserts."""
        orig = pymupdf.open(self.source)
        try:
            orig_labels = {
                n + 1: (orig[n].get_text("text").strip().split()[-1] if orig[n].get_text("text").strip() else "")
                for n in range(orig.page_count)
            }
        finally:
            orig.close()
        inserted: set[int] = set()
        for n in range(doc.page_count):
            text = doc[n].get_text("text").strip()
            label = text.split()[-1] if text else ""
            if str(n + 1) not in orig_labels.values() and label not in orig_labels.values():
                inserted.add(n + 1)
            elif text == "":
                inserted.add(n + 1)
        # Also tag pages beyond original count when labels still line up
        orig_count = len(orig_labels)
        for n in range(orig_count + 1, doc.page_count + 1):
            inserted.add(n)
        return inserted
