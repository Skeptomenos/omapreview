# omepreview Preview-Parity SPEC and Implementation Plan

**Date:** 2026-09-13  
**Companion:** `omepreview`-preview-gap-analysis.md`  
**Target tree:** Skeptomenos/omepreview (AGPL-3.0-or-later), forked from pbergin11/omapdf  
**Stack:** Python ≥ 3.11, GTK4, PyMuPDF, existing JSON op engine  

This spec adds Preview-class page handling and true redaction without replacing omepreview’s agent/ghost model.

---

## 0. Goal

Make `omepreview edit` the Linux default PDF app for:

- add / remove / reorder / rotate pages
- copy and paste pages between two windows
- annotate and paint
- sign
- redact for real

CLI and MCP must grow the same ops. If the GUI can do it, an agent can do it.

Non-goals for this spec: PAdES, image-editor Preview features, Continuity Camera, Quartz filters, merging Xournal++ or PDF Arranger source.

---

## 1. Design rules

1. Every mutation is an op in `docs/ops.md`. GUI is an op author. Engine is the writer.
2. Page ops are batches and atomic, same as today’s annot ops.
3. Redaction is never an ink rectangle. Content is removed on commit.
4. Ghosts for destructive ops: delete/rotate/redact/insert show as pending until Save.
5. Coordinates and page numbers stay 1-based, top-left origin, PDF points.
6. New ops fail closed: bad page index aborts the batch.
7. Do not steal Super. Use Ctrl/Shift/Delete inside GTK. Omarchy users bind Super in Hyprland if they want.

---

## 2. New operations

Add to `src/omepreview/ops.py`, appliers in `engine.py`, schema tests, and `docs/ops.md`.

### 2.1 `rotate_pages`

```json
{
  "op": "rotate_pages",
  "pages": [2, 3],
  "degrees": 90
}
```

- `degrees` ∈ {90, 180, 270, -90}.
- Rotates page objects, not a visual overlay.
- Report: `{ "pages": [...], "degrees": 90, "applied": true }`.

### 2.2 `delete_pages`

```json
{
  "op": "delete_pages",
  "pages": [1, 4, 9]
}
```

- 1-based. After apply, later page numbers compact.
- Engine must apply high-to-low or resolve all indices first.
- Refuse to delete every page (document must keep ≥ 1 page) unless `insert_pages` is in the same batch and the net count ≥ 1.

### 2.3 `move_pages`

```json
{
  "op": "move_pages",
  "pages": [5, 6],
  "after": 1
}
```

- `after`: 0 = beginning; N = after current page N.
- Used for sidebar drag reorder inside one document.

### 2.4 `insert_pages`

```json
{
  "op": "insert_pages",
  "after": 2,
  "source": "other.pdf",
  "source_pages": [1, 2, 3]
}
```

Variants:

| Field | Meaning |
|---|---|
| `source` | Path to another PDF |
| `source_pages` | Pages to copy (default all) |
| `blank` | `{ "count": 1, "width": 595, "height": 842 }` — omit `source` |
| `image` | Path to PNG/JPEG — raster becomes a page sized to image @ 72 dpi or fitted to previous page size |

Cross-window paste serializes selected pages to a temp PDF (or in-memory bytes) and emits `insert_pages`.

### 2.5 `extract_pages`

```json
{
  "op": "extract_pages",
  "pages": [2, 3],
  "to": "excerpt.pdf"
}
```

CLI convenience. GUI “drag to file manager” / “save selection as” uses this.

### 2.6 `redact`

```json
{
  "op": "redact",
  "page": 1,
  "match": "Jane Doe"
}
```

or

```json
{
  "op": "redact",
  "page": 1,
  "rect": [72, 400, 300, 430],
  "fill": [0, 0, 0]
}
```

Rules:

- Exactly one of `match` or `rect`.
- On apply: PyMuPDF `add_redact_annot` + `apply_redactions()` (text and intersecting image samples removed). Then draw an opaque rect so the hole is visible.
- Optional `"apply_now": false` keeps a redaction annotation as a ghost until Save.
- Report includes resolved `rects` and a `verify` object: `{ "text_still_present": false }` from a post-pass `get_text()`.
- If verify fails, batch fails. No silent overlay.

### 2.7 Already-planned ops this spec depends on

Implement with page ops, do not skip:

- `delete_annotation` — needed so redaction ghosts and markup stay editable.
- `stamp` — optional P2.

---

## 3. Editor UX

### 3.1 Thumbnail sidebar (F9 stays)

Each row: page preview, page number, selection chrome.

| Gesture | Result |
|---|---|
| Click | Select page, jump view |
| Ctrl+click | Toggle selection |
| Shift+click | Range select |
| Drag on selected thumbnails | Reorder → `move_pages` ghost |
| Delete / Backspace | `delete_pages` ghost |
| Ctrl+C | Copy selected pages to `application/x-omepreview-pages` + a PDF fallback on the clipboard |
| Ctrl+V | `insert_pages` after the focused thumbnail (or end) |
| Ctrl+X | copy + `delete_pages` |
| Ctrl+R | rotate 90° CW; Shift+Ctrl+R CCW |
| Right-click | Rotate L/R, Delete, Extract…, Insert blank, Insert file… |
| Drop PDF on sidebar | `insert_pages` at drop index |
| Drop PNG/JPEG on sidebar | image page at drop index |
| Drag thumbnails to a file manager | `extract_pages` to the drop path |

Visual: deleted pages ghost as struck / dimmed. Inserted pages ghost with a “+” badge. Rotated pages show the new orientation immediately (live preview via a scratch doc or matrix).

### 3.2 Two windows

Clipboard MIME: `application/x-omepreview-pages` = JSON `{ "n": N, "pdf_b64": "..." }` plus `application/pdf`. Paste also accepts legacy `application/x-omapdf-pages`.

Any omepreview editor instance must paste that. This is Preview’s two-window move.

### 3.3 Redact tool

New toolbar button after stamps.

- Mode A: drag over **text hits** — snap to glyph bboxes (Preview Redact Selection).
- Mode B: drag a free rectangle (images, handwriting).
- Ghosts are translucent black; caption “not applied until Save.”
- First redact in a session: modal “Save writes a new file by default (`*_redacted.pdf`). Original stays.” Default on. User can switch to in-place with a typed confirm.

After Save, run verify (`get_text` + optional `mutool draw` smoke). If text remains, show error and do not replace the original.

### 3.4 Annotation / sign (already shipped)

Keep pen, highlighter, text, notes, signature, stamps.

Add only what page-parity needs:

- Form field hit-testing: click empty AcroForm widget → type → `fill_field`.
- Select existing annotation → Delete → `delete_annotation`.

Shapes, crop, password: P2, not in MVP.

### 3.5 Shortcuts

| Key | Action |
|---|---|
| F9 | Toggle thumbnails |
| Ctrl+C / X / V | Page clipboard when focus is sidebar; text copy when focus is page text select |
| Delete | Delete selected pages (sidebar) or selected annot (page) |
| Ctrl+R / Shift+Ctrl+R | Rotate selection |
| Ctrl+S | Commit ghosts (including page ops + redaction) |
| Ctrl+Z / Shift+Ctrl+Z | Undo / redo across save |
| B | Insert blank page after current |
| R | Redact tool |

Focus rule: if the sidebar has a page selection, keys apply to pages. Otherwise they apply to page-canvas tools.

---

## 4. CLI and MCP

### 4.1 CLI

```
omepreview pages input.pdf --list
omepreview pages input.pdf --delete 1,4-6 -o out.pdf --confirm
omepreview pages input.pdf --rotate 90 --pages 2,3 -o out.pdf
omepreview pages input.pdf --move 5-6 --after 1 -o out.pdf
omepreview pages dest.pdf --insert src.pdf --src-pages 1-3 --after 2 -o out.pdf
omepreview pages dest.pdf --blank --after 0 -o out.pdf
omepreview pages in.pdf --extract 2-5 -o excerpt.pdf
omepreview redact in.pdf --page 1 --match "SSN" -o out.pdf --confirm
omepreview redact in.pdf --page 1 --rect 72,400,300,430 -o out.pdf --confirm
```

`--dry-run --json` is available on all of the above. Consequential edits also
require `--confirm` to commit.

### 4.2 MCP tools

Add: `list_pages`, `delete_pages`, `rotate_pages`, `move_pages`, `insert_pages`, `extract_pages`, `redact`.

`redact` and `delete_pages` default to dry-run (same posture as `place_signature`). Human or explicit `confirm: true` commits.

---

## 5. Engine notes (PyMuPDF)

Page ops:

- Use `Document.select`, `insert_pdf`, `delete_page`, `set_rotation`, `new_page`, `insert_image` on a full-page rect.
- Never rewrite page objects by rasterizing unless the source is an image file.
- Preserve bookmarks where possible; after page deletes, drop or rewrite dests that point at removed pages.
- Forms: transferring pages with `insert_pdf` should copy widgets. Test a signed-looking AcroForm.

Redaction:

- `page.add_redact_annot(quad_or_rect, fill=(0,0,0))`
- `page.apply_redactions(images=PDF_REDACT_IMAGE_PIXELS)` (or current PyMuPDF equivalent) so image pixels in the rect are destroyed, not just covered.
- Second pass: `page.get_text()` must not contain `match`.
- Optional paranoid mode (P2): rasterize page to image and rebuild — Censor’s “paranoid” path. Not MVP.

Ghost preview:

- Page ops: apply to an in-memory copy used only for rendering. Disk file unchanged until Save.
- Matches current ghost model for ink/notes.

---

## 6. Acceptance tests

Put under `tests/`. Use generated fixtures (already the project style).

### 6.1 Page ops

1. 5-page PDF → delete 2,4 → 3 pages; text of old page 3 is new page 2.
2. Rotate page 1 by 90 → `/Rotate` or mediabox swap as PyMuPDF writes it; visual snapshot differs.
3. Move pages [5,6] after 1 on a 6-page file → order 1,5,6,2,3,4.
4. Insert pages 1–2 of B after page 1 of A → page count A+2; content identity via text extract.
5. Insert blank after 0 → first page empty, original page 1 is now 2.
6. Insert image page → page count +1; page has a raster XObject.
7. Refuse delete-all.
8. Atomic fail: batch with `delete_pages: [99]` changes nothing.

### 6.2 Clipboard / GUI (if you have GTK tests; else manual checklist)

9. Two editor instances; copy 2 pages; paste; both files independent after save.
10. Delete key on sidebar selection does not delete if focus is a text box.

### 6.3 Redaction

11. `match` on “SECRET” → output `get_text()` has no “SECRET”; `pdftotext` has no “SECRET”.
12. Rect over an embedded image region → sampling the rect is near-black; original image bytes of that crop are gone.
13. Overlay-only control: drawing a black ink rect (old pen tool) still leaves text in `get_text()` — document that this is **not** redact.
14. Dry-run redact does not write removal.
15. Verify hook fails the save if extraction still finds the string.

### 6.4 Regression

16. Existing highlight / note / sign / fill_field tests still pass.
17. Undo save after redact restores original bytes.

---

## 7. Implementation plan

Work in vertical slices. **Every milestone (M1 page knife, M2 safe share, M3 daily driver) must be validated on three surfaces: CLI, MCP, and the GTK editor.** Slice 1 therefore ships engine + CLI + MCP together; GUI page-surgery remains Slices 2–3, and M1 UI proof happens after those slices land.

**Engel-500** (real bank-statement redaction exam) is an end-of-development check after M2, not a Slice 1 goal.

### Slice 0 — prep (0.5 day)

- Branch `preview-parity` off upstream.
- Extend `ops.py` schema with the new op names (validated; appliers follow in Slice 1).
- Add fixture factory: `tests/data/make_docs.py` builds a 6-page labeled PDF (“PAGE 1” …) plus helpers for image assets / image-only pages. Generated fixtures, not committed binaries.

### Slice 1 — page engine + CLI + MCP (2–3 days)

- Implement `rotate_pages`, `delete_pages`, `move_pages`, `insert_pages` (PDF source + blank + image), `extract_pages`.
- `omepreview pages ...` command with `--dry-run --json`.
- MCP: `list_pages`, `delete_pages` (dry-run default), `rotate_pages`, `move_pages`, `insert_pages`, `extract_pages`.
- Tests 1–8 plus MCP dry-run/apply coverage.
- Regression: `omepreview edit` still launches (no sidebar page ops yet).
- Manual: `omepreview pages a.pdf --insert b.pdf --after 1 -o out.pdf`.

Exit: CLI and MCP page surgery work on aarch64 with system `python-pymupdf`.

### Slice 2 — sidebar GUI (3–4 days)

- Thumbnail list becomes a selection model (GTK MultiSelection).
- Drag reorder → `move_pages` ghosts.
- Delete / rotate keys and context menu.
- File-drop on sidebar.
- In-memory scratch doc for live preview of page ghosts.
- Save applies the page-op batch then remaining annot ops (order: page ops first, then markup, so rects stay valid — **or** resolve markup against pre-op page numbers and document that). Spec decision: **page ops first in a save batch**. Markup ghosts store page ids from the pre-save document; on mixed save, rebase markup page numbers through the page-op plan.

Exit: one window can delete, rotate, reorder, insert blank, insert file. Undo works.

### Slice 3 — cross-window clipboard + extract (1–2 days)

- Clipboard serializer (PDF bytes + JSON).
- Ctrl+C/V/X.
- Drag thumbnails to Nautilus / Nemo / Cosmic Files / `xdg-desktop-portal` drop — best effort; if portal is painful, “Extract…” file dialog is enough for v1.
- Image-as-page drop.

Exit: two `omepreview edit` windows pass pages like Preview.

### Slice 4 — redaction (2–3 days)

- `redact` op + verify hook.
- Toolbar tool, text-snap + rect modes.
- Save-as-`_redacted.pdf` default.
- MCP + CLI.
- Tests 11–15.
- Docs: “pen is not redact.”

Exit: Censor-class removal inside omepreview.

### Slice 5 — forms + annot delete (1–2 days)

- Hit-test widgets; `fill_field` from GUI.
- `delete_annotation` + Delete key on selected markup.
- Unblocks “edit saved annotations” on the upstream roadmap.

### Slice 6 — docs, packaging, Omarchy (1 day)

- Update README: “Preview-like page sidebar.”
- `docs/ops.md` + roadmap (move redaction from Later to Shipped).
- PKGBUILD smoke on aarch64.
- Manual checklist in `docs/preview-parity.md`.

**Calendar total:** about 10–14 focused days for one developer who already knows the repo.

---

## 8. Phased product milestones

### M1 — Page knife (Slices 0–3)

Ship even if redact is still “later.” This is the Preview feeling. **M1 is complete only when CLI, MCP, and GTK editor all pass the same page-op acceptance set** (GTK proof lands after Slices 2–3).

### M2 — Safe share (Slice 4)

Redact + verify. Do not advertise redact until tests 11–12 pass.

### M3 — Daily driver (Slice 5–6 + P2 backlog)

Forms in GUI, annot delete, then shapes / crop / password as follow-ups.

---

## 9. P2 backlog (not in M1/M2)

- Outline / TOC sidebar.
- Highlights & Notes list (upstream “comments summary” can feed this).
- Shapes: line, arrow, rect, oval as annots.
- Crop page op.
- `encrypt` / permissions on save-as.
- Two-page view; contact sheet.
- Paranoid raster-page redact mode.
- `sign --auto` (already planned).
- Print action.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| PyMuPDF page insert drops some annotations / named dests | Fixture with links + comments; document known losses |
| Mixed save rebasing is buggy | Page ops first; freeze markup page numbers through a remap table |
| Redaction looks done but text remains in streams | Mandatory verify hook; fail save |
| aarch64 PyMuPDF API drift | Pin/test against Arch `python-pymupdf` the user already syncs |
| Clipboard MIME ignored by other apps | Always also put `application/pdf` on the clipboard |
| Name clash if this is a fork | Contribute upstream; do not publish a second “omapdf” unless forking with a new name |

---

## 11. Suggested first PR

One PR, engine only:

> feat(ops): rotate_pages, delete_pages, move_pages, insert_pages, extract_pages

No GUI. Unblocks agents and the editor work. Matches how omepreview already ships features (engine → CLI → GUI).
