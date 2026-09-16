# Read, summarize, answer, or review

Read the full relevant document before answering. Use page citations. If the
request is broad, inspect every page; if it names pages or a section, inspect
those pages and state the scope.

## CLI route

```sh
omapreview read input.pdf --text-only
omapreview read input.pdf --json
```

Use text-only output for prose and JSON output for bboxes, fields, annotations,
or target selection. Render a page with `snapshot` when columns, images, or
layout change the interpretation. No write is needed for a read-only answer.

## MCP route

Call `read_pdf` for all relevant pages, with `text_only=true` when prose is the
main need. Call `render_page` for layout or image evidence and inspect its PNG
content block. Do not use a shell, local screenshot, or hidden PDF library in
an MCP-only route.

## Completion and limits

Support each conclusion with a page number and exact observed text or visual
fact. If the PDF has no extractable text and the request needs text search,
state that it is scanned/image-only and OCR is not built in. Offer a visual
review or ask for OCR text; do not invent text or claim a comparison/diff
service. If review leads to markup, switch to the markup recipe and verify the
saved output separately.
