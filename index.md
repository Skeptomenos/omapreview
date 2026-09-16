# omapreview — index

- User-facing name and command: **omapreview**. Python package: `omepreview`. Version **0.1.1**.
- Linear: `omepreview` (AI Development) — https://linear.app/helmus/project/omapdf-27d4875d9078
- GitHub: [`Skeptomenos/omapreview`](https://github.com/Skeptomenos/omapreview) (`Skeptomenos/omapdf` redirects here). Forked from [`pbergin11/omapdf`](https://github.com/pbergin11/omapdf).
- Branch: `main`.
- Visitor install:

```bash
curl -fsSL https://github.com/Skeptomenos/omapreview/releases/download/v0.1.1/install.sh | bash
```

- Launcher: Super+Space → empty editor; Open / Ctrl+O picks a PDF. Desktop `Exec=omapreview edit %f`.
- Entry docs: `README.md`, `docs/ops.md`, `docs/omapdf-preview-parity-spec.md`, `docs/omapdf-preview-gap-analysis.md`.
- Before assessing UI/CLI/MCP coverage: [interface parity audit, 2026-09-16](docs/evidence/interface-parity-2026-09-16.md) maps available features, documentation gaps and synthetic CLI/MCP protocol evidence at `5845979`.
- [Verifiable CLI/MCP contract](docs/ops.md) defines the shipped operations, page rendering, and post-save verification routes.
- [Progressive agent skill](skill/SKILL.md) defines the shipped independent CLI/MCP modes, scenario recipes, and fresh-context validation.
- Before using current-source application workflows: read the [workflow matrix and safety contract](docs/ops.md#application-workflows). [DEV-195](https://linear.app/helmus/issue/DEV-195) tracks the CLI/MCP expansion. These routes are not in the published v0.1.1 release.
- Before scoping review fixes: [review recheck, 2026-09-15](docs/evidence/review-recheck-2026-09-15.md) records which R01–R26 findings reproduce at `784d158` and the recommended work boundaries.
- Before dispatching or continuing review work: every conversation reads and updates the [single shared plan](/home/david/.local/state/omapreview/coordination/review-remediation.md), outside all worktrees. It owns waves, batch scopes, independent GUI/CLI/MCP validation, progress and the conflict-safe update protocol.

- Before performance work: [R23 measurements, 2026-09-16](docs/evidence/batch-g-r23-2026-09-16.md) records thumbnail, search, page-operation, history-memory and recorder probes, with the next bounded improvement.

- Before OCR implementation: read the [OCR contract and synthetic acceptance corpus](docs/ocr-contract.md), then the [opt-in OCRmyPDF waves in the shared plan](/home/david/.local/state/omapreview/coordination/review-remediation.md#opt-in-ocrmypdf--implementation-waves-2026-09-16). The contract defines the first increment; the plan owns batch scope, validation and dependencies. OCR is not implemented by the contract batch. [DEV-188](https://linear.app/helmus/issue/DEV-188/opt-in-ocrmypdf-integration-across-gui-cli-and-mcp) tracks the work; the [feasibility assessment](/home/david/.local/state/omapreview/coordination/evidence/ocr-feasibility/assessment.md) contains local OCR probes and limits.
