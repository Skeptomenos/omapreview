# README redesign evidence

This note records the provenance of the README visual refresh. All document
content used for the screenshots is synthetic.

## Visual direction

- Reference inspected: `/home/david/Downloads/Codex Image Sep 15, 2026, 11_54_27 PM.png`.
- Role: style reference only. It informed the warm paper, navy, blue, and
  orange editorial treatment. It is not embedded and is not product evidence.

## Generated asset

- `docs/assets/readme-hills.jpg` was generated with the built-in image tool and
  optimized from its lossless PNG output for repository size.
- Prompt: “Create a quiet editorial illustration of layered abstract hills
  and a low sun, suggesting calm, careful document work. Warm off-white paper,
  three blue-gray hill layers, one muted orange sun, sparse pale-blue contour
  lines, refined flat editorial print illustration, wide horizontal crop, no
  text, logos, interface, watermark, people, or photorealistic scenery.”
- The asset is used as document artwork and a decorative README banner. It is
  not a screenshot of omapreview.
- SHA-256: `728d37e5f1e40d845e7b2b57e01e38693252c3583cc64ce43038674f982d91b9`.

## Synthetic fixture

- `docs/demo/make_demo_pdf.py` creates `docs/demo/omapreview-demo.pdf` with
  PyMuPDF and the generated illustration. It contains three designed pages,
  synthetic copy, and no personal data.
- Rebuild with:

  ```bash
  python docs/demo/make_demo_pdf.py
  ```

- The checked-in fixture is a 3-page PDF. SHA-256:
  `b85585753ea5db2c2e4281ac75aecf7a7f4ead40eaba800b1e1671e959048fd1`.

## Screenshots

- `docs/screenshots/omapreview-editor.png` is a clean page-1 editor view with
  the thumbnail rail and tool rail visible (922x1182 PNG).
- `docs/screenshots/omapreview-proposal.png` shows the same real editor with a
  highlight, sticky-note, and redaction proposal as visible ghosts (1568x978
  PNG).
- Both were captured on 2026-09-15 UTC from commit
  `5845979789a2552e48847af0fd725f65c40de9ae` using the worktree executable:

  ```text
  /home/david/.codex/worktrees/fb88/omapreview/.venv/bin/python -m omepreview.cli edit
  ```

  Cua window snapshots bound PID 151937 / window 187653285890288 and PID
  152845 / window 187653286384256. No generative UI, mockup, or synthetic
  application chrome was used.
- The two synthetic editor processes were cooperatively closed as far as the
  driver allowed, then gracefully terminated by exact PID after the driver
  refused cross-transport force-close. Both were verified absent.

SHA-256: `9ab9f6fb53a89f3ff96b8c59496ba9e1ddfa4f0e93be611068bd9e5e4ca2994a`
for `omapreview-editor.png`; `2a81e6c8108b7d18fa20d5fc8089d748653597c8b29e6c79c57b35bd78d502b4`
for `omapreview-proposal.png`.

## Validation

- README local asset links resolve, `git diff --check` is clean, and the
  fixture generator compiles.
- CLI checks passed for `--version`, `read --json`, `apply --dry-run --json`,
  `apply`, `redact`, `pages --list`, and editor help. The optional MCP server
  starts and exits cleanly on closed standard input.
- The required suite reports `238 passed, 1 failed`: the inherited GTK
  clipboard test fails in `src/omepreview/page_clipboard.py` because the
  installed GTK binding rejects the string MIME type. This README-only change
  does not touch that code path.
