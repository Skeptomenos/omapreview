# UI, CLI and MCP parity — 2026-09-16

## Scope and result

Inspected local `main` at `5845979789a2552e48847af0fd725f65c40de9ae`.
The active Wave 1 worktrees are not included in this result.

**The CLI and MCP have broad document-editing coverage. Full workflow,
discovery and safety parity with the UI is incomplete.** The schema has 15
operation types. The CLI exposes 17 top-level commands. A live application
MCP session advertises 19 tools.

Both generic batch entry points can reach all 15 operation types. This is
source-level routing coverage, not proof that all operations behave correctly
through all interfaces. A synthetic four-operation batch was tested through
both interfaces and produced matching extracted text and annotation types.

## Feature map

“Generic” means CLI `omapreview apply --ops edits.json` or MCP `apply_ops`.
It is a supported route, even when there is no dedicated command or tool.

| Feature | UI | CLI | MCP |
|---|---|---|---|
| Read pages, text, fields and annotations | Page view, comments, field interaction | `read`, `fields`, `pages --list` | `read_pdf`, `list_form_fields`, `list_pages` |
| Highlights and markup styles | Highlighter; imported styles have known losses | `annotate`, generic | `highlight` for text matches; generic for rectangles |
| Sticky notes | Note tool | `note` | `add_note` |
| Text boxes | Text tool | Generic `text_box` | Generic `text_box` |
| Freehand ink and check/cross marks | Pen and stamp buttons | Generic `ink` with supplied strokes | Generic `ink` with supplied strokes |
| Lines, arrows, rectangles and ovals | Shapes tool | `shape` | `add_shape` |
| Form filling | Click empty text fields | `fill`; engine also handles checkboxes | `fill_field`; same engine |
| Place saved visual signatures | Sign picker and placement | `sign` | `place_signature` |
| Manage signature library | Record/re-record/delete | `sig add/draw/list/remove`; draw launches GTK | List and placement only; no add/remove/record tools |
| Rotate, delete, move and insert pages | Thumbnail actions and drag/drop | `pages` options | Dedicated page tools |
| Extract PDF pages | Extract dialog and clipboard exports | `pages --extract`, generic | `extract_pages`, generic |
| Crop | Crop tool | `crop` | `crop_pages` |
| Redact and delete annotations | Pending edits, then Save | `redact`, `delete-annotation` | `redact`, `delete_annotation` |
| Flatten | Share flattened copy | `flatten` | `flatten_pdf` |
| Render a page for visual inspection | Native page rendering | `snapshot`, optional coordinate grid | No snapshot/image-render tool |
| Search | Interactive full-document search | Read text or use edit operations with `match`; no search command | Read text or use matching edit tools; no search tool returning hit rectangles |
| Human review of proposed operations | Draggable pending edits; import has known losses | `edit FILE --ops proposal.json` | No direct propose/open-editor tool; generic apply can dry-run |
| Undo/redo and pending-session state | Editor history | No headless session API | No editor-session API |
| Share and clipboard actions | Email, LocalSend, copy, ZIP, show in folder | No equivalent share commands | No equivalent share tools |

UI actions such as dragging, clipboard gestures and undo history need not each
become an API. The useful parity target is an equivalent document result and
a clear review/verification workflow.

The architecture also has exceptions to the “one write path” shorthand:
flatten uses `engine.flatten()`, while GUI page extraction uses
`export_guard.write_pages_if_clean()` and the clipboard serialization helpers.
Shared engine access alone does not prove equivalent export behavior.

## Confirmed gaps

1. **MCP visual verification is missing.** An MCP-only agent can read text and
   geometry but cannot request a rendered page through this server. CLI
   `snapshot` already provides that capability.
2. **Generic operations are hard to discover.** The live `apply_ops` schema
   accepts an array of unrestricted objects. It does not describe individual
   operation variants or required fields. Its tool description names only
   five of the 15 supported types. There are no dedicated text-box or ink tools.
3. **Safety defaults differ.** Live probes confirmed that dedicated MCP
   `redact` writes nothing without confirmation, while generic MCP
   `apply_ops` with a redact operation writes by default. CLI `redact` also
   writes unless `--dry-run` is supplied. MCP `flatten_pdf` writes by default;
   CLI `flatten` has no `--dry-run` option. These defaults contradict broad
   documentation claims about universal dry-run/confirmation behavior.
4. **Proposal import is not a lossless handoff.** The prior review confirmed
   dropped operations and changed redaction/style/geometry intent. Batch B
   owns R06/R07/R19/R21. Its in-progress changes are not accepted evidence yet.
5. **Protocol coverage is incomplete.** Existing tests include CLI subprocess
   checks and direct MCP-wrapper calls. A search of the checked-in tests found
   no MCP initialize/list-tools/call-tool transport tests. The probe below adds
   dated protocol evidence, not a comprehensive regression suite.

The generic confirmation mismatch is already in Wave 1. New parity features
are recommendations only; this audit does not launch or implement them.

## Existing documentation

- [Operations contract](../ops.md): all 15 operations, coordinates, examples
  and a CLI/MCP wrapper table. Best existing technical starting point.
- [Agent playbook](../../skill/SKILL.md): read, inspect, dry-run, verify and
  hand off proposals. Its headline operation list omits newer operations.
- [Manual parity checklist](../preview-parity.md): calls for CLI/MCP/GTK checks,
  but most boxes are blank. It is not a complete current parity report.
- [Roadmap](../roadmap.md): records page-op CLI/MCP support, but still lists
  shapes and crop under “Later” although they exist. The claim that CLI
  dry-run/JSON options exist everywhere is too broad.
- [Preview gap analysis](../omapdf-preview-gap-analysis.md): historical
  comparison with macOS Preview, not a current interface matrix. Its MCP
  inventory lists nine tools, while this audit found 19.
- [Prior review recheck](review-recheck-2026-09-15.md): known correctness
  defects. This audit adds real MCP transport evidence for R21.

Several documents use the older `omepreview` command name. It remains a
declared alias; visitor documentation should use `omapreview`.

## Validation evidence

Used Python 3.14 and MCP SDK 2.2.0 from the checkout's `.venv`. Verified that
`omepreview.__file__` resolves into this checkout. No real user PDFs,
signatures, clipboard operations or native GUI sessions were used.

```bash
.venv/bin/omapreview --help
.venv/bin/python /home/david/.local/state/omapreview/coordination/evidence/parity/2026-09-16/probe.py
```

The probe exited 0. It launched `.venv/bin/omapreview-mcp` over stdio, completed
initialization, listed tools, and called read, generic markup, dedicated and
generic redact, flatten, and invalid-operation paths. It also ran real CLI
read/apply/snapshot/redact commands and inspected the output PDFs.

- 15 operation types; 17 CLI commands; 19 advertised MCP tools.
- Text box, ink, rectangle underline and rectangle shape: CLI/MCP output text
  and annotation types matched. This is not a pixel comparison or full parity test.
- Dedicated MCP redaction default: no output file.
- Generic MCP redaction and CLI redaction defaults: output written and target
  text removed. MCP flatten default: output written.
- Unknown operation: MCP error returned, no output file.
- Synthetic source hash unchanged; every mutation used a separate output.

Probe, synthetic fixtures, output PDFs, MCP stderr and full JSON results:
`/home/david/.local/state/omapreview/coordination/evidence/parity/2026-09-16/`.
The first probe attempt used the SDK v1 error attribute; it was corrected to
SDK v2 `is_error` before the successful run. No product change was needed.

The full repository suite and native UI acceptance were not rerun for this
read-only capability audit. Wave 1 owns the required implementation gates.

## Recommended follow-up

After Wave 1 settles the shared contracts, define one bounded parity task:
add MCP page snapshots, expose the complete operation schema/examples, align
the remaining confirmation behavior, and turn representative cross-interface
cases into CLI/MCP transport regression tests. Update the operation guide and
agent playbook from that verified surface. Signature-library MCP management
and a direct human-review handoff can be separate product decisions.
