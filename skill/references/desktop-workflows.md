# Live editor, recorder, and handoff

Discover with CLI `omapreview workflow-schema` or MCP `workflow_schema`.
Execute each route with CLI `omapreview workflow NAME --args 'JSON'`, or MCP
`run_workflow(name, arguments)`. These are independent transports for the same
contract. Follow existing authorization; a confirmation field is the commit
boundary, not a reason to ask again when the user already authorized the action.

## Human review and live edits

1. Call `editor` with `{"action":"open","path":"input.pdf","ops":[...]}`.
   Markup ops load as editable ghosts. For page surgery, open first and `stage`
   one page op at a time. An omitted path opens an empty editor.
2. Wait for `ready`, or use `status` with the returned exact session ID after
   `starting`. `list` finds live instances. Choose by exact path and session;
   never infer the active window. Older editors without this API must be reopened.
3. Read `status`. It includes pending markup/page ops, history depths, conflict,
   page/zoom and revision. Every mutation must include this revision. Reject a
   stale revision and inspect fresh state before choosing a target again.
4. `stage` adds proposals. `replace` changes one pending item using a new markup
   op and its 0-based `index`. `move` takes index and PDF-point dx/dy. `delete`
   removes a pending ghost. `view` sets page and zoom percent (`fit:true` restores fit); `search` highlights
   a query in the actual visible document and accepts a 0-based `hit` index. None is a saved edit.
5. `save`, `undo`, and `redo` require `confirm:true`. History can restore saved
   bytes. Read/render the saved path after a write. Dirty `close` requires
   confirmation to discard pending work. Source conflicts require reload/reopen;
   keep pending state and inspect disk first.

Example CLI open:

```sh
omapreview workflow editor --args '{"action":"open","path":"input.pdf","ops":[{"op":"note","page":1,"at":[100,100],"text":"Review here"}]}'
```

MCP uses `run_workflow("editor", {"action":"open", "path":"input.pdf", "ops":[...]})`.
For later actions pass `session` and `revision` from actual status, never example
IDs. Timeouts have uncertain outcomes: inspect before retrying. The session is
real GTK state and ends with the process; it is not persistent document history.

## Signature recording

Use `signatures` to inspect/import existing authorized SVG/PNG files. To draw,
call `record_signature` with `{"action":"start","name":"initials","click":true}`
(mouse) or omit click for the trackpad recorder. Existing names require explicit
replacement confirmation. Tell the human to draw and press Enter, or Escape to
cancel. Poll `{"action":"status","job":"RETURNED_ID"}`. Only `saved:true` with
a saved path/hash completes recording. `cancel` requests cancellation; wait for
`cancelled`. For failed/uncertain jobs inspect the library before retry. No
headless drawing is available. Placement remains a separately confirmed edit.

## Saved artifacts and external applications

Use `export` with `path`, `action`, and a new `output` for copy/zip/flatten.
Flatten requires confirmation and refuses pending redactions. Reopen PDFs or
inspect ZIP members, compare source fingerprint, and render before handoff.
For pending editor work, save and verify first.

External actions are email, localsend, folder, clipboard-file and zip-clipboard.
They require `confirm:true`; missing desktop helpers are errors. `handed_off`
only means the external application was launched. `delivered:false` is explicit.
The human chooses a recipient and sends in that app. Never claim delivery.
Clipboard ownership can change; inspect the pasted artifact.

`clipboard` copy-pages requires path/pages/confirmation. paste-pages saves the
clipboard PDF to a new output; inspect it, then use insert_pages. Cut is a
successful copy followed by separately confirmed page deletion. Do not delete
if copying failed. Extract/insert provides the file-based drag-export route.
