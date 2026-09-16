# Permanent redaction

Redaction destroys content. Read the full relevant page, identify the exact
target and protected neighbor, and preserve the original in a separate output.
Use text matching when the text is extractable; use a rectangle only when the
user has supplied or approved the geometry.

## CLI route

```sh
omapreview read input.pdf --page 1 --json
omapreview redact input.pdf --page 1 --match "secret token" --dry-run --json
omapreview redact input.pdf --page 1 --match "secret token" --confirm -o redacted.pdf --json
omapreview read redacted.pdf --page 1 --json
omapreview snapshot redacted.pdf --page 1 -o redacted.png
```

The generic route is equivalent:
`omapreview apply input.pdf --ops redact.json --dry-run/--confirm`. Verify
that target text and intersecting payloads are gone, neighbor text remains, and
the rendered result has the requested fill. A black rectangle or annotation
appearance is not proof of redaction.

## MCP route

Call `read_pdf` and `render_page` first. Call `redact` with `confirm=false` to
preview, then with `confirm=true` and a new `output` after approval. For a
batch, use `apply_ops` with the redact op and `dry_run=true/false`. Call
`read_pdf` and `render_page` on the saved output. The saved read must show the
target absent and the neighbor retained; the saved render must show the result.

If text extraction is empty because the page is scanned, text-match redaction
cannot be verified by this route. Stop and ask for an approved rectangle or an
OCR-backed workflow; do not claim that a visual black box removed the payload.
If verification is unavailable, leave the task incomplete.
