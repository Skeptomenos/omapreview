# PKGBUILD smoke — aarch64 / Omarchy

Slice 6 packaging notes. This cloud agent environment is **not** Arch Linux;
full `makepkg` was **not** run here. Use this as the Omarchy/aarch64 checklist.

## What was run here (any CPU)

| Step | Command | Result |
|------|---------|--------|
| Tests | `python3 -m pytest tests/ -q` | Must exit 0 before release |
| Wheel build | `python -m build --wheel --no-isolation` | Run from repo root after `pip install build hatchling` |
| CLI entry | `python3 -m omepreview.cli --version` | Confirms console scripts resolve |
| MCP import | `python3 -c "from omepreview.mcp_server import mcp"` | Optional extra |

## What needs Omarchy (or Arch aarch64)

Run on real hardware before publishing to AUR:

```bash
# From a clean Arch/aarch64 chroot or Omarchy machine
cd packaging
makepkg -f -si   # or -o for offline build
omapreview --version
pacman -Si python-mcp                    # require version 2.2.0 or newer
omapreview-mcp --help 2>/dev/null || true   # needs accepted MCP SDK
omapreview edit /path/to/sample.pdf         # needs gtk4 + python-gobject
```

| Check | Notes |
|-------|--------|
| `depends` resolve | `python`, `python-pymupdf` on aarch64 |
| `optdepends` | `python-mcp>=2.2.0`; if unavailable, use current-source `.[mcp]` in a checkout venv |
| Desktop file | `share/omapreview.desktop` opens PDFs via `omapreview edit` |
| Bar widget | `cp /usr/share/omapreview/shell-plugin/omapdf.bar ~/.config/omarchy/plugins/` |
| GTK editor | Page sidebar (F9), redact tool (R), form click-fill |

## PKGBUILD gaps to watch

- `arch=('any')` — pure Python; no native compile, but PyMuPDF wheel must exist for aarch64 on Arch.
- `pkgver` / `source` — PKGBUILD fetches the GitHub tag archive `v$pkgver`, not a clone of the app repo. Pin `sha256sums` on the AUR copy.
- Editor is not a hard `depends`; document `gtk4` + `python-gobject` for `omepreview edit` in README.

## Evidence

Slice 6 agent run: pytest log in
`/cursor/stores/bc-edf2aef7-00f6-4716-aad3-e4d4e3b9a39b/media/preview-parity-s6/pytest-output.txt`.
