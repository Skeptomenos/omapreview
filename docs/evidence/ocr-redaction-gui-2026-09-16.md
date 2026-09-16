# Native OCR redaction verification — 2026-09-16

Status: accepted and integrated on main at `21a8112`. [DEV-196](https://linear.app/helmus/issue/DEV-196) owns this follow-up to the [OCR interface acceptance](ocr-wave2-2026-09-16.md). The source and tests match reviewed candidate `e68b28f49bb5aa4c2611a2f28cd233f0176e3a6f`. Later documentation changes do not alter that tested code.

## Why verification reopened

The earlier CLI/MCP OCR-redaction tests added 2 PDF points to the OCR search rectangle. They established removal for that reviewed region, but did not establish coverage for the default GUI text-snap selection.

A native baseline on `d6c8ebe` used Recognize text, default text-snap redaction, Save copy and a fresh editor process to reopen the result. It removed the hidden target text, but left two faint pixels from the scanned word outside its OCR boxes. No readable secret or retained original image was demonstrated.

## Change

The shared redaction helper adds up to 0.75 PDF points around selected glyphs on image-backed pages. Neighbor checks limit the added margin. GUI rectangles and CLI/MCP operations use the same helper; callers do not need test-only padding.

Independent review found that the first margin implementation could delete a tiny diagonal neighbor. The corrected guard preserves that unselected glyph and its scan pixels. It does not modify the caller's input rectangle.

## Native saved-artifact evidence

The synthetic PDF has two scanned pages, a target phrase, nearby control text and an untouched control page. The native journey used the actual OCR panel, redaction gesture and Save confirmation. It did not inject a proposal as a substitute for native selection.

The independent audit measures a broad image region, `[110, 290, 215, 325]` PDF points, which includes the full target and its fringes:

| Artifact | Nonwhite target pixels |
|---|---:|
| OCR copy before redaction | 2,468 |
| Baseline native Save | 2 |
| First fixed native Save (`8498e059`) | 0 |

The target is absent from text extraction, word extraction and text trace. Every page 1 image pixel outside that region is unchanged. The control page renders identically when opened independently. A full object and file-stream audit finds no orphan image or surviving original target image stream or pixel digest.

The original image-only source still matches its initial SHA-256, `2315fb07c40e1b854eb933609775ce42e8ac986cded6d50bef3168374cb81f52`. The OCR copy retains SHA-256 `1bfb2d586c1515bf053256ce5c129178423dd46e026e7c29a259ce898fa27537`.

The first worker audit used an inner crop that could miss fringes. The independent broad-region audit supersedes that observation. It also corrects the raster-coordinate check for cropped pages. A black overlay alone is not accepted as proof of removal.

After the diagonal guard changed, the reviewer replayed the captured native operations through the final candidate. Applied regions, page content streams, embedded image pixels and fresh renders match the actual native saved artifact. The native journey was not repeated after this guard change; the independent replay establishes that its output remains valid.

## Required checks

The final candidate is `e68b28f49bb5aa4c2611a2f28cd233f0176e3a6f` (commits `8498e059` and `e68b28f`). Independent review accepted the corrected diagonal protection and saved-payload checks. A separate run of `tests/test_ocr_redaction_unpadded.py` passed all 8 tests in 2.92 seconds.

The deterministic tests use tight synthetic OCR-like boxes. Across GUI-derived and match operations at 0°, 90° and 270°, including cropped pages, the old zero-margin behavior leaves 24 target pixels. The corrected behavior leaves zero. The observation includes the full target fringe and verifies that target pixels existed before redaction. Same-line and diagonal control tests check text and image preservation.

The required `.venv/bin/python -m pytest tests/ -q` gate passed: **635 passed, no skips, exit 0, 200.74 seconds**. It ran under bubblewrap with private Downloads and XDG configuration/data/cache/state, the working display runtime, unchanged HOME and `OMAPREVIEW_TEST_OCRMYPDF` pointing at OCRmyPDF 17.11.0. This includes actual CLI and stdio MCP OCR journeys. There were 404 existing GI/Python deprecation warnings. The exact command and candidate are in `root-gate/command-result.json`; the full output is in `root-gate/pytest.log`.

The first baseline and candidate full-suite attempts crashed in the inherited GTK popover test. A controlled comparison traced this to the test environment: an empty private `XDG_RUNTIME_DIR` hid the Wayland socket. The same test exits 139 in that environment and passes with the working display connection. Downloads and app configuration remain private. No application fix or test skip is needed for this failure.

## Evidence and limits

Raw run records, screenshots and synthetic PDFs are under `/home/david/.local/state/omapreview/coordination/evidence/ocr-redaction-gui/`. Key records are `native/source-manifest.json`, `independent-review/candidate8498/native-broad-audit.json`, `independent-review/final-e68b28f/recheck-results.json`, `native/unpadded-ab.json`, and `root-gate/gtk-focused-comparison.json`.

The native check covers one real OCR-to-redaction journey. Focused tests cover additional geometry. This does not establish OCR accuracy on every scan. Agents must still inspect the selected region and verify the saved output. No personal PDFs were used.

Published v0.1.1 and the host installation remain unchanged. This record covers current source on main.
