# omapreview — index

- User-facing name and command: **omapreview**. Python package: `omepreview`. Version **0.1.0**.
- Linear: `omepreview` (AI Development) — https://linear.app/helmus/project/omapdf-27d4875d9078
- GitHub: [`Skeptomenos/omapreview`](https://github.com/Skeptomenos/omapreview) (`Skeptomenos/omapdf` redirects here). Forked from [`pbergin11/omapdf`](https://github.com/pbergin11/omapdf).
- Branch: `main`.
- Visitor install:

```bash
curl -fsSL https://github.com/Skeptomenos/omapreview/releases/download/v0.1.0/install.sh | bash
```

- Launcher: Super+Space → empty editor; Open / Ctrl+O picks a PDF. Desktop `Exec=omapreview edit %f`.
- Entry docs: `README.md`, `docs/ops.md`, `docs/omapdf-preview-parity-spec.md`, `docs/omapdf-preview-gap-analysis.md`.
- Before assessing UI/CLI/MCP coverage: [interface parity audit, 2026-09-16](docs/evidence/interface-parity-2026-09-16.md) maps available features, documentation gaps and synthetic CLI/MCP protocol evidence at `5845979`.
- After green Wave 1 integration: [Batch H implementation brief](docs/cli-mcp-verification-batch.md) defines MCP page rendering, operation discovery and required agent verification. Dispatch and live progress remain in the single shared plan.
- After H lands: [Batch I implementation brief](docs/agent-skill-batch.md) defines the skill's progressive disclosure, independent CLI/MCP modes, scenario recipes and fresh-context validation.
- Before scoping review fixes: [review recheck, 2026-09-15](docs/evidence/review-recheck-2026-09-15.md) records which R01–R26 findings reproduce at `784d158` and the recommended work boundaries.
- Before dispatching or continuing review work: every conversation reads and updates the [single shared plan](/home/david/.local/state/omapreview/coordination/review-remediation.md), outside all worktrees. It owns waves, batch scopes, independent GUI/CLI/MCP validation, progress and the conflict-safe update protocol.
