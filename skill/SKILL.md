---
name: omepreview
description: Use for PDF reading, review, annotation, form filling, visual signing, redaction, page surgery, flattening, or verified handoff. Choose the available CLI or MCP route and report incomplete work when a required check or capability is unavailable.
---

# omepreview

Use `omapreview` for the CLI and `omapreview-mcp` for the stdio MCP server. The
two interfaces use the same PDF operation engine. Pages are 1-based. Coordinates
are finite PDF points in unrotated CropBox-local space, with a top-left origin.

## Route the task

1. Identify the source, intended destination, requested postconditions, and
   content that must remain unchanged. Ask only for facts that block the task.
2. Honor an explicit CLI or MCP choice. If only one interface is available,
   use it. If both are available without a preference, prefer MCP for structured
   reads and page images; use CLI for a real capability gap.
3. Load exactly one mode guide and the relevant scenario guide below. Load
   [`verification.md`](references/verification.md) before any mutation or
   whenever the task promises a saved result.
4. Read the source first. Resolve targets from that same read or render. Carry
   forward existing user authorization when it covers this operation, target,
   and destination. Ask only for missing authorization or a material change;
   use dry-run/proposal mode for consequential work before the single commit.
5. Reopen and inspect the saved destination. A successful apply response, file
   existence, dry-run, pending GUI ghost, or pre-edit image is not completion.
   Report the output path, source/output fingerprints when available, checks,
   and any failed or unavailable check.

## Load one mode and one scenario

- CLI selected or needed: read [`cli.md`](references/cli.md) for commands,
  JSON ops, output handling, and confirmation behavior.
- MCP selected or preferred: read [`mcp.md`](references/mcp.md) for discovery,
  schemas, image blocks, structured results, and MCP-only verification.
- Read, summarize, answer, or review: read [`read-review.md`](references/read-review.md).
- Annotate, redline, stamp, or mark up: read [`markup.md`](references/markup.md).
- Fill a form, sign, or initial: read [`forms-signatures.md`](references/forms-signatures.md).
- Permanently remove content: read [`redaction.md`](references/redaction.md).
- Reorder, crop, insert, extract, flatten, or hand off: read [`pages-export.md`](references/pages-export.md).

Scenario references contain independent CLI and MCP routes. An MCP-only route
uses only MCP tools for reading, rendering, applying, and checking the PDF; it
does not hide a shell or PyMuPDF fallback.

## Stop truthfully

Stop before writing when the requested signature is absent, text is scanned
and OCR is required, a form target is ambiguous, authorization is denied, or a
required saved-output check cannot run. If a mode fails, establish whether it
wrote before changing modes. Never repeat an uncertain mutation blindly.
Report a partial result only with the completed checks and the missing result.

An optional GUI handoff may load a proposal as draggable ghosts. It is a review
step, not saved-output evidence; the final check still reads and renders the
saved PDF.
