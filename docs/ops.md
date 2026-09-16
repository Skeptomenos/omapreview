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
| Discovery | `omepreview operations` | `operation_schema` |
| Page render | `omepreview snapshot FILE` | `render_page` (PNG + metadata) |
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

The CLI keeps its existing write behavior for ordinary markup, form, page
ordering, insertion, extraction, shape and crop operations. `--dry-run` is
available for every edit command. A consequential operation (`sign`,
`redact`, `delete-annotation`, page deletion, or a generic batch containing
one of these) also defaults to a proposal; pass `--confirm` to write it.
`flatten` follows the same `--dry-run` / `--confirm` rule. The command names
did not change. For example:

```bash
# Before: this wrote the redacted copy.
omepreview redact doc.pdf --page 1 --match "SECRET" -o out.pdf

# Now: the first command only resolves the target; this one commits it.
omepreview redact doc.pdf --page 1 --match "SECRET" -o out.pdf --json
omepreview redact doc.pdf --page 1 --match "SECRET" -o out.pdf --confirm --json
```

## Discovery and rendering

`omepreview operations` and MCP `operation_schema` return the same versioned
catalog. It covers every `ops.py` operation, all variants, required fields,
defaults, numeric bounds and examples. Use the catalog before constructing a
generic batch. Dedicated wrappers are convenience routes; text boxes, ink,
check/cross marks and any other catalog operation use the generic route.

The MCP server is optional. For a checkout install `pip install -e '.[mcp]'`, then
start `omapreview-mcp` on the stdio transport. Installed releases can enable
it directly in their venv without a checkout. See the [MCP setup guide](../README.md#mcp).

`omepreview snapshot FILE --page N --scale 2 --grid 50 --clip X0,Y0,X1,Y1`
writes a PNG for a human or agent to inspect. `render_page` accepts the same
page, scale, grid and clip values but has no output path and keeps the source
read-only. Its response contains an actual `image/png` content block and a
JSON text block. The metadata includes the SHA-256 source fingerprint, page
and page-count, mediabox/CropBox, rotation, canonical clip, displayed clip,
pixel dimensions and the affine transform from unrotated CropBox-local PDF
points to top-left PNG pixels. CLI `snapshot` preserves its historical
displayed-page `size` field and also exposes the canonical `pdf_geometry`.

Clips use `[x0,y0,x1,y1]` in unrotated CropBox-local points. Their width and
height must be strictly positive; `x0` and `y0` may be zero. The clip must be
inside the CropBox. Scale is 0.05–4, grid is 1–10,000,
each image side is at most 16,384 pixels, the image is at most 40,000,000
pixels and the encoded PNG is at most 8,000,000 bytes. Rendering rejects bad
or oversized dimension, pixel or grid requests before drawing/allocation. The
raw PNG byte check happens after encoding. MCP then caps the base64 image
payload at 10,666,668 bytes before returning it. Both interfaces preserve the
PDF bytes.

### MCP errors

Expected validation, input, file and document-domain failures return an MCP
tool result with `isError: true` and actionable text, such as the missing path,
invalid page or clip, unknown field name, or failed text match. Unexpected
failures remain masked as the generic `Error executing tool NAME` response.
This keeps recovery guidance available without exposing crash details.

## Post-save observation

Read the destination after every write. Use `omepreview read OUT --json` or
MCP `read_pdf` for text, fields, annotation types, content and geometry. Use
`omepreview pages OUT --list` or MCP `list_pages` for count, order, size and
rotation. Use `omepreview snapshot OUT` or MCP `render_page` without a grid
for appearance. These reads include a source fingerprint; compare it with
the render metadata so the image and structured observation refer to the same
saved bytes. A successful apply report, file existence or attractive preview
is not by itself verification.

| Task | Fresh saved-output observation |
|---|---|
| Markup, text box, ink, shape, delete annotation | `read`/`read_pdf` plus a no-grid `snapshot`/`render_page` |
| Fill field | `read`/`read_pdf` field value and a no-grid render |
| Signature | no-grid render of the saved page; confirm position, size, date and readable protected text |
| Page surgery | `pages`/`list_pages` plus affected and unchanged control-page renders |
| Extract, crop | read/render the destination and compare the extract source fingerprint |
| Redact | the engine's serialized redaction/payload gate and `verify` report must pass, then a fresh read confirms targeted content is absent and a render confirms appearance; a black cover is not proof |
| Flatten | fresh read confirms annotations/widgets are baked and before/after renders retain appearance |

## Conventions

- **Pages are 1-based** everywhere a human or agent sees them.
- **Coordinates are PDF points** (1/72"), origin at the **top-left** of the
  page, y growing downward — identical to what `omepreview read` reports, so a
  bbox from a read can be passed straight back as a target.
- On cropped or rotated pages, coordinates stay relative to the unrotated
  CropBox. Signature placement, date text, and redaction fill use this same
  frame. These writes preserve the page rotation and both page boxes.
- Every applied op is echoed back in the report with its resolved geometry
  (`rects`, `rect`, `page`) and `"applied": true|false` (false = dry run).
- Validation is strict: unknown ops, missing keys, and unmatched text fail
  the whole batch before anything is written.
- A non-dry-run batch applies every operation to a disposable document first.
  The main output is published only after the complete operation sequence and
  serialization succeed. In-place saves use an atomic replacement and keep
  the source file private (`0600`). Existing PDF encryption and permissions
  are retained; password-protected files that cannot be opened are rejected.
- Dry runs execute the same in-memory sequence, including redactions and
  annotation deletions, so later operations see the state they would see on
  commit. They publish no main output or extraction files.

### Numeric limits

Numeric inputs must be JSON numbers, not numeric strings, and must be finite.
Coordinates are limited to -1,000,000 through 1,000,000 points. Colors are
limited to 0 through 1. Text size must be greater than 0.1 and at most 1,000
points. Ink and shape widths must be greater than 0 and at most 1,000 points.
Signature widths must be greater than 0 and at most 10,000 points. Blank-page
dimensions must be greater than 0 and at most 100,000 points.

`omepreview snapshot` accepts scale values from 0.05 through 4 and grid steps
from 1 through 10,000 points. A rendered image is also limited to 16,384 pixels
per side and 40,000,000 total pixels. A grid is limited to 4,000 lines and
8,000 labels. Requests that exceed these work budgets must reduce the scale,
increase the grid step, or crop the PDF. Invalid, nonfinite, nonpositive, or
out-of-range requests reject before page drawing, raster allocation, or output
replacement.

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
{"op": "fill_field", "field": "tenant_name", "value": "Jane Doe",
 "page": 2, "rect": [160, 180, 400, 198]}
```
Fills an AcroForm field by name (find names with `omepreview fields`). Checkbox
fields accept `true/yes/on/1` (case-insensitive). Unknown names fail with the
document's actual field list in the error. `omepreview read` and `omepreview
fields` report each field's page and widget rectangle. A name-only request is
valid only when it matches one widget. For repeated names, add `page` and,
when needed, the exact `rect` from the read result. A selector that matches no
widget or more than one widget is rejected; the engine never silently fills the
first match. The CLI `fill --page N --rect X0,Y0,X1,Y1` and MCP `fill_field`
accept the same selectors. The report adds the resolved `page` and `rect`.

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
pixels. Report includes `inserted` and which variant was used. Internal links
between selected source pages are remapped to their copied page numbers.
Links to source pages outside `source_pages` are omitted because the copied
document has no corresponding target page. External URI links are retained.
Repeated entries in `source_pages` produce repeated page copies. If a linked
target is repeated, links use the first copied occurrence as the deterministic
destination.

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
Internal links whose source and destination pages are both selected are
preserved and remapped in the excerpt. Links to pages outside the selection
are omitted. External URI links are retained. Repeated selected page numbers
produce repeated copies; a repeated linked target resolves to its first copy.

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
annotations and form widgets into page content. It defaults to a proposal;
pass CLI `--confirm` or MCP `confirm=true` to commit. It **refuses** when any
page still has a PDF redaction annotation: baking the black box does not
remove the underlying text. Apply a reviewed `redact` (`apply_now`) or delete
those annotations first. Flatten never silently `apply_redactions()` — that
would apply unapproved regions.

**Pen / ink is not redact.** Drawing a black ink stroke or rectangle overlay
covers content visually but leaves the underlying text in `get_text()` /
`pdftotext`. Only the `redact` op destroys content.

### delete_annotation
```json
{"op": "delete_annotation", "page": 1, "index": 0}
```
Removes one annotation on `page` by 0-based `index` (listed in `omepreview read`
under `annotations`). Consecutive delete-annotation operations are applied
high-to-low within their run so indices stay valid. A run never crosses a
page move, insertion, deletion or another operation; later page numbers refer
to the document state at that point. Report adds `type` and
`rect` of the removed annotation.

## Extending

New op = one schema clause in `ops.py` + one applier in `engine.py` + a spec
entry here + a test. Keep ops small and composable; a batch is the unit of
atomicity.

## Related docs

- [preview-parity.md](preview-parity.md) — manual acceptance checklist
- [roadmap.md](roadmap.md) — shipped vs next vs P2

Planned: `stamp` (library images: APPROVED, initials). See docs/roadmap.md.

## Application workflows

`omapreview workflow-schema` and MCP `workflow_schema` discover the same
versioned workflow catalog. Each route includes its exact JSON argument
schema, defaults, and status contract. Run a route with
`omapreview workflow NAME --args 'JSON'` (`@file` or `-` for stdin also work),
or MCP `run_workflow(name, arguments)`. Unknown arguments and wrong JSON types
fail before action. The base CLI needs only stdlib and PyMuPDF. Desktop routes
load GTK or external helpers only when used.

| Current capability | CLI workflow / MCP run_workflow name | Completion evidence |
|---|---|---|
| Text search and hit geometry | `search` | Source fingerprint, 1-based page, rect and quad; compare a page render |
| Signature add/import/list/inspect/remove | `signatures` | Stored asset path and SHA-256, then inspect/list; replacement/removal requires `confirm=true` |
| Draw a signature | `record_signature` | `start` hands off to the real recorder; `status` must reach `saved`, with path/hash; `cancel` requests cancellation |
| Open empty editor or PDF with review proposals | `editor`, action `open` | Exact session ID and `ready` from a live editor status; `starting` is incomplete |
| Pending edits, geometry/text changes, removal | `editor`, actions `status`, `stage`, `replace`, `select`, `move`, `delete` | Updated pending operations and revision; no PDF saved yet |
| Page preview / insert / delete / crop / rotate / reorder | `editor`, action `stage` | One page operation per request; saved result after confirmed `save` |
| Undo/redo, including saved edits | `editor`, actions `undo`, `redo` | Requires `confirm=true`; reread actual saved bytes when history changes disk |
| Save or close a session | `editor`, actions `save`, `close` | Save requires confirmation; dirty close requires confirmation to discard pending work |
| Navigate, zoom, search visible pages | `editor`, actions `view`, `search` | Current 1-based page, zoom percent, hit geometry; observe the native window |
| PDF copy, ZIP, flatten copy | `export` | New explicit destination, fingerprint; unzip/read/render as appropriate |
| Email / LocalSend / show folder | `export` | Confirmed external-app handoff; `delivered=false` always, user completes the external action |
| Copy file / ZIP to clipboard | `export` | `clipboard-owned` after helper success; paste and inspect the destination |
| Copy/paste pages | `clipboard` | Copy selected pages as PDF; paste to a new PDF, then use `insert_pages`; inspect saved page identities |
| Cut / cross-window paste / drag export | `clipboard` + existing page ops | Copy successfully before separately confirmed deletion; extract/insert is the file transport equivalent of drag |
| Read, markup, shapes, ink, stamps, forms, signatures, redaction, page surgery | Existing operation catalog and `apply` / `apply_ops` | Existing saved read/render checks above |
| Ask-agent context | `read` / `read_pdf`, `editor` status and pending ops | Return observed content and exact document/session identity to the caller; no implied external agent submission |

Search uses the GUI's PyMuPDF embedded-text semantics. It does not perform OCR.
`query` is 1–4096 characters; `limit` is 1–10000; `offset` is nonnegative.
Continue at `next_offset` until null, using the same source fingerprint. One
rect/quad is a matched text fragment; a dehyphenated match can span fragments.
Coordinates stay in unrotated CropBox-local points on rotated/cropped pages.

The recorder requires a graphical session and GTK. States are `starting`,
`awaiting_human`, `saved`, `cancelled`, and `failed`. `cancel` can return
`cancellation_requested`; poll until terminal. A crashed worker is reported as
failed when detectable. An uncertain process outcome requires library inspection
before retry. Job records are private local runtime files, not permanent audit
storage. No synthetic drawing or headless signature creation is claimed.

Every GUI instance registers a private local Unix socket. Session discovery
returns exact IDs, PIDs, PDF paths, source fingerprint, dirty/conflict flags,
pending markup/page operations, undo/redo depths, current page/zoom and a
revision. `view` accepts `fit=true` or zoom percent; `search` accepts a 0-based
`hit` to navigate a result. All session mutations require that revision. On rejection, inspect
fresh status and review the target before retry. Pending indices are 0-based;
PDF pages remain 1-based. `replace` takes one markup operation and replaces the
chosen pending item (match-based markup may resolve to several ghosts).
`pending` preserves ghost order, including deletion runs; Save alone uses the
engine's safe execution order. `selected_index` refers to that pending list.
`move` rejects fixed widget/annotation targets. Use `replace` to retarget them.
`stage` accepts a markup batch or one page operation. `extract_pages` uses the
existing file export operation. Proposal opening accepts the GUI's markup ops;
stage page operations after opening. Imported page sources use retained copies.

Session requests are limited to 1 MiB. All editor changes run on the GTK main
loop. Source conflict checks remain active. The transport is local-user access,
not a network service or authorization boundary between processes of the same
user. A timeout is not proof that a mutation failed: read status and saved bytes
before retry. Session history lives as long as the editor process. It is not a
persistent version store. Fit/scroll/pointer gestures and toolbar styling remain
human presentation controls; navigation/zoom and all resulting document edits
have the routes above.

Export workflows operate on saved PDF bytes. Save pending edits first. `copy`,
`zip`, `flatten`, and `zip-clipboard` require a new output; existing files and
symlinks are refused. Publication is private and does not overwrite a racing
file. `flatten` uses the shared engine and its pending-redaction refusal.
Email, LocalSend, folder, and clipboard actions require `confirm=true`.
Email opens a composer; LocalSend opens its app. A PID or successful helper exit
never proves delivery. Validation uses isolated mock helpers, never recipients.
Clipboard PDF payloads are limited to 64 MiB; Wayland uses wl-copy/wl-paste,
X11 uses xclip. Missing helpers return actionable errors. A clipboard owner can
be replaced by another app; inspect the final pasted artifact.

Future OCR belongs in the shared operation schema/engine with its own documented
execution contract. The recorder-specific handoff is not an OCR job framework.
