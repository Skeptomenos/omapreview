# Reorder, crop, insert, extract, flatten, or hand off

Read the source page list and relevant text before page surgery. State page
identity and order, source preservation, and geometry effects. Use a separate
destination for exports and handoff copies.

## CLI route

```sh
omapreview pages input.pdf --list
omapreview pages input.pdf --rotate 90 --pages 2 --dry-run --json
omapreview pages input.pdf --move 3 --after 0 --confirm -o moved.pdf --json
omapreview pages input.pdf --extract 2-3 -o excerpt.pdf --confirm --json
omapreview crop input.pdf --page 1 --rect 72,80,500,750 --dry-run --json
omapreview flatten marked.pdf --confirm -o final.pdf --json
```

Use `--insert PDF`, `--blank`, or `--image` with `--after` for insertion. Check
the saved page count, order, identity text, size/rotation, and affected renders.
For extraction, confirm the source fingerprint is unchanged. Crop changes the
visible CropBox; it does not destroy content. Flatten is irreversible in the
output and refuses pending redaction annotations.

## MCP route

Call `list_pages` and `read_pdf` before choosing page numbers. Use
`rotate_pages`, `move_pages`, `insert_pages`, `extract_pages`, or `crop_pages`
with the live schemas. Use `apply_ops` with `dry_run=true` when a proposal is
needed. `delete_pages` and `flatten_pdf` require `confirm=true`; an extraction
uses `to` and preserves the source. Call `list_pages`, `read_pdf`, and
`render_page` on every affected saved destination. For an image insertion,
inspect the returned saved render.

## Finalize and hand off

Before flattening, check for pending redactions and apply reviewed redaction
operations first. After flattening, fresh reads and renders must show the
intended appearance with annotations/widgets baked in. Name the exact verified
artifact. Sharing or sending it requires a separately available tool and the
user's authorization; this skill does not imply publication.
