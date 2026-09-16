# Roadmap

## Shipped

**v0.1 — the agent-native core**
- [x] Op engine: highlight/underline/strikeout/squiggly, notes, text boxes,
  form filling, signature placement with date stamp, freehand ink
- [x] Structured read: text blocks + bboxes, form fields, annotations (with
  0-based `index` for delete)
- [x] CLI with `--json`/`--dry-run` everywhere; `--confirm` for consequential edits; atomic `apply` batches
- [x] MCP server (`omepreview-mcp`) with confirm-before-signing and consequential-edit posture
- [x] Signature store + `sig draw` GTK drawing window
- [x] Claude Code skill: full PDF-assistant playbook with the precision
  ladder (anchors → grid snapshot → dry-run → visual verify → ghost handoff)
- [x] `snapshot --grid`: page PNG with a labeled ops-coordinate grid so
  agents can read placement coordinates visually
- [x] Omarchy bar widget (`omepreview`.bar`) + recent-PDFs picker
- [x] Test suite over generated sample documents

**v0.2/v0.3 — the editor** (arrived early, in GTK4 rather than Qt)
- [x] `omepreview edit`: page view, hand-drawn vector icon toolbar, pill styling
- [x] Tools: select/drag, pen + tap-again color palette, highlighter, text,
  sticky notes, signature, check/cross stamps, **redact** (R)
- [x] Ghost model with undo/redo **across the save boundary** (a save is an
  undoable step; undoing it resurrects the items as editable ghosts)
- [x] `--ops` proposals load as draggable ghosts (agent proposes, human
  confirms)
- [x] Thumbnails sidebar, live fit-width, zoom presets + Ctrl+scroll,
  full-document search, complete page-navigation keys
- [x] Click-to-open saved comments (any PDF's annotations, not just ours)
- [x] Share menu: email attach, LocalSend, copy file / zip to clipboard,
  show in folder, flatten-copy-first toggle
- [x] Ask-your-agent (✦ → `omarchy agent prompt` with the file) + disk
  watcher that reloads the view when the agent saves changes
- [x] Default-PDF-handler desktop file (`omepreview open` → the editor)

**Verifiable CLI/MCP contract**
- [x] `render_page` PNG content with source-bound geometry metadata and CLI
  `snapshot --clip` parity
- [x] Complete `operation_schema` / `omepreview operations` discovery catalog
- [x] Saved-output reads and renders documented for every operation type

**v0.1.1 — review remediation and distribution**
- [x] Safe publication, stable editor history, precise page/object targets, and
  signature/export validation across the GUI, CLI, and MCP paths
- [x] Progressive agent skill with independent CLI/MCP workflows and
  post-save verification recipes
- [x] Deterministic application source asset with matching Arch and visitor
  installation metadata

**Preview parity — page surgery (M1)**
- [x] Page ops: `rotate_pages`, `delete_pages`, `move_pages`, `insert_pages`,
  `extract_pages` — engine, CLI (`omepreview pages`), MCP, tests
- [x] GTK thumbnail sidebar: multi-select, drag reorder, delete/rotate
  shortcuts, context menu, file-drop insert, extract dialog, scratch preview
- [x] Two-window page clipboard (`application/x-omepreview-pages` + PDF fallback; legacy `application/x-omapdf-pages` still pastes);
  Ctrl+C/V/X when sidebar focused

**Preview parity — safe share (M2)**
- [x] True `redact` op (text + image pixels removed, verify hook)
- [x] CLI `omepreview redact`, MCP `redact` (dry-run default)
- [x] GTK redact tool: text-snap and free-rectangle modes; ghosts until Save;
  default `*_redacted.pdf` save-as-copy
- [x] Documented: **pen/ink is not redact**

**Preview parity — daily driver (M3)**
- [x] GUI form field hit-test → `fill_field` on Save
- [x] `delete_annotation` op + Delete on selected saved markup
- [x] CLI `omepreview delete-annotation`, MCP `delete_annotation` (dry-run default)
- [x] Manual checklist: [docs/preview-parity.md](preview-parity.md)

## Current source — not yet released

- [x] CLI/MCP application workflows, including editor sessions and exports:
  [workflow contract](ops.md#application-workflows).
- [x] Optional OCRmyPDF engine and checkout installation. OCR creates a verified
  searchable copy through the shared operation: [Wave 1 evidence](evidence/ocr-wave1-2026-09-16.md).
- [ ] OCR CLI/MCP progress, cancellation and agent verification recipes.
- [ ] Native Recognize text workflow with safe saved-copy handoff.
- [ ] Combined OCR interface acceptance. The [shared plan](../index.md) owns batch status.

## Next

- **`omepreview sign --auto`** — signature-line detection: "Signature:"/"Sign
  here" labels, ruled lines, signature-type fields → ranked placement
  proposals; agents and the editor both consume them as ghosts
- **Comments summary page** — append a final page listing every comment
  with its page number, for recipients with weak viewers or paper
- **Stamp library** — APPROVED / DRAFT / PAID / initials as a `stamp` op
  and an editor picker
- **`omepreview diff a.pdf b.pdf`** — agent-friendly version comparison
- **Toolbar overflow menu** — collapse tools into ⋯ at narrow widths
- **Selection → agent context** — "ask about this selection" sends the
  selected region's text along with the question

## Later

- **Cryptographic signing (v0.4)** — PAdES via pyHanko as an optional
  extra: certificates, visible + cryptographic signature in one op,
  `omepreview verify`. Kept clearly distinct from visual signing in the UX.
- **P2 editor ops** — shapes (line, arrow, rect, oval), crop page,
  encrypt/permissions on save-as, outline sidebar, two-page view,
  paranoid raster redact mode, print action

## Distribution

1. GitHub ([Skeptomenos/omepreview](https://github.com/Skeptomenos/omepreview)), AUR package
   (`packaging/PKGBUILD`) — see [packaging/SMOKE-aarch64.md](../packaging/SMOKE-aarch64.md)
   for aarch64/Omarchy smoke steps
2. Omarchy plugin listing for the bar widget
3. Pitch to the omarchy package repo once polished (the omasnap path)
