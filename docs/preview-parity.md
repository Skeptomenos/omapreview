# Preview parity — manual checklist

Human success test for the Preview-parity milestone (spec §6). Automated
coverage lives in `tests/`; use this list when validating on Omarchy or a
plain-GTK desktop.

**Surfaces:** exercise each row on **CLI**, **MCP** (where the op exists),
and **GTK editor** (`omepreview edit`) unless marked GUI-only.

## Page surgery (M1)

| # | Check | Pass |
|---|--------|------|
| 1 | 5-page PDF → delete pages 2 and 4 → 3 pages remain; old page 3 text is now page 2 | ☐ |
| 2 | Rotate page 1 by 90° → saved file shows rotated content | ☐ |
| 3 | Move pages 5–6 after page 1 on a 6-page file → order 1,5,6,2,3,4 | ☐ |
| 4 | Insert pages 1–2 of B after page 1 of A → count +2; text matches source | ☐ |
| 5 | Insert blank after position 0 → blank first page; original page 1 is page 2 | ☐ |
| 6 | Insert image as page → count +1; page contains raster | ☐ |
| 7 | Refuse delete-all (error, file unchanged) | ☐ |
| 8 | Atomic fail: invalid page index in batch → nothing written | ☐ |

### Thumbnail sidebar (GUI)

| # | Check | Pass |
|---|--------|------|
| 9 | F9 toggles thumbnail sidebar | ☐ |
| 10 | Multi-select, drag reorder, Delete/Backspace ghosts deleted pages | ☐ |
| 11 | Ctrl+R / Shift+Ctrl+R rotate selected thumbnails | ☐ |
| 12 | Right-click: rotate, delete, extract, insert blank/file | ☐ |

## Two-window clipboard (GUI)

| # | Check | Pass |
|---|--------|------|
| 13 | Two `omepreview edit` windows: copy 2 pages in A, paste in B → independent files after Save | ☐ |
| 14 | Ctrl+C/V/X with sidebar focus; Delete does not remove pages when a text entry has focus | ☐ |

## Redaction (M2)

| # | Check | Pass |
|---|--------|------|
| 15 | `redact` match removes text from `get_text()` / `pdftotext` (not just covered) | ☐ |
| 16 | Rect redact on image region destroys pixels (sample near-black) | ☐ |
| 17 | **Pen/ink is not redact** — black ink overlay still in `get_text()` | ☐ |
| 18 | Dry-run redact writes nothing | ☐ |
| 19 | GTK: redact ghosts until Save; default `*_redacted.pdf`; original stays | ☐ |

## Forms and annotation delete (M3)

| # | Check | Pass |
|---|--------|------|
| 20 | Click empty AcroForm field → type → Save → `omepreview read` shows value | ☐ |
| 21 | Select saved annotation → Delete → Save → annot gone in `omepreview read` | ☐ |

## Regression

| # | Check | Pass |
|---|--------|------|
| 22 | `python -m pytest tests/ -q` exits 0 | ☐ |
| 23 | Highlight, note, sign, fill_field still work | ☐ |
| 24 | Undo save restores prior file bytes | ☐ |

## Shapes (M3 P2)

| # | Check | Pass |
|---|--------|------|
| 25 | `omepreview shape` line/arrow/rect/oval → `omepreview read` shows Line/Square/Circle annot | ☐ |
| 26 | MCP `add_shape` applies same geometry as CLI | ☐ |
| 27 | GTK: Shapes tool drag → ghosts → Save → annots in `read` | ☐ |

## Crop (M3 P2)

| # | Check | Pass |
|---|--------|------|
| 28 | `omepreview crop --rect …` shrinks page CropBox; `read` size matches | ☑ |
| 29 | Highlight/shape coords still align after crop (regression) | ☑ |
| 30 | GTK: Crop tool drag → page aspect updates → Save persists | ☑ |

## Not in this checklist

- **Engel-500** (real bank-statement redaction exam) — end-of-dev after M2, not Slice 6.
- **P2:** password, `sign --auto`, outline sidebar.

## Quick commands

```bash
python -m pytest tests/ -q
omepreview pages doc.pdf --list
omepreview redact doc.pdf --page 1 --match "SECRET" -o out.pdf --dry-run --json
omepreview redact doc.pdf --page 1 --match "SECRET" -o out.pdf --confirm --json
omepreview delete-annotation doc.pdf --page 1 --index 0 -o out.pdf --confirm
omepreview shape doc.pdf --page 1 --shape rect --rect 72,100,200,180 -o out.pdf
omepreview crop doc.pdf --page 1 --rect 50,50,500,750 -o out.pdf
omepreview read out.pdf --json
```
