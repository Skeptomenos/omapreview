# OCR: a reviewed searchable copy

Load this for scanned text or a requested searchable PDF. Use the selected
interface throughout. Recognition adds hidden text; it does not prove accurate
transcription. Keep the original, and use a new explicit destination.

## Before approval

Read and render the source. Discover dependencies and installed languages.
Preflight the exact pages, languages, and destination. Review the source SHA-256,
per-page eligibility, warnings, and refusals. OCR is a standalone operation;
apply later highlights or redactions in separate edits of the saved copy.

Missing backend/language data stops execution. Report the action from discovery;
installation is a separate task. Refused signed, encrypted, annotated, form,
linked, or attached documents need a separate approved preparation decision.
Do not remove protected content to make OCR run. Existing usable text skips the
whole page, including mixed scan/text pages. Default language is `eng`; use only
exact installed identifiers. Physically sideways scans are not auto-corrected.

## CLI-only route

```sh
omapreview operations
omapreview ocr-status
omapreview read source.pdf --json
omapreview snapshot source.pdf --page 1 -o source.png
omapreview ocr source.pdf --page 1 --language eng -o searchable.pdf --json
```

The last command is preflight and writes nothing. After approval, copy its
`applied[0].ocr.source_sha256` into `HASH_FROM_APPROVED_PREFLIGHT` below. Preserve
the reviewed source, options, and destination:

```sh
omapreview ocr source.pdf --page 1 --language eng -o searchable.pdf \
  --expected-source-sha256 HASH_FROM_APPROVED_PREFLIGHT --confirm --json
omapreview read searchable.pdf --json
omapreview snapshot searchable.pdf --page 1 -o searchable.png
omapreview workflow search --args '{"path":"searchable.pdf","query":"EXPECTED PHRASE"}'
```

Inspect the actual PNG and compare the read text with the scan. Check every
selected page, protected text, and a control page/region. Generic `apply` accepts
the same `ocr` object and requires `--confirm`. `--dry-run` always writes nothing.

Phase events are JSON lines on stderr; stdout holds only the final result.
SIGINT/Ctrl+C or SIGTERM requests cancellation and waits for cleanup. A cancelled
CLI returns 130; other OCR failures return 1 with a final stderr JSON error
`{error:{code,message,ocr},output:null,applied:[]}`. Inspect the code and report;
never scrape backend log prose. Cancellation after publication can return the
completed copy. After an uncertain interruption, read/render the explicit
destination before retrying.

## MCP-only route

1. Initialize and discover `operation_schema`, `ocr_status`, `apply_ops`,
   `read_pdf`, `render_page`, and `run_workflow` from `tools/list`.
2. Call `ocr_status()`, `read_pdf(path="source.pdf")`, and
   `render_page(path="source.pdf",page=1)`. Inspect the image content block.
3. Preflight with `apply_ops(path="source.pdf",ops=[{"op":"ocr","pages":[1],
   "languages":["eng"]}],output="searchable.pdf")`. The default is dry-run.
4. After approval, call the same payload with `dry_run=false` and add
   `expected_source_sha256` from `applied[0].ocr.source_sha256` to the op.
5. Call `read_pdf` and `render_page` on **searchable.pdf**, then
   `run_workflow(name="search",arguments={"path":"searchable.pdf",
   "query":"EXPECTED PHRASE"})`. Compare actual image/text/geometry with the
   source and requested postconditions. No CLI/Python fallback is needed.

Use a request `_meta.progressToken` for phase notifications. Phases 0–5 mean
preflight, staging, recognizing, verifying, publishing, complete; recognition
is indeterminate, not a page percentage. Requests without a token still work
and receive no progress notifications. The supported MCP SDK (2.2.0 or newer) includes phase names. JSON-RPC cancellation and stdio EOF cancel owned
engine work and wait for cleanup. A disconnected client has no result channel.
On a connected client, OCR tool errors have `isError=true` and JSON
`{error:{code,message,ocr},output:null,applied:[]}` in structured content and text. Cancellation may suppress the request response.

## Verify and report

Match the reported output hash to the independent read/render fingerprint.
Check page count, selected pages, geometry, and preserved control content.
`processed` means new usable text, with `review_required=true`; compare each
expected phrase against the scan. Nonempty text can be wrong. Report `skipped`
and `needs_review` pages explicitly. Zero recognized pages is an unchanged-content
copy, not successful recognition. Never invent a confidence score.

For follow-on highlight use the saved search/read geometry, then reread and
render the marked copy. For redaction load [redaction.md](redaction.md): verify
both hidden text removal and scan-pixel removal, with an unchanged control
region/page. A black overlay is insufficient. Human review remains required
for sensitive content; OCR cannot prove that all sensitive data was found.

If the source hash changed, preflight again and review the changed source before
execution. Engine deadlines bound cooperative work and process cleanup. Native
PDF calls cannot be interrupted internally; hard parent termination has no
cleanup guarantee. Do not report completion from progress or output existence.
