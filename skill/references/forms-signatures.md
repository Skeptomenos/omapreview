# Forms, signatures, and initials

Read actual fields and saved signatures before constructing an edit. Never
invent a value or a signature asset. Visual signatures are not certificate
signatures.

## CLI route: forms

```sh
omapreview fields input.pdf
omapreview fill input.pdf --field field_name "value" --dry-run --json
omapreview fill input.pdf --field field_name "value" --confirm -o filled.pdf --json
omapreview read filled.pdf --json
omapreview snapshot filled.pdf --page 1 -o filled.png
```

If a requested field is absent, repeated without a unique target, or is not an
AcroForm field, stop and report the ambiguity. When the live operation catalog
offers `page` and `rect` selectors, use them to narrow a repeated field to one
widget; otherwise stop rather than selecting the first match. Use a `text_box`
only when the user supplies placement facts and accepts visible text rather
than a form value. Verify field name/value/page and rendered appearance in the
saved file.

## MCP route: forms

Call `list_form_fields`, inspect name/type/page/rect/value, then call
`fill_field` only for an actual unique field. When its schema exposes `page`
and `rect`, pass the 1-based page and exact widget rectangle for a repeated
name; an unresolved ambiguity is a refusal. For a proposal, use `apply_ops`
with `fill_field` and `dry_run=true`, then commit once with `dry_run=false`.
Reread the exact output with `read_pdf` and render the field's page. Never
select the first repeated field by position alone.

## CLI route: signatures

```sh
omapreview sig list
omapreview sign input.pdf --page 1 --at 120,540 --width 180 --sig default --date --dry-run --json
omapreview sign input.pdf --page 1 --at 120,540 --width 180 --sig default --date --confirm -o signed.pdf --json
omapreview read signed.pdf --json
omapreview snapshot signed.pdf --page 1 -o signed.png
```

Use the saved signature name the user authorized. If it is missing, stop
without writing and ask the user to add or draw one. A GUI ghost may be offered
for aesthetic placement, but the saved output must still be reopened and
rendered. Check the signature position, size, optional date, and protected text.

## MCP route: signatures

Call `list_signatures`, then `place_signature` with `confirmed=false` for a
proposal. After user approval call it again with `confirmed=true` and an
explicit output. Verify with `read_pdf` and a no-grid `render_page` image.
Missing signature, unapproved placement, or absent visual verification means
the signing task is incomplete. Do not describe this as cryptographic signing.
