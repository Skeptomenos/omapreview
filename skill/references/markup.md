# Annotate, redline, stamp, or mark up

Read first. Choose text matching when the exact phrase identifies the target;
choose a rectangle or point from the read bbox or a grid render when placement
depends on layout. Exact matching can fail on ligatures, so use the true text
returned by the source read or a shorter distinctive phrase.

## CLI route

Use a dedicated route or one generic ops file:

```sh
omapreview annotate input.pdf --page 1 --match "termination" --style highlight --dry-run --json
omapreview note input.pdf --page 1 --at 450,200 --text "Check this" --dry-run --json
omapreview apply input.pdf --ops markup.json --dry-run --json
omapreview apply input.pdf --ops markup.json --confirm -o marked.pdf --json
```

Use `text_box` for visible replacement or labels, `ink` for freehand checks and
crosses, and `shape` for line/arrow/rect/oval. The operation catalog gives the
required fields and defaults. Inspect `read marked.pdf --json` and a no-grid
snapshot of every affected page.

## MCP route

Call `read_pdf` and `render_page` first. Build one `apply_ops` batch with
`highlight`, `note`, `text_box`, `ink`, or `shape`; call it with `dry_run=true`
to inspect the resolved edit, then with `dry_run=false` after approval. The
dedicated `highlight`, `add_note`, and `add_shape` tools are available for
single writes, but the generic route is safer for a proposal. Call `read_pdf`
and `render_page` on the saved output. A wrong expected annotation or geometry
must fail the completion check.

## Review outcome

For a review, summarize the page-anchored findings and the matching saved
annotations together. Preserve nearby text and report unsupported or partial
items. Do not call a pending GUI overlay or a successful tool response saved
evidence.
