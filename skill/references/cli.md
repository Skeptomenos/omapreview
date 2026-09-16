# CLI route

Use this reference when the user selects CLI or when MCP lacks a needed
capability. Keep the source and destination paths explicit. Use `--json` for
reports that another step must inspect.

## Discover and inspect

```sh
omapreview read input.pdf --json
omapreview fields input.pdf
omapreview pages input.pdf --list
omapreview operations
omapreview workflow-schema
omapreview snapshot input.pdf --page 1 --scale 2 -o before.png
```

`read` returns page text, bboxes, fields, annotations, and a
`source_fingerprint`. `snapshot` renders the requested page and returns matching
fingerprint metadata. Use `--text-only` only when layout is not needed. Use
`--grid N` to choose coordinate values from an image; the grid is not evidence
of the saved result.

## Propose and commit

Put one or more operation objects in a JSON file and inspect the dry run first:

```sh
omapreview apply input.pdf --ops edits.json --dry-run --json
omapreview apply input.pdf --ops edits.json --confirm -o output.pdf --json
```

Use `--confirm` only after the user approves the resolved targets. A separate
output keeps the source available for repair and comparison. If a command
returns nonzero, preserve the source and inspect its actionable stderr; do not
assume a partial write.

The dedicated routes map to the same operations: `annotate`, `note`, `fill`,
`sign`, `delete-annotation`, `redact`, `crop`, `shape`, `flatten`, and `pages`.
`pages --extract PAGES -o excerpt.pdf` is source-preserving. `pages --list`
is read-only. `snapshot` is read-only and supports `--clip X0,Y0,X1,Y1` in
unrotated CropBox-local points.

CLI consequential defaults are explicit: `sign`, `redact`,
`delete-annotation`, `pages --delete`, `ocr`, and `flatten` need `--confirm`; an
unconfirmed call proposes or dry-runs and does not publish the destination.
Ordinary annotation, field, rotation, move, insert, crop, shape, and extract
commands can write without `--confirm`; use `--dry-run` whenever proposing any
edit. The generic `apply` route applies the same rule per operation.

## After the write

Run fresh reads against the exact output path:

```sh
omapreview read output.pdf --json
omapreview pages output.pdf --list
omapreview snapshot output.pdf --page 1 -o after.png
```

Check the operation-specific postconditions, the output fingerprint, and a
control page or protected text. For redaction or flattening, use the dedicated
scenario checks. If the saved observation is wrong or stale, do not report
success; return to the original source and repair with a new destination.

## Application workflows

Use `omapreview workflow NAME --args 'JSON'` for routes in `workflow-schema`.
Arguments can come from `--args @file.json` or stdin with `--args -`. Results
are always JSON. The schema lists exact fields and status meanings. Search,
signature management, editor sessions, exports, and clipboard share this route.
For desktop tasks load [desktop-workflows.md](desktop-workflows.md).

For OCR load [ocr.md](ocr.md): `ocr-status` reports dependencies/languages;
`ocr` preflights by default. Execution also needs the approved source hash.
