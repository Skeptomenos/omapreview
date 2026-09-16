# omapreview — index

- User-facing name and command: **omapreview**. Python package: `omepreview`. Version **0.2.0**.
- Linear: `omepreview` (AI Development) — https://linear.app/helmus/project/omapdf-27d4875d9078
- GitHub: [`Skeptomenos/omapreview`](https://github.com/Skeptomenos/omapreview) (`Skeptomenos/omapdf` redirects here). Forked from [`pbergin11/omapdf`](https://github.com/pbergin11/omapdf).
- Branch: `main`.
- Visitor install:

```bash
curl -fsSL https://github.com/Skeptomenos/omapreview/releases/download/v0.2.0/install.sh | bash
```

- Launcher: Super+Space → empty editor; Open / Ctrl+O picks a PDF. Desktop `Exec=omapreview edit %f`.
- Entry docs: `README.md`, `docs/ops.md`, `docs/omapdf-preview-parity-spec.md`, `docs/omapdf-preview-gap-analysis.md`.
- Before assessing UI/CLI/MCP coverage: [interface parity audit, 2026-09-16](docs/evidence/interface-parity-2026-09-16.md) maps available features, documentation gaps and synthetic CLI/MCP protocol evidence at `5845979`.
- [Verifiable CLI/MCP contract](docs/ops.md) defines the shipped operations, page rendering, and post-save verification routes.
- [Progressive agent skill](skill/SKILL.md) defines the shipped independent CLI/MCP modes, scenario recipes, and fresh-context validation.
- Before using current-source application workflows: read the [workflow matrix and safety contract](docs/ops.md#application-workflows). [DEV-195](https://linear.app/helmus/issue/DEV-195) tracks the CLI/MCP expansion. These routes are included in v0.2.0.
- Before scoping review fixes: [review recheck, 2026-09-15](docs/evidence/review-recheck-2026-09-15.md) records which R01–R26 findings reproduce at `784d158` and the recommended work boundaries.
- Before dispatching or continuing review work: every conversation reads and updates the [single shared plan](/home/david/.local/state/omapreview/coordination/review-remediation.md), outside all worktrees. It owns waves, batch scopes, independent GUI/CLI/MCP validation, progress and the conflict-safe update protocol.

- Before performance work: [R23 measurements, 2026-09-16](docs/evidence/batch-g-r23-2026-09-16.md) records thumbnail, search, page-operation, history-memory and recorder probes, with the next bounded improvement.

- Before OCR work: read the [OCR contract and synthetic acceptance corpus](docs/ocr-contract.md), the [shared operation](docs/ops.md#ocr), then the [opt-in OCRmyPDF waves in the shared plan](/home/david/.local/state/omapreview/coordination/review-remediation.md#opt-in-ocrmypdf--implementation-waves-2026-09-16). The engine and optional checkout installation passed [Wave 1 acceptance](docs/evidence/ocr-wave1-2026-09-16.md). CLI/MCP lifecycle, progressive agent recipes and native Recognize text passed [combined acceptance](docs/evidence/ocr-wave2-2026-09-16.md), including the real editor bridge and 627 tests. [DEV-188](https://linear.app/helmus/issue/DEV-188/opt-in-ocrmypdf-integration-across-gui-cli-and-mcp) tracks the work. OCR is included as an optional extra in v0.2.0.
- Before assessing OCR redaction safety: [native OCR redaction acceptance](docs/evidence/ocr-redaction-gui-2026-09-16.md) records the actual OCR → text-snap redaction → Save → reopen journey, a fix for scan-edge remnants, independent saved-image/text checks, neighboring-content protection and 635 passing tests. [DEV-196](https://linear.app/helmus/issue/DEV-196/verify-gui-redaction-removes-ocr-text-and-scan-pixels) tracks this follow-up; its evidence supersedes the earlier padded test for the default GUI selection path.

- v0.2.0 is published and installed on omarchy-air with CLI, OCR and MCP. [Release/install evidence](docs/evidence/release-0.2.0-2026-09-16.md) records 638 passing tests, public artifact hashes, installed CLI/MCP checks and native GUI limits. [DEV-197](https://linear.app/helmus/issue/DEV-197/publish-and-install-omapreview-020) tracks delivery; [DEV-198](https://linear.app/helmus/issue/DEV-198/guard-empty-editor-tooltip-before-page-lookup) tracks a nonfatal empty-window tooltip error. [Changelog](CHANGELOG.md) records shipped user-facing changes.
