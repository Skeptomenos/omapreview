<p align="center">
  <img src="share/icons/omapreview.png" alt="omapreview app icon" width="128" />
</p>

# omapreview

**Human-friendly, agent-native PDF review for Linux.**

omapreview is a focused GTK4 workspace for reading, annotating, signing,
redacting, and rearranging PDFs. It is built for Omarchy and works on plain
GTK Linux too.

**New in 0.2.0:** searchable scans with optional OCR, richer CLI/MCP workflows,
and verified OCR redaction. [Read the changelog](CHANGELOG.md).

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

The visitor installer fetches the v0.2.0 release, installs its Arch
dependencies, and adds the command and desktop entry for your user:

```bash
curl -fsSL https://github.com/Skeptomenos/omapreview/releases/download/v0.2.0/install.sh | bash
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

### Optional OCR and MCP

Enable OCR, MCP, or both during installation:

```bash
curl -fsSL https://github.com/Skeptomenos/omapreview/releases/download/v0.2.0/install.sh | bash -s -- --with-ocr --with-mcp
```

Use only `--with-ocr` or `--with-mcp` if you need one extra. The basic install
includes the GUI and CLI. OCR and MCP remain optional. For a checkout, use
`bash packaging/install-user.sh --with-ocr --with-mcp`.

OCR uses OCRmyPDF 17.11.x with Tesseract and Ghostscript. Install the system
OCR tools and the languages you need before enabling recognition. On Arch:

```bash
sudo pacman -S --needed tesseract tesseract-data-eng ghostscript
tesseract --list-langs
```

Choose a matching `tesseract-data-<code>` package for another language. The
installer does not download OCR language data or perform privileged OCR setup.
Check available recognition support with `omapreview ocr-status`.
OCRmyPDF 17.11.0 is validated on Linux aarch64 with Python 3.14.7.

The Arch package recipes include the application and list the system OCR tools
as optional dependencies. For an installation that manages the supported
Python OCR backend too, use the visitor installer with `--with-ocr`.

### Recognize scanned text

Open a PDF, then choose **Recognize text** in the editor rail or press
Ctrl+Shift+O. Choose pages, installed OCR languages, and a new PDF path.
**Review** checks the saved source and shows the destination. **Recognize
text** starts the approved request. The editor stays responsive and shows
phase progress and Cancel. A verified copy with recognized text opens automatically
if the editor has not changed and has no undo history. Otherwise, the copy stays saved and **Open copy**
lets you choose when to switch documents. Save, discard, or cancel pending
edits before starting or opening the copy.

The original is kept. Search the copy and review recognized text against the
page image. OCR reports pages it skipped or could not recognize; it does not
certify transcription accuracy. You can then search, highlight or redact the
recognized text. Redaction removes both the hidden text and the selected scan
pixels; inspect the saved copy to verify the result.

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
| Ctrl+Shift+O | Recognize text in a new copy |
| F9 | Toggle thumbnails |
| R | Redact tool |
| Space / Enter (signature pad) | Arm and save a trackpad signature |

## CLI

```bash
omapreview edit document.pdf
omapreview edit document.pdf --ops proposal.json
omapreview read document.pdf --json
omapreview annotate document.pdf --page 1 --match "renewal date" -o marked.pdf
omapreview redact document.pdf --page 1 --match "PRIVATE TOKEN" -o redacted.pdf --confirm
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

Use `--dry-run --json` to inspect resolved geometry without writing an output.
Signing, redaction and deletion require `--confirm`; review the proposal first.
After a write, read the saved file and use `omapreview snapshot` to inspect its
appearance. See the [operations spec](docs/ops.md) for the complete contract.

## MCP

The GUI installation also installs the CLI. The release and checkout installers
link `omapreview` and the compatible `omepreview` alias in `~/.local/bin`.
The Arch package installs both in `/usr/bin`. No shell setup files are changed.
If your terminal cannot find the command, use `~/.local/bin/omapreview`, or run
`export PATH="$HOME/.local/bin:$PATH"` for the current shell.

MCP is optional. Add `--with-mcp` to the installer, or enable it later without a checkout:

```bash
~/.local/share/omapreview/venv/bin/python -m pip install 'mcp>=2.2.0'
~/.local/share/omapreview/venv/bin/omapreview-mcp
```

For a checkout, use `.venv/bin/pip install -e '.[mcp]'`, then
`.venv/bin/omapreview-mcp`. MCP requires SDK 2.2.0 or newer. For an Arch
package installation, check that the available `python-mcp` meets that version;
otherwise use the visitor installer’s managed venv.
Configure the absolute server path in an MCP client.
The installers also link both `omapreview-mcp` and `omepreview-mcp` into
`~/.local/bin`; `omapreview mcp` is another server entry point.
The base CLI works without MCP.

Version 0.2.0 adds application workflows alongside PDF operations: search
with hit geometry, signature-library management, live editor proposals and
history, recording handoff, exports, and clipboard/external-app handoffs.
Discover the contract with `omapreview workflow-schema`
or MCP `workflow_schema`, then use `workflow` or `run_workflow`. See the
[workflow matrix and safety contract](docs/ops.md#application-workflows).
Consequential edits default to proposals. Sharing requires explicit confirmation;
an external-app handoff does not mean the file was delivered.

MCP `render_page` returns an actual page image so agents can verify saved
results. OCR supports preflight, progress and cancellation through the same
shared engine. See the [agent playbook](skill/SKILL.md) for separate CLI/MCP
recipes and task-specific verification steps.

## Project notes

- User-facing command: `omapreview`. Python package: `omepreview`.
- Desktop entry: `omapreview edit %f`; it does not open a launcher file picker.
- Saved trackpad signatures go to `~/Downloads/omapreview/signature/`.
- The editor, CLI, and MCP share the core PyMuPDF operation engine for supported operations.
- The project is licensed under [AGPL-3.0-or-later](LICENSE).

The screenshot fixture and asset provenance are recorded in
[`docs/evidence/readme-redesign-2026-09-16.md`](docs/evidence/readme-redesign-2026-09-16.md).
