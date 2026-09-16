# MCP route

Use this reference when the user selects MCP or when structured page images are
the best fit. Start `omapreview-mcp` as an MCP stdio server and keep protocol
stdout clean. In an MCP-only task, every PDF read, render, mutation, and saved
check below is a tool call.

## Discover the live contract

Initialize the session, call `tools/list`, then call `operation_schema` for edits and `workflow_schema` for application workflows. The
required core tools are `read_pdf`, `render_page`, `operation_schema`,
`list_form_fields`, `apply_ops`, and `list_pages`. Available dedicated tools
include `highlight`, `add_note`, `fill_field`, `place_signature`,
`list_signatures`, page tools, `delete_annotation`, `redact`, `crop_pages`,
`add_shape`, and `flatten_pdf`. Use the live schema rather than inventing a
field name or variant.

`read_pdf(path, pages, text_only)` returns text blocks, bboxes, fields,
annotations, and `source_fingerprint`. `render_page(path, page, scale, grid,
clip)` returns an actual PNG image content block plus JSON metadata. The image
metadata includes the rendered source fingerprint, page geometry, rotation,
clip, pixel dimensions, and coordinate transform. Rendering is read-only.

## Propose and commit

Prefer one `apply_ops` call for a batch. Its `dry_run` default is `true`;
re-call with `dry_run=false` only after approval. Pass real JSON booleans, not
strings. Dedicated consequential tools use `confirm=true`, except
`place_signature`, which uses `confirmed=true`; `flatten_pdf` also uses
`confirm=true`. These tools default to no write. Dedicated ordinary tools may
write immediately, so use `apply_ops` with `dry_run=true` when proposing them.

Set `output` to a new destination when the source must remain available. The
`extract_pages` tool uses `to` and preserves the source. Do not treat a result
with `output: null`, `applied: false`, or `needs_confirmation` as committed.

## MCP-only completion

After a committed call, call `read_pdf` on the exact saved output and call
`render_page` without a grid for every affected visual page. Call `list_pages`
for page surgery. Compare saved observations and image metadata to the source
fingerprint and requested postconditions. An MCP client that cannot reread or
rerender the saved artifact must report verification unavailable and leave the
task incomplete.

If a tool errors, first inspect the returned error and whether the destination
exists in the tool results. Do not retry a mutation until the write state is
known. If the requested feature is absent from `tools/list`, report the gap or
switch to CLI only when the user permits that route.

## Application workflows

Call `run_workflow(name, arguments)` using the exact `workflow_schema` route.
Search, signatures, recorder handoff, editor sessions, export, and clipboard
work without a hidden CLI call. Load [desktop-workflows.md](desktop-workflows.md)
for live sessions and handoff states. Error results are not completion.

For OCR load [ocr.md](ocr.md). `ocr_status` is read-only; `apply_ops` handles
preflight and confirmed execution with the approved source hash.
