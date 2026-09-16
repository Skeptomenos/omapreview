# Changelog

## [0.2.0] — 2026-09-16

### Added

- Optional OCRmyPDF integration makes scanned PDFs searchable in the GUI, CLI and MCP. Select pages and installed languages, then save a new searchable copy.
- Native **Recognize text** panel and Ctrl+Shift+O shortcut, with preflight, progress, cancellation and an explicit copy handoff when editing history must be kept.
- CLI/MCP OCR capability checks, source-bound confirmation, cancellation and saved-output verification. OCR reports skipped or incomplete pages instead of treating every output as successful recognition.
- Shared application workflows for search, signature libraries, exports, live editor sessions, proposals, history, recording and clipboard/external-app handoffs. Discover them with `workflow-schema` or MCP `workflow_schema`.
- Release installer options `--with-ocr` and `--with-mcp`. The GUI install also provides the CLI and command aliases.
- OCR-specific agent recipes for CLI and MCP, with progressive disclosure and explicit verification steps.

### Fixed

- OCR redaction now covers faint scan-edge pixels outside tight text boxes while preserving neighboring glyphs, including diagonal neighbors. Saved hidden text and image content are checked independently.
- Real editor-session responses correctly return OCR task records, including success, failure and cancellation.
- Ordinary MCP edits no longer block the server event loop during OCR.
- OCR preparation and child processes stop on cancellation or editor close. Opening a completed copy preserves the user's control over pending edits and undo history.
- Pending crop targets, workflow snapshots and recording lifecycle checks preserve the intended document and targets.

### Installation and compatibility

- OCR and MCP are independent optional extras. Base GUI/CLI use still needs only the core dependencies.
- OCR needs Tesseract, installed language data and Ghostscript. The supported Python backend is OCRmyPDF `>=17.11.0,<17.12`; MCP requires SDK `>=2.2.0`.
- OCR writes a new file and rejects an existing destination, encrypted input, digital signatures and unapplied redactions. Force/redo OCR, handwriting recognition and automatic image cleanup are outside this release.
- Existing consequential-action confirmation rules remain: inspect the proposal before passing CLI `--confirm` or the equivalent MCP confirmation.

## [0.1.1] — 2026-09-16

- Added MCP page rendering, operation discovery and verification-focused CLI/MCP guidance.
- Improved atomic saves, signature import/export, undo history, rotated/cropped coordinates, forms and page operations.
- Refreshed the README with real GUI screenshots and the application icon. The desktop launcher opens an empty editor.
- Corrected undo/redo icons to use upper-left and upper-right arrows.

[0.2.0]: https://github.com/Skeptomenos/omapreview/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Skeptomenos/omapreview/releases/tag/v0.1.1
