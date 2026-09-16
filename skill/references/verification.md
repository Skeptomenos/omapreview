# Saved-result verification

Use this reference for every mutation and for any answer that names a rendered
or extracted artifact. Verification is a fresh observation of the destination,
not a replay of the request.

## Common loop

1. Read the source and record the requested postconditions, protected content,
   source path, and source fingerprint.
2. Render the source page or target region when placement, appearance, or image
   content matters. Resolve coordinates in the same unrotated point space.
3. Preview or dry-run consequential work. Confirm the exact destination and
   obtain authorization before committing once.
4. Read the saved destination with the selected interface. Compare page count,
   text, fields, annotations, geometry, operation results, and fingerprints.
5. Render each affected saved page without a grid. Check the visual target,
   neighboring/protected content, and page geometry. Use both structured and
   visual checks when both matter.
6. Report the verified artifact, exact checks, and any limit. If any check is
   wrong, stale, or unavailable, report failure or partial completion and do
   not claim the mutation succeeded.

## Operation checks

| Task | Fresh saved observations |
|---|---|
| Highlight, note, text box, ink, shape | Type, content, count, geometry, rendered placement, and retained neighbor text. A check or cross is ink. |
| Fill | Intended field identity and value, page/rect, and rendered appearance. |
| Signature | Saved rendering shows the authorized signature at the requested size and position; optional date is visible; protected text remains readable. This is visual, not certificate signing. |
| Rotate, move, delete, insert | Page count, order, identity text, page size/rotation, affected renders, and an unchanged control page. |
| Extract, crop | Extracted page selection and render; source fingerprint unchanged; crop is a visible-page-box change, not content destruction. |
| Delete annotation | Requested annotation absent; unrelated annotations remain; render reflects removal. |
| Redact | Target text/payload absent in fresh reads, neighbor text retained, redaction rendering is correct, and serialized-content checks pass. Black ink is not redaction. |
| Flatten | Annotations/widgets are baked into fresh reads and renders; pending redactions are refused before reporting flatten success. |

## Failure evidence

Use a deliberately wrong expected text, page, geometry, or output fingerprint
as a negative assertion during a bounded trial. It must fail the same check used
for completion. A missing signature, scanned text without OCR, duplicate or
ambiguous form target, denied approval, wrong/stale output, or unavailable
saved read/render is a truthful stop or partial result, never a successful
mutation.
