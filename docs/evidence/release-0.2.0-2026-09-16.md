# omapreview 0.2.0 release and installation

Dated evidence: 2026-09-16, omarchy-air (Arch Linux aarch64).

## Outcome

- Published [v0.2.0](https://github.com/Skeptomenos/omapreview/releases/tag/v0.2.0) at `c853a94b2602f230b09b972bd524f3e16525b56f`.
- Updated the README, added CHANGELOG.md, and shipped optional OCR/MCP installer flags.
- Installed the public release with `bash install.sh --with-ocr --with-mcp` in `/home/david/.local/share/omapreview`. Exit 0; no sudo needed.
- `omapreview --version` returned `omapreview 0.2.0`. All four CLI/MCP command links resolve to the installed venv. Desktop Exec points there too.
- `omapreview ocr-status` returned available=true: OCRmyPDF 17.11.0, Tesseract 5.5.3, Ghostscript 10.08.0. MCP SDK is 2.2.0; PyMuPDF is 1.28.2.
- `xdg-mime query default application/pdf` still returns `omapreview.desktop`.
- v0.1.1 remains at `e6d6f2494cd9c7a831b898dce8b9aff20ff048e7`; no prior release assets changed. No PyPI or AUR publication.

## Release checks

`.venv/bin/python -m pytest tests/ -q` completed with **638 passed, no skips, exit 0** at `46127bf`. It ran under bwrap with private Downloads/XDG app state and the real OCR backend. HOME and the working Wayland runtime remained unchanged. Final release runtime, tests and dependencies are identical; subsequent changes corrected two documentation sentences and source checksum pins. Final packaging tests: **13 passed**.

An independent archive review accepted all 93 selected source files and all 35 runtime Python modules in the wheel and sdist. Bytes and executable bits match the release commit. Git archive adds group-write bits to full modes. Version and all four entry points are correct. Rebuilding the source archive from the final candidate produced identical bytes.

The exact final wheel, then the actual host install, each passed the same synthetic CLI and real stdio MCP journey:

1. Preflight, source-hash confirmation and selected-page OCR to a new copy.
2. Read recognized text and render the result.
3. Redact an exact text match without caller-added padding.
4. Reopen the saved output independently. Confirm target hidden text is absent and target scan pixels are cleared; nearby text/image content and the control page remain unchanged.
5. Confirm the original file hash is unchanged.

Native installed GUI launch through Cua displayed the empty editor, Open PDF and Recognize text controls. Screenshot and accessibility evidence were inspected. Existing user document windows were not operated on. The full native OCR → text-snap redaction → Save → reopen acceptance is recorded in [OCR redaction evidence](ocr-redaction-gui-2026-09-16.md); the release runtime contains that verified implementation.

## Limits and follow-up

- Empty-canvas tooltip lookup emits a nonfatal `no PDF open` traceback. [DEV-198](https://linear.app/helmus/issue/DEV-198/guard-empty-editor-tooltip-before-page-lookup) tracks the missing early document guard. This is not a clean GUI-log result.
- A Cua background Open PDF action did not establish a file-dialog postcondition. No installed Open-dialog interaction is claimed. Cua refused background/foreground close input on this desktop and rejected termination as foreign-process ownership. Stopping the Cua service then removed its validation processes (PIDs 883338 and 883340); process readback confirmed both gone. No validation window remains. No shell UI workaround was used.
- OCR accuracy still requires review. Synthetic acceptance does not certify arbitrary scans.

## Artifact hashes

| Asset | SHA-256 |
|---|---|
| install.sh | `a3f1d6c6bf0455d3c1d285f446f2020bcf4f81b2e75020d7ff64b7d00ffefeb5` |
| omapreview-0.2.0-src.tar.gz | `dc3f9a208ab9d3b9059ed222be470294e35a4146d2f69e9b9cc296f9707e8b7f` |
| omepreview-0.2.0-py3-none-any.whl | `9c7de16a44c606850e70150cf748a657e1b202d486dc62341561d41a236dbfc5` |
| omepreview-0.2.0.tar.gz | `142d5b42c100e42de0507a43f27e178b68f7371c2836f8d66288e512dc8ba517` |

`gh release download v0.2.0` followed by `sha256sum -c SHA256SUMS` passed. All five public assets, including SHA256SUMS, are byte-identical to the accepted local artifacts. The visitor installer fetched and verified the public source asset again during installation. Its locally built wheel hash also matched the published wheel.

## Local records and rollback

Evidence root: `/home/david/.local/state/omapreview/coordination/evidence/release-0.2.0/`.

- `final-gate/command-result.json`, `final-gate/pytest.log`: full-suite command and output.
- `final-assets.json`, `published-verification.json`: provenance and downloaded hashes.
- `final-wheel-smoke/results.json`, `host-smoke/results.json`: both installed CLI/MCP journeys.
- `host-install.log`, `host-install-result.json`, `host-verified.json`: install and readback.
- `gui-empty.json`, `gui-empty.png`, `gui.log`: native capture and tooltip failure.
- `host-backup-0.1.1/`: private pre-upgrade prefix, desktop entry, icon and command-link/default-handler manifest. Restore the saved prefix to its original path if rollback is needed; venv entry-point paths depend on that location.

Release tracking: [DEV-197](https://linear.app/helmus/issue/DEV-197/publish-and-install-omapreview-020). Shared plan section: `release020`.
