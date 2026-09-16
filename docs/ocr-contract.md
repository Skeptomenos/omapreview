# OCR O0 contract and acceptance corpus

Status: implementation contract, 2026-09-16. OCR is not shipped by this batch.
The shared plan's `ocr-o0` section owns acceptance and handoff status. This
file owns the proposed contract. [Machine-readable catalog and request vectors](ocr-contract.json)
and [generated fixtures](../tests/ocr_fixtures.py) are its executable companions.
`assert_saved_copy` checks disk evidence; `assert_ocr_report` also checks the future
implementation report against the fixture manifest and saved bytes.
Root must link this file from `index.md` when it integrates O0.

## Shared operation and preflight

Add one standalone `ocr` op to the existing catalog and `engine.apply`.
Keep stdlib + PyMuPDF as core dependencies. Do not import OCRmyPDF into the app.
Use the caller's explicit `output` argument; do not add an op-local output alias.
Reject absent output, source aliases (resolved paths, hardlinks and symlinks),
any existing output (including dangling symlinks), directories and mixed batches.
Never overwrite a destination that appears during recognition.

```python
proposal = apply(source, [{"op": "ocr", "pages": [1, 3], "languages": ["eng"]}],
                 output=new_copy, dry_run=True)
# After user/caller approval, bind execution to the inspected bytes.
apply(source, [{"op": "ocr", "pages": [1, 3], "languages": ["eng"],
               "expected_source_sha256": proposal["applied"][0]["ocr"]["source_sha256"]}],
      output=new_copy, dry_run=False)
```

`dry_run=True` never calls OCR, creates staging/output files or estimates accuracy.
It can open/read/render the source and run bounded dependency version/language
queries. Return resolved pages, eligibility/refusals, dependency information,
absolute destination and source fingerprint. No pages count as processed yet.
MCP defaults to preflight; explicit `dry_run=false` remains its generic confirmation
mechanism. CLI requires its explicit execution/confirmation option. The GUI's
approved start action supplies the binding. Engine callers must explicitly use
`dry_run=False` **and** a matching source hash for OCR. Do not change ordinary ops'
confirmation semantics or interpret a stale proposal as approval of changed bytes.

Omitted `pages` means all pages. Reject empty, duplicate, boolean, non-integer,
zero, negative and out-of-range pages. Sort valid selected pages into document
order. Unselected pages remain in the output in their original positions.
Languages are exact identifiers discovered from Tesseract; preserve requested
order for language priority. Default `["eng"]`; reject missing data, `osd` alone,
duplicate/empty/unsafe values and unsupported options. Script identifiers such as
`script/Latin` require an exact installed match. Do not download packs. No force,
redo, deskew, PDF/A conversion, automatic pixel rotation, cleanup or external
plugins/config files in v1. Unknown op keys are errors.

## Dependency gate for O2

The demonstrated tuple is Linux aarch64, Python 3.14.7, OCRmyPDF 17.11.0,
Tesseract 5.5.3 with `eng`, PyMuPDF 1.28.2, Ghostscript 10.08.0, MCP SDK 2.2.0.
Recommend `ocrmypdf>=17.11.0,<17.12` for the first optional extra. Only 17.11.0 is
accepted by these probes; the range is a packaging maintenance boundary, not
proof of every patch version. O2 must rerun this corpus on its resolved version.
Do not claim OCRmyPDF 16/other 17 minor versions, other platforms or Python
versions are accepted. Base installs remain usable without OCR and MCP.

Discovery must identify the exact executable/interpreter used by the worker,
version, supported range, Tesseract version, installed languages, PyMuPDF version,
and availability/version of the PDF rasterizer actually selected. Check executable
exit status; OCRmyPDF's version output was empty in the initial feasibility probe,
so use metadata from that same isolated interpreter if needed. Bound dependency
queries to 10 seconds each. A missing/unknown version fails execution with an
install/recheck action. This tested route has Ghostscript available; do not infer
that it is universally required or silently switch rasterizers without acceptance.
Basic discovery must still report unavailable dependencies without an import crash.

English is the only real language acceptance here. German is absent. A second
installed language and combined language recognition remain an O2/O5 gate.
No system package installation or `sudo` belongs in the app.

## Eligibility and preservation

Inspect the whole document, including unselected pages, before writing. Fail
closed on malformed/unreadable structures. Refuse these v1 inputs:

- Encryption, including owner-only encryption that opens without a password.
- Any signature widget/field, signed value, signature dictionary, ByteRange or
  signature/DocMDP reference. Walk PDF objects/field hierarchy; byte substring
  searches alone miss compressed objects and can misclassify ordinary text.
- Pending redaction annotations.
- Any widgets/forms (including XFA), annotations, links or embedded attachments.
  Also refuse active content, portfolios and tagged structure until preserved
  semantics have dedicated acceptance. Never flatten or remove these to proceed.
- Unsupported geometry: non-unit `/UserUnit`, nonzero MediaBox origin, malformed
  boxes, rotations other than legal quarter-turns. Preserve all five boxes for
  supported pages; unusual Trim/Bleed/Art boxes require matching verification.

The deliberately narrow content policy is conservative. Real raw-backend probes
preserved a simple note, text widget, URI link and attachment, including rendered
appearance. That does not establish arbitrary annotation appearances, field
relationships, actions, file specifications, tags or accessibility semantics.
These fixtures remain **refusal** cases for v1; do not advertise general support
from their successful raw-backend experiments. Return the offending page/object
kind and suggest using the original or an explicitly prepared copy. Do not advise
removing signatures as an automatic remedy.

`signature_structure.pdf` contains invalid synthetic `/Sig` and `/ByteRange`
objects. `unsigned_signature.pdf` has an empty signature field. They prove
conservative structural refusal, **not cryptographic signature validation**.
No cryptographically signed corpus is provided or required for a claim of
cryptographic validation, because that claim is explicitly out of scope.

For ordinary pages, any usable pre-existing text skips the whole selected page.
Use visible, non-whitespace, non-control Unicode from extraction as the minimum
usable-text test; record the count, not a confidence score. Existing invisible
OCR is skipped too. Native text plus scanned regions is reported as
`skipped/existing_text` with `scanned_regions_not_processed=true` when images are
present. If the backend skips text that PyMuPDF cannot extract usefully, report
`needs_review/existing_text_unusable`; never force OCR. No new text is not success.

## Geometry and saved-output gates

Normalize known `/Rotate` metadata to zero on a disposable snapshot, run OCRmyPDF
with `--mode skip --output-type pdf --optimize 0 --jobs 2`, then restore original
rotation before validation. Do not rotate the source pixels. Use `--pages` for
selected-page recognition. Restore rotation for every page and keep every box.
The four-rotation fixture reproduces the naive failure and verifies this route.
A physically sideways raster remains unsupported for orientation correction;
it can yield empty or incorrect text and must not be presented as corrected.

Coordinates remain PDF points, top-left, in the existing PyMuPDF unrotated page
space used by read/engine. One point is 1/72 inch. For a zero-rotation crop rendered
at DPI `d`, `(u,v)` pixels map to `(u*72/d, v*72/d)` crop-relative points, with pixel
rounding. Add the CropBox offset only when converting to MediaBox space. Apply
`page.rotation_matrix` only for display; use `derotation_matrix` for inverse
pointer mapping. Never scale text coordinates twice or insert into display
coordinates. Preserve image resolution; the fixture is generated at 200 DPI,
verification renders at 100 DPI. OCRmyPDF may select its own working DPI; do not
claim the verification DPI was its recognition DPI.

Before publication, reopen the staged PDF. Independently verify page count,
order, all five page boxes, rotations, usable extracted text/word counts, finite
word boxes inside unrotated page bounds, preserved pre-existing text and visual
appearance on **every** page. At 100 DPI RGB, require equal dimensions and exact
samples for this supported no-cleanup route; fail closed on differences. The
fixture oracle additionally checks known phrases and cropped text placement;
these are test ground truth, not information available to the product. Exact
renders alone do not prove hidden text or metadata preservation. Snapshot and restore the source Info metadata fields and XMP metadata after OCR;
OCRmyPDF changes producer/date fields and adds/rewrites XMP even in this route.
Remove newly added XMP when the source has none. Compare restored metadata,
outline destinations/styles (ignoring storage xrefs) and page labels. The fixture
proves simple internal outlines, custom labels, XMP and nondefault Trim/Bleed/Art
boxes. Refuse untested rich/external outline destinations until accepted.

`processed` means new usable text was extracted and gates passed; it never means
correct transcription. Attach `review_required=true` to recognized pages. `blank`
is allowed only for a provably empty page or an exactly all-white rendered page
with no visible marks and no usable text. A near-white threshold must not discard
faint scans. Empty recognition on any nonblank image becomes `needs_review/no_text`.
Do not infer photo, orientation or handwriting from an empty result. For poor
scans with nonempty wrong text, `processed` plus the universal review flag is
honest; the fixture records a known transcription mismatch. No invented confidence.

A copy with only blank/skipped pages is a verified unchanged-content copy with
`recognized_pages=0`, not successful recognition. A copy with needs-review pages
may publish with overall `needs_review`; render/geometry/worker failures cannot
publish. Tesseract may hit a per-page limit while OCRmyPDF exits 0: no new text
must never become processed. Exact timeout attribution needs a structured signal;
otherwise retain `needs_review/no_text`, not a fabricated page timeout reason.

## Report and errors

Keep the existing `{output, applied}` envelope. Its single op resolution adds
`ocr` with: `status` (`preflight`, `complete`, `needs_review`, `failed`, `cancelled`,
`timed_out`), `source_sha256`, `output_sha256` (null until publication), `destination`,
`pages`, `dependencies`, `recognized_pages`, `verification` and `warnings`. Character counts use the
length of PyMuPDF plain text after leading/trailing whitespace is stripped.
Each page has stable 1-based `page`, `selected`, `status`, `reason`,
`text_before_chars`, `text_after_chars` (null in preflight), `word_count` and
`review_required`. Preflight uses `eligible`, `skipped` or `blank`; never processed.
Report all pages, including unselected pages. Geometry and render results are
separate booleans with verification DPI. Never include full extracted document
text in progress/logs. The usual read route can retrieve text after saving.

Errors extend `OpError` with stable `code` and optional `ocr` report, while retaining
an actionable message for existing boundaries. [The JSON contract](ocr-contract.json)
lists codes. O3 maps those fields to structured CLI/MCP errors without changing
ordinary op errors. Failed operations have `output=null`, no output hash, and no
`applied=true`; completed per-page recognition may be diagnostic but is not a saved
result. Never expose transient staging paths as final output. A client that loses
the response must inspect its explicit destination and hash before retrying.

## Minimal lifecycle for O1, O3 and O4

Keep `engine.apply` synchronous. Add optional keyword-only `cancel_event` (a
`threading.Event`) and `progress` (callback accepting a plain dict). No persistent
job service or recorder handoff reuse. One caller owns one worker process group,
one private temporary directory, one deadline and its final report.

Phases are preflight, staging, recognizing, verifying, publishing, complete.
Progress is monotonic **phase** progress (0 through 5), not an OCR page percentage.
During recognition use an indeterminate indicator; no parsing stderr prose.
Callbacks must not block or raise into publication; adapters queue/drain events.
If page progress is added later it must come from a proven structured backend hook.

Start the worker in a new process session on Linux; bound concurrency to two
workers and per-page Tesseract time to 30 seconds. A total monotonic deadline
(default 300 seconds, max 3600) includes staging, recognition and verification.
Poll cancellation/deadline through expensive work in bounded chunks. Use a private
0700 staging directory and private TMPDIR; outputs are 0600. Set/check resource
limits before launch (suggested first acceptance bounds: 100 pages, 500 MiB input,
2 GiB temporary storage and 100 MP per raster). O1 must measure their practicality,
report actual enforcement/limits and fail with `resource_limit`; do not claim a
temporary-disk hard quota from periodic size polling alone.

On cancel/timeout/error: stop the entire process group with TERM, allow at most
2 seconds, then KILL survivors; reap the direct child; verify no running descendants
remain; delete staging and temporary files. Check cancellation and source identity
again immediately before the publication commit point. Publish by atomic no-clobber
creation from a same-filesystem staged file; `os.replace` alone is insufficient.
A destination race fails without modifying the new occupant. Final hash is of the
actual published bytes. If cancel races after publication, return the completed
copy/result rather than deleting it or falsely reporting cancellation. Parent
SIGKILL cannot run cleanup: it is outside the demonstrated guarantee and must not
be described as a supported clean cancellation.

Snapshot source bytes and its stat identity/hash before work. Verify the source
is unchanged after the copy and immediately before publication; refuse any change.
The worker sees only the snapshot. Recheck pathname identity as well as contents,
including source symlink retargeting. This is optimistic conflict detection, not a
filesystem lock against unrelated writers; a change after the last check cannot
be prevented. Report that bounded guarantee instead of claiming a global transaction.

- CLI runs synchronously. SIGINT/SIGTERM set cancellation and wait for cleanup.
- MCP generic apply becomes an async adapter: execute engine work in a thread,
  forward queued phase events with the request progress token, and wire request
  cancellation **and transport EOF** to the event. Shield bounded cleanup/join;
  never abandon a live thread on request cancellation. SDK task cancellation by
  itself does not stop the child. A disconnected client has no result channel.
  Cancel requests after publication follow the completed-result rule above.
- GUI starts a worker thread and retains one OCR task per editor instance. The
  existing AF_UNIX bridge returns an instance-local task ID immediately; start,
  status and cancel are OCR-specific actions dispatched on GTK. Status reads cached
  progress; no slow recognition on GTK. Do not exceed the bridge's 15-second
  response bound. O4 owns this small in-memory record; no durable registry.
  Dirty state requires save/discard/cancel before snapshot creation. Start checks
  fresh session revision. Completion rechecks document identity/revision before
  opening the copy; if changed, retain the saved result and request explicit Open.
  Do not discard edits or retarget another document. Close cancels/joins the task.

The lifecycle experiment uses real stdio MCP 2.2.0 and OCRmyPDF subprocesses to
check progress, explicit cancellation, EOF and deadline cleanup. It is a bounded
experiment, not adapter implementation. Other MCP versions/transports and hard
parent death remain unproven. O3 must test its actual installed adapter, including
progress without a token and disconnection while Tesseract is running.

## Reproduce and use the corpus

```sh
.venv/bin/python tests/ocr_fixtures.py /tmp/ocr-fixtures-new
.venv/bin/python tests/ocr_probe.py /tmp/ocr-probe-new /path/to/ocrmypdf
.venv/bin/python tests/ocr_lifecycle_probe.py /tmp/ocr-lifecycle-new /path/to/ocrmypdf
.venv/bin/python -m pytest tests/test_ocr_contract.py -q
```

Directories must be new. Each fixture has a manifest with expected outcome and
source SHA-256. No generated PDFs are committed. The raw-backend probe intentionally
bypasses the proposed refusal policy to establish preservation evidence; it does
not test application preflight. O1 must run manifest refusals through real apply
and request vectors through validation. It must also test same-path/hardlink/symlink
outputs, existing/sentinel/racing outputs, source mutation/retargeting, worker bad
PDF/nonzero exit, empty output, false geometry and render/text damage, publication
failure, timeout/cancel and missing backend/language. Source and sentinel hashes
must remain unchanged. These are implementation gates, not passed O0 feature tests.

After OCR, O3/O5 must independently read/render the published copy, search and
highlight the recognized phrase, then redact both its hidden text and scan pixels
with an unchanged control region/page. Human review remains required for sensitive
content; OCR is never proof that all sensitive content was found.

References: [OCRmyPDF API](https://ocrmypdf.readthedocs.io/en/stable/api.html)
recommends process isolation; [advanced options](https://ocrmypdf.readthedocs.io/en/stable/advanced.html)
define skip mode and preservation limits. The local dated O0 evidence owns the
observed results; upstream documentation does not replace those measurements.
