<p align="center">
  <img src="share/icons/omapreview.png" alt="omapreview app icon" width="128" />
</p>

# omapreview

**Human-friendly, agent-native PDF review for Linux.**

omapreview is a focused GTK4 workspace for reading, annotating, signing,
redacting, and rearranging PDFs. It is built for Omarchy and works on plain
GTK Linux too.

<p align="center">
  <img src="docs/assets/readme-hills.jpg" alt="Layered blue hills on warm paper" width="760" />
</p>

<p align="center">
  <img src="docs/screenshots/omapreview-editor.png" alt="omapreview GTK4 editor showing a three-page synthetic document" width="820" />
</p>

<p align="center"><sub>Real GTK4 editor pixels. The document is a reproducible synthetic fixture; no personal PDFs are used.</sub></p>

## One workbench, two ways to work

| For people | For agents |
| --- | --- |
| Open a PDF, mark it up, nudge proposed changes, and Save when the page looks right. | Read structured text and bboxes, build JSON ops, and run the same engine through the CLI or optional MCP server. |

The editor keeps proposed changes visible as movable ghosts until Save. The
CLI and MCP server use the same JSON operation contract, so a human can review
what an agent proposes before it is written.

## Read, mark, hand off

<p align="center">
  <img src="docs/screenshots/omapreview-proposal.png" alt="omapreview showing proposed highlight and note ghosts over the synthetic document" width="820" />
</p>

### Review

Open an empty editor from the Omarchy launcher, or open a file directly. Use
the page rail and thumbnails to move through the document. Search is available
with Ctrl+F.

### Annotate

Highlight, underline, strike out, add notes, draw with ink, add shapes, place
saved signatures, fill form fields, crop, redact, and manage pages. Redaction
removes the matched content; a black drawing is not a redact.

### Automate

`omapreview read` returns text, layout, annotations, fields, and bboxes.
Coordinates are PDF points with a top-left origin and pages are 1-based. Use a
bbox from `read` as a precise target for an operation.

## Install

### Arch / Omarchy

The visitor installer fetches the v0.1.1 release, installs its Arch
dependencies, and adds the command and desktop entry for your user:

```bash
curl -fsSL https://github.com/Skeptomenos/omapreview/releases/download/v0.1.1/install.sh | bash
```

Then open **omapreview** from the launcher with Super+Space. The editor starts
empty. Open a PDF with the in-app **Open PDF** button or Ctrl+O.

### From a checkout

```bash
git clone https://github.com/Skeptomenos/omapreview.git
cd omapreview
bash packaging/install-user.sh
```

For a development install, keep system GTK bindings visible:

```bash
python -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/omapreview edit
```

### Optional OCR setup from current source

The published v0.1.1 installer and Arch source asset have no OCR action. The
following setup is for the current-source OCR candidate. In a checkout venv,
run `.venv/bin/pip install -e '.[ocr]'` (or `'.[ocr,mcp]'` for both) to keep an
existing user installation unchanged. To update the user command and desktop
entry from that checkout, enable OCR, MCP, or both with the installer:

```bash
bash packaging/install-user.sh --with-ocr
# or: bash packaging/install-user.sh --with-mcp
# or: bash packaging/install-user.sh --with-ocr --with-mcp
```

The checkout installer writes `~/.local/bin/omapreview` and the user desktop
entry, so it replaces those links if they already exist. The OCR extra installs
OCRmyPDF `>=17.11.0,<17.12`; only 17.11.0 is validated on Linux aarch64 with
Python 3.14.7. Basic GUI and CLI installation does not require OCR. MCP is a
separate extra.

OCR also needs Tesseract, installed language data and a PDF rasterizer. On
Arch / Omarchy, check the available packages with
`pacman -Si tesseract tesseract-data-eng ghostscript`, then install any missing
packages yourself. For another language, choose its matching
`tesseract-data-<code>` package. No installer downloads language data or runs
privileged OCR setup. List the languages actually available to Tesseract with
`tesseract --list-langs`. Check the backend version with
`.venv/bin/python -c 'from importlib.metadata import version; print(version("ocrmypdf"))'`.
If a tool or language is missing, install it and rerun these checks before OCR.

The Arch package definitions keep the v0.1.1 source URL and checksum. They
list the system OCR tools as optional guidance for a future OCR release; they
do not install OCRmyPDF or enable OCR in v0.1.1.

### Recognize text in the current-source editor

Open a PDF, then choose **Recognize text** in the editor rail or press
Ctrl+Shift+O. Choose pages, installed OCR languages, and a new PDF path.
**Review** checks the saved source and shows the destination. **Recognize
text** starts the approved request. The editor stays responsive and shows
phase progress and Cancel. A verified copy opens automatically if the editor
has not changed. If it has changed, the copy stays saved and **Open copy**
lets you choose when to switch documents. Save, discard, or cancel pending
edits before starting or opening the copy.

The original is kept. Search the copy and review recognized text against the
page image. OCR reports pages it skipped or could not recognize; it does not
certify transcription accuracy. This editor action is in current source and
is absent from the published v0.1.1 release.

## Make omapreview your default PDF editor

After installing on Arch / Omarchy, run this as your normal user, without sudo:

```bash
xdg-mime default omapreview.desktop application/pdf
```

Check the default:

```bash
xdg-mime query default application/pdf
```

The result should be `omapreview.desktop`. PDFs opened from your file manager
or with `xdg-open` will then use omapreview. The installer leaves your existing
default unchanged until you choose to change it.

## Editor shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+O | Open a PDF |
| Ctrl+S | Save pending ghosts through the engine |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo, including saves |
| Ctrl+F | Search |
| Ctrl+Shift+O | Recognize text in a new copy (current source) |
| F9 | Toggle thumbnails |
| R | Redact tool |
| Space / Enter (signature pad) | Arm and save a trackpad signature |

## CLI

```bash
omapreview edit document.pdf
omapreview edit document.pdf --ops proposal.json
omapreview read document.pdf --json
omapreview annotate document.pdf --page 1 --match "renewal date" -o marked.pdf
omapreview redact document.pdf --page 1 --match "PRIVATE TOKEN" -o redacted.pdf
omapreview pages document.pdf --list
```

For a batch of mixed edits, put the operation list in JSON:

```json
[
  {"op": "highlight", "page": 1, "match": "renewal date"},
  {"op": "note", "page": 1, "at": [420, 180], "text": "Check this before signing."}
]
```

```bash
omapreview apply document.pdf --ops edits.json -o reviewed.pdf
```

Use `--dry-run --json` when you want resolved geometry without writing an
output. See the [operations spec](docs/ops.md) for the complete contract.

## MCP

The GUI installation also installs the CLI. The release and checkout installers
link `omapreview` and the compatible `omepreview` alias in `~/.local/bin`.
The Arch package installs both in `/usr/bin`. No shell setup files are changed.
If your terminal cannot find the command, use `~/.local/bin/omapreview`, or run
`export PATH="$HOME/.local/bin:$PATH"` for the current shell.

MCP is optional. After the release installer, enable it without a checkout:

```bash
~/.local/share/omapreview/venv/bin/python -m pip install 'mcp>=2.2.0'
~/.local/share/omapreview/venv/bin/omapreview-mcp
```

For a checkout, use `.venv/bin/pip install -e '.[mcp]'`, then
`.venv/bin/omapreview-mcp`. The current-source MCP lifecycle requires SDK
2.2.0 or newer. For an Arch package install, use `python-mcp` only if
`pacman -Si python-mcp` reports version 2.2.0 or newer. The tested Arch ARM
repository has 1.29.0, so use a checkout venv with `.[mcp]` there instead.
Configure the absolute server path in an MCP client.
New installers also link both `omapreview-mcp` and `omepreview-mcp` into
`~/.local/bin`; current source additionally supports `omapreview mcp`.
The base CLI works without MCP.

The current source adds workflow coverage beyond v0.1.1's document operations:
search with hit geometry, signature-library management, live editor proposals
and history, recording handoff, exports, and clipboard/external-app handoffs.
These additions are pending release; the published v0.1.1 installer does not
contain them yet. Discover the exact contract with `omapreview workflow-schema`
or MCP `workflow_schema`, then use `workflow` or `run_workflow`. See the
[workflow matrix and safety contract](docs/ops.md#application-workflows).
Consequential edits default to proposals. Sharing requires explicit confirmation;
an external-app handoff does not mean the file was delivered.

See the [agent playbook](skill/SKILL.md) for PDF task workflows.

## Project notes

- User-facing command: `omapreview`. Python package: `omepreview`.
- Desktop entry: `omapreview edit %f`; it does not open a launcher file picker.
- Saved trackpad signatures go to `~/Downloads/omapreview/signature/`.
- The editor, CLI, and MCP share the core PyMuPDF operation engine for supported operations.
- The project is licensed under [AGPL-3.0-or-later](LICENSE).

The screenshot fixture and asset provenance are recorded in
[`docs/evidence/readme-redesign-2026-09-16.md`](docs/evidence/readme-redesign-2026-09-16.md).
