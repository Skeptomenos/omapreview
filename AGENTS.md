# omapreview

- `Ownership-ID: Personal`. Load `ownership-profile-personal` when not already present.
- User-facing name and command: **omapreview**. Python package remains `omepreview`.
- Agent-native PDF studio: GTK4 editor + CLI + MCP server over one PyMuPDF op engine.
- Visitor README is the product front door: problem, why, install, use — keep it true.

Canonical playbook. `CLAUDE.md` is a symlink to this file.

## Commands

- Setup: `python -m venv --system-site-packages .venv` — plain venv hides system `gi`, breaks the editor.
- Install (checkout): `.venv/bin/pip install -e '.[dev]'` or `bash packaging/install-user.sh`
- Visitor (Arch/Omarchy, no clone): `curl -fsSL https://github.com/Skeptomenos/omapreview/releases/download/v0.1.1/install.sh | bash`
- Test: `.venv/bin/python -m pytest tests/ -q` — must exit 0. Generates own PDFs, runs <1s.
- Editor: `omapreview edit` opens **empty**; `omapreview edit file.pdf` opens that file. Open from the app is Ctrl+O. Desktop `Exec` is `omapreview edit %f` — do not put a file picker in Exec.

## Navigate

- `src/omepreview/ops.py` schema, `src/omepreview/engine.py` sole write-path (`apply()`), `src/omepreview/cli.py`, `src/omepreview/mcp_server.py`.
- `src/omepreview/gui.py` editor — pending ghosts in `Editor.pending`, `Editor.to_ops()` on Save; empty launch + in-app Open; rendering notes in `gui/README.md`.
- `docs/ops.md` stable op contract, `docs/roadmap.md` status, `docs/omapdf-preview-parity-spec.md` + `docs/omapdf-preview-gap-analysis.md` page-surgery plan.
- `tests/test_engine.py`, `skill/SKILL.md` agent playbook, `shell-plugin/omapdf.bar` bar widget, `packaging/PKGBUILD`, `packaging/install.sh`.
- Live issue/branch notes: `index.md` (commit when the compass changes).

## Bindings

- Linear: `omepreview` (AI Development). Live issue/branch: `index.md`.

## Rules

- New capability = new op: schema in `ops.py` + applier in `engine.py` + spec in `docs/ops.md` + test — keeps CLI, MCP, GUI in sync.
- Coordinates are PDF points, top-left origin, 1-based pages everywhere — `read` bboxes pass straight back as targets.
- Core stays stdlib + PyMuPDF; `mcp` remains an optional extra — keeps the Arch package lean.
- Errors state what failed and what to do next — agents read them (see `fill_field` unknown-field error).
- Consequential actions default to dry-run/propose; the caller confirms — never auto-commit sign/flatten/redact.
- GUI builds ops and Saves through the engine; no GUI-only writes — preserves human/agent parity.
- License is AGPL-3.0-or-later — match it; prefer reimplementation with citation over copying GPL code.
- Speak **omapreview** to users. Do not ship launcher file dialogs. Do not commit PII PDFs.

## Done here

- `pytest` green; new op ships all four parts; hand back files, test output, and gaps.
