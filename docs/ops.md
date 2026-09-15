# The omepreview operations spec

A document edit is a JSON array of operation objects. This vocabulary is the
project's stable contract: the CLI, MCP server, and GUI are all clients of it,
and third-party tools are welcome to speak it directly (`omepreview apply doc.pdf
--ops ops.json`).

## CLI and MCP

Each op is available through `omepreview apply --ops file.json` and MCP
`apply_ops`. Convenience wrappers:

| Op | CLI | MCP |
|----|-----|-----|
| Page list | `omepreview pages FILE --list` | `list_pages` |
| Page surgery | `omepreview pages FILE --delete …` etc. | `delete_pages`, `rotate_pages`, `move_pages`, `insert_pages`, `extract_pages`, `crop_pages` |
| Crop page | `omepreview crop FILE --page N --rect …` | `crop_pages` |
| Redact | `omepreview redact FILE --page N --match …` or `--rect …` | `redact` (dry-run default) |
| Delete annot | `omepreview delete-annotation FILE --page N --index I` | `delete_annotation` (dry-run default) |
| Markup / forms | `annotate`, `note`, `fill`, `sign`, `shape` | `highlight`, `add_note`, `fill_field`, `place_signature`, `add_shape` |

Consequential MCP tools (`place_signature`, `delete_pages`, `redact`,
`delete_annotation`) default to dry-run; pass `confirm=true` (or
`confirmed=true` for signatures) after human approval. The generic
`apply_ops` tool also defaults to dry-run; pass `dry_run=false` after approval.

## Conventions

- **Pages are 1-based** everywhere a human or agent sees them.
- **Coordinates are PDF points** (1/72"), origin at the **top-left** of the
  page, y growing downward — identical to what `omepreview read` reports, so a
  bbox from a read can be passed straight back as a target.
- Every applied op is echoed back in the report with its resolved geometry
  (`rects`, `rect`, `page`) and `"applied": true|false` (false = dry run).
- Validation is strict: unknown ops, missing keys, and unmatched text fail
  the whole batch before anything is written.
- A non-dry-run batch applies every operation to a disposable document first.
  The main output is published only after the complete operation sequence and
  serialization succeed. In-place saves use an atomic replacement and keep
  the source file private (`0600`). Existing PDF encryption and permissions
  are retained; password-protected files that cannot be opened are rejected.

### Numeric limits

Numeric inputs must be JSON numbers, not numeric strings, and must be finite.
Coordinates are limited to -1,000,000 through 1,000,000 points. Colors are
limited to 0 through 1. Text size must be greater than 0.1 and at most 1,000
points. Ink and shape widths must be greater than 0 and at most 1,000 points.
Signature widths must be greater than 0 and at most 10,000 points. Blank-page
dimensions must be greater than 0 and at most 100,000 points.

`omepreview snapshot` accepts scale values from 0.05 through 4 and grid steps
from 1 through 10,000 points. These bounds cap raster and grid work. Invalid,
nonfinite, nonpositive, or out-of-range values reject the request before a
file is written.

## Operations

### highlight
```json
{"op": "highlight", "page": 1, "match": "early termination clause"}
{"op": "highlight", "page": 1, "rect": [72, 130, 400, 148], "style": "underline"}
```
Exactly one of `match` (exact text search; every occurrence on the page is
marked) or `rect`. `style`: `highlight` (default) | `underline` | `strikeout`
| `squiggly`. Report adds `rects`.

### note
```json
{"op": "note", "page": 2, "at": [450, 200], "text": "30-day notice required"}
```
A sticky-note (Text) annotation — renders as a comment icon in any viewer.

### text_box
```json
{"op": "text_box", "page": 2, "rect": [100, 300, 300, 330], "text": "N/A", "size": 11}
```
A FreeText annotation: visible text drawn on the page (e.g., writing into a
non-form document). `size` defaults to 11pt.

### fill_field
```json
{"op": "fill_field", "field": "tenant_name", "value": "Jane Doe"}
```
Fills an AcroForm field by name (find names with `omepreview fields`). Checkbox
fields accept `true/yes/on/1` (case-insensitive). Unknown names fail with the
document's actual field list in the error. Report adds `page`.

### place_signature
```json
{"op": "place_signature", "page": 4, "at": [120, 540], "width": 180,
 "signature": "default", "date": true}
```
Stamps a saved signature **SVG** (or an imported PNG) with its top-left
corner at `at`, scaled to `width` points (height keeps the image's aspect
ratio). `signature` names a file saved via `omepreview sig draw` / `sig add`
(default: `"default"`) under `~/Downloads/omapreview/signature/` (legacy
read fallback: `~/.config/omepreview/signatures/`). `date: true` writes
today's ISO date below. Report adds `rect` (and `date`).

Note: the image is inserted into page content, not as an annotation — it
survives every viewer and doesn't need flattening.

### ink
```json
{"op": "ink", "page": 1, "strokes": [[[100, 200], [120, 220], [140, 200]]],
 "color": [0.75, 0.1, 0.1], "width": 2}
```
Freehand strokes as a real Ink annotation. `strokes` is a list of polylines
(each 2+ `[x, y]` points); `color` is `[r, g, b]` in 0..1 (default black);
`width` in points (default 2). Powers the editor's pen and its ✓/✕ stamps.
Report adds the bounding `rect`.

### shape
```json
{"op": "shape", "page": 1, "shape": "line", "from": [72, 100], "to": [300, 200]}
{"op": "shape", "page": 1, "shape": "arrow", "from": [72, 100], "to": [300, 200]}
{"op": "shape", "page": 1, "shape": "rect", "rect": [80, 80, 220, 160]}
{"op": "shape", "page": 1, "shape": "oval", "rect": [80, 80, 220, 160],
 "color": [0.1, 0.35, 0.85], "width": 2}
```
Vector shape annotations (Preview-class markup). `shape` is one of `line`,
`arrow`, `rect`, `oval`. Line and arrow need `from` and `to` points; rect and
oval need `rect`. Renders as PDF Line, Square, or Circle annotations.
`color` defaults to black; `width` defaults to 2pt. Report adds `rect` (bbox).

### crop_pages
```json
{"op": "crop_pages", "pages": [1], "rect": [72, 80, 500, 750]}
```
Sets the PDF **CropBox** for each listed page — the visible page region in
current page coordinates (top-left origin, same space as `omepreview read` rects).
Does not auto-trim content to ink bounds; repeated crops stack in page space.
Report: `{ "pages": [...], "rect": [...], "resolved": [{ "page", "cropbox",
"size_before", "size_after" }, ...] }`.

### rotate_pages
```json
{"op": "rotate_pages", "pages": [2, 3], "degrees": 90}
```
Rotates page objects (not a visual overlay). `degrees` ∈ {90, 180, 270, -90}.
Report: `{ "pages": [...], "degrees": 90 }`.

### delete_pages
```json
{"op": "delete_pages", "pages": [1, 4, 9]}
```
1-based page numbers. After apply, later pages compact. Deletes are applied
high-to-low. Refuses to delete every page unless `insert_pages` in the same
batch keeps the net count ≥ 1. Report: `{ "pages": [...] }`.

### move_pages
```json
{"op": "move_pages", "pages": [5, 6], "after": 1}
```
Reorder pages. `after`: 0 = beginning; N = after current page N. Report:
`{ "pages": [...], "after": 1 }`.

### insert_pages
```json
{"op": "insert_pages", "after": 2, "source": "other.pdf", "source_pages": [1, 2, 3]}
{"op": "insert_pages", "after": 0, "blank": {"count": 1, "width": 595, "height": 842}}
{"op": "insert_pages", "after": 1, "image": "scan.png"}
```
Exactly one of `source`, `blank`, or `image`. `source_pages` defaults to all
pages in the source PDF. Blank pages default to A4 (595×842 pt). Image pages
are sized to the previous page when `after` ≥ 1, otherwise to the image
pixels. Report includes `inserted` and which variant was used.

### extract_pages
```json
{"op": "extract_pages", "pages": [2, 3], "to": "excerpt.pdf"}
```
Writes selected pages to `to`. Does not modify the source document. Report:
`{ "pages": [...], "to": "excerpt.pdf" }`.

Extraction is staged until the complete batch succeeds. The source remains
byte-identical for an extract-only request. The source and all extraction
destinations must be distinct files; aliases and duplicate extraction
destinations are rejected before any write. When a main output is requested,
it must also be distinct from every extraction destination. The main output
may be the source path for an in-place save. Extracting an encrypted source is
rejected because a new excerpt cannot preserve the source's owner password and
permission settings without the original credentials. When one batch publishes
several independent outputs, operation application is atomic, but the final
filesystem renames are separate: an unusual failure during that publication
phase can leave earlier destination renames in place.

### redact
```json
{"op": "redact", "page": 1, "match": "Jane Doe"}
{"op": "redact", "page": 1, "rect": [72, 400, 300, 430], "fill": [0, 0, 0]}
```
Exactly one of `match` (every occurrence on the page) or `rect`. On apply,
PyMuPDF `add_redact_annot` + `apply_redactions()` removes matched text and
intersecting image samples, then fills the region opaque (default black).
The same authorized rectangle also **strips** intersecting sticky notes
(and their replies), FreeText, file-attachment annotations, and form
fields/values. Apply grows each rectangle to the glyph bboxes already
substantially inside it (so a tight word-snap covers descenders) without
taking neighbors that only graze the edge. After save, verification reopens
the serialized file and checks object payloads plus glyphs whose bbox is
**substantially inside** the applied rectangle — not raw `get_textbox(clip)`,
which reports the next line when Times-like word boxes overlap. A leftover
glyph is named in the error (`leftover 'TOKEN' at [x0, y0, x1, y1]`). If an
intersecting annotation type is not in that list (stamp, ink, highlight,
sound, …), redact **refuses** rather than reporting success while a payload
remains. Annotations wholly outside the rectangle are left intact.

`apply_now: false` adds redaction annotations as editable ghosts until a later
apply. Report adds `rects` and
`verify: { "text_still_present": false, "payloads_still_present": [] }`;
if verify fails, the whole batch fails.

The GTK editor commits the **displayed rectangle** of each ghost (never a
fresh `search_for`), so selecting one repeated word does not redact the
others. CLI/MCP `--match` still means every occurrence on that page.

If the page already has pending redaction annotations, `apply_now` (the
default) refuses rather than also applying those unapproved regions.

**Flatten** (`omepreview flatten FILE [-o OUT]`, MCP `flatten_pdf`) bakes
annotations and form widgets into page content. It **refuses** when any page
still has a PDF redaction annotation: baking the black box does not remove
the underlying text. Apply a reviewed `redact` (`apply_now`) or delete those
annotations first. Flatten never silently `apply_redactions()` — that would
apply unapproved regions.

**Pen / ink is not redact.** Drawing a black ink stroke or rectangle overlay
covers content visually but leaves the underlying text in `get_text()` /
`pdftotext`. Only the `redact` op destroys content.

### delete_annotation
```json
{"op": "delete_annotation", "page": 1, "index": 0}
```
Removes one annotation on `page` by 0-based `index` (listed in `omepreview read`
under `annotations`). When deleting several on the same page in one batch,
indices are applied high-to-low so they stay valid. Report adds `type` and
`rect` of the removed annotation.

## Extending

New op = one schema clause in `ops.py` + one applier in `engine.py` + a spec
entry here + a test. Keep ops small and composable; a batch is the unit of
atomicity.

## Related docs

- [preview-parity.md](preview-parity.md) — manual acceptance checklist
- [roadmap.md](roadmap.md) — shipped vs next vs P2

Planned: `stamp` (library images: APPROVED, initials). See docs/roadmap.md.
