# Batch H — verifiable CLI and MCP work

Goal: an agent can discover supported edits, inspect a page through MCP,
perform an authorized task, and verify the saved result through the CLI or
MCP before reporting completion.

This is the implementation brief. The sole live plan, dispatch gate, owner,
progress and evidence are in the `batch-h` section of
[/home/david/.local/state/omapreview/coordination/review-remediation.md](/home/david/.local/state/omapreview/coordination/review-remediation.md).
Do not maintain another progress checklist in a worktree.

## Start gate and boundaries

Start from the recorded, green integration commit of Wave 1 (A/B/C).
Recheck the [interface audit](evidence/interface-parity-2026-09-16.md), because
it describes pre-fix `5845979`. Reuse B's validation/confirmation contracts,
C's coordinate transforms and A's publication guarantees.

Run H as **Wave 1.5**, before D/E/F start. H overlaps D in CLI/MCP contracts
and F in rendering. D/E/F then branch from the green H integration commit.
This brief prepares the batch; it does not launch an implementation task.

Own MCP rendering and operation discovery, the CLI equivalents, narrow
read-only observation support, remaining confirmation inconsistencies,
protocol tests and agent-facing documentation. Entry points:
`src/omepreview/{mcp_server,render,cli,ops,read,engine}.py`, `tests/`,
`docs/ops.md`, `docs/preview-parity.md`, `docs/roadmap.md`, `skill/SKILL.md`.

Keep the core stdlib + PyMuPDF; MCP stays optional. Preserve existing command
names and response fields. This batch does not add OCR, comparison services,
signature-library management tools, editor remote control, sharing integrations,
or a general verification framework. D retains its execution/target/link fixes;
E retains editor history; F retains signature and export storage fixes.

## Required steps

### 1. Establish the integrated contract

- Verify the assigned base, worktree import path and isolated synthetic setup.
  Run the baseline full suite. Inventory operations, CLI commands and actual
  MCP `tools/list`; counts in the earlier audit are not the acceptance target.
- Record which Wave 1 fixes already satisfy confirmation, numeric limits and
  coordinate requirements. Do not implement them again.
- Extend the existing tests and synthetic probe approach. Test the actual
  `.venv/bin/omapreview` and `.venv/bin/omapreview-mcp` entry points.

### 2. Deliver MCP page rendering first

Add a discoverable **`render_page`** tool. Proposed request contract:

```text
render_page(path, page=1, scale=2.0, grid=null, clip=null)
```

- `page` is a 1-based integer. `scale` is finite and positive. Optional `grid`
  is a positive step in PDF points. Optional `clip` is a rectangle in the
  canonical unrotated PDF-point coordinate space used by operations.
- Return an actual MCP PNG **image content block**, plus structured metadata
  identifying the source document fingerprint, page, PDF geometry, rotation,
  clip, pixel dimensions and PDF-to-image coordinate transform. Include a
  JSON text representation of the metadata for compatible clients. A local
  filename or base64 string inside an ordinary JSON field is not sufficient.
- The default renders the entire visible page. Clipping allows detailed
  inspection without rendering an enormous full-page image. With a grid,
  labels describe operation coordinates even after rotation/crop/clipping.
- Reuse `render.raster_page` and the integrated coordinate helpers. Rendering
  and grid overlays must leave source PDF bytes unchanged. Render from a
  consistent file snapshot; metadata must identify the bytes actually rendered.
- Keep MCP rendering read-only and in memory; no output-path parameter,
  persistent scratch files, image uploads or source replacement. It can expose
  a read-only tool hint. CLI `snapshot` keeps its file-output interface and
  gains equivalent clipping/coordinate behavior where needed.
- Reuse B's published scale/grid/pixel bounds. Also bound encoded response
  size and image dimensions. Document concrete limits. Reject invalid pages,
  nonfinite numbers, malformed/outside clips, excessive allocations and
  unsupported/password-locked documents promptly, with an actionable error.
  Do not silently lower resolution or return a stale image as success.
- Test and visually inspect 0/90/180/270-degree pages, nonzero CropBox,
  full-page and clipped views, grid on/off, and a small signature/text target.
  Compare decoded CLI and MCP pixels for identical options.

MCP image blocks and structured results follow the
[MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
Verify SDK v1/v2 compatibility within the project's supported dependency range;
the installed audit environment uses SDK 2.2.0. Keep wire names distinct from
Python attribute names such as SDK v2 `is_error`.

### 3. Make every operation discoverable

- Expose all supported operation variants, required fields, geometry, defaults
  and examples through MCP itself. Prefer a typed `apply_ops` input schema;
  if SDK constraints prevent that, provide a read-only operation-schema tool
  and point to it explicitly from `apply_ops`.
- Provide the same schema/catalog from a CLI JSON command. Document generic
  routes for text boxes, ink and check/cross marks; dedicated wrappers for
  every operation are not required.
- Derive discovery from the authoritative operation contract, or add a
  coverage check that fails when any operation is omitted or unsupported
  fields are advertised. Preserve the CLI/MCP response fields clients use.

### 4. Close remaining confirmation gaps

- Audit generic and dedicated MCP entry points and CLI commands after B lands.
  Signing, flattening, redaction and deletion must follow one documented
  confirmation policy. Unconfirmed consequential requests do not write.
- Reuse B's established explicit confirmation convention. Add missing CLI
  dry-run/confirmation support and remaining wrapper support rather than
  introducing a second policy or an authentication system.
- Treat the CLI default change as a compatibility change: document the exact
  before/after commands, flags and migration examples. Preserve command names.
- Test omitted, explicit false, explicit true and malformed flags. Test the
  generic route as well as dedicated wrappers so a batch cannot bypass policy.
  A rendering or verification request never confirms a later mutation.

### 5. Require verification of each supported task

The agent playbook must teach this loop:

1. Read the source and state the requested postconditions and protected content.
2. Render relevant pages/regions when placement, appearance or image content
   matters. Resolve coordinates from that same document version.
3. Propose/dry-run consequential work. Obtain the authorization the task needs.
4. Apply once to the intended destination and retain the source when required.
5. Reopen/read the **saved output** and render affected pages without the grid.
   Compare observations with the requested result and protected content.
6. Report completion only when those checks pass. Otherwise report the failed
   or unavailable check, preserve the evidence, and repair/retry within scope.

An “applied” report, successful tool call, file existence or attractive image
alone is not verification. Pre-edit images and pending GUI previews do not
prove the contents of the saved file. Use the output path and fingerprint to
bind evidence to the result. Validate image-based tasks visually and content
tasks with fresh structured reads; use both when needed.

| Operation/task | Required independent observations after saving |
|---|---|
| `highlight`, `note`, `text_box`, `ink`, `shape` | Annotation/text type, content, geometry and count as relevant; rendered placement/style; neighboring content retained. Check/cross marks use the ink route. |
| `fill_field` | Intended field value and page/target identity; rendered appearance. Ambiguous targets are not a verified success. |
| `place_signature` | Saved-page rendering shows the requested signature, position/size and optional date; protected text remains readable. This is visual signing, not certificate verification. |
| `rotate_pages`, `move_pages`, `delete_pages`, `insert_pages` | Page count, order, identity, rotation/size and affected page renders; check an unchanged control page. Include PDF, blank and image inserts. |
| `extract_pages`, `crop_pages` | Output selection/geometry and renders; extraction source hash unchanged; crop does not claim content destruction. |
| `delete_annotation` | Intended annotation absent; unrelated annotations retained; render reflects removal. |
| `redact` | Existing serialized-content/payload verification passes, fresh reads show targeted content absent, neighboring content survives, and rendering shows expected appearance. A black rectangle is not proof of redaction. |
| Flatten | Fresh reads show annotations/widgets baked as intended; before/after renders retain their appearance; staged redactions are refused. |

Each row needs a usable CLI and MCP observation route. Add only narrow
read-only fields/tools where current `read`, `fields`, `pages` and rendering
cannot expose a listed fact. Document the limits of every verification claim.
Do not add a universal `verified: true` flag that hides incomplete checks.

Known D/E/F correctness defects remain assigned there. H must demonstrate that
its observation tools expose a deliberately wrong result and that the recipe
does not call that task complete; it must not silently absorb all remaining
engine repairs. Record unresolved failures explicitly in the shared plan.

### 6. Validate, document and hand back

- Add real MCP initialize/initialized, tools/list and tools/call tests for
  rendering, discovery, edits, confirmation and errors. Validate response
  schemas and decode the returned PNG bytes. Keep stdout protocol-clean.
- Cover every operation type with a positive saved-result verification case
  through generic CLI and MCP batches; cover specialized wrappers by behavior
  group. Include failed confirmation, invalid input, unchanged source/output
  on rejection and an intentionally wrong/stale result that verification catches.
- Add one complete MCP-only journey: discover → read/render → propose →
  authorized apply → read/render saved output → evidence-based conclusion.
  Do not use hidden shell/PyMuPDF reads to compensate for missing MCP features
  in this acceptance journey. Test code may use PyMuPDF as an independent oracle.
- Exercise the analogous CLI journey. Save commands, protocol responses,
  source/output hashes, decoded images and assertions under the shared
  `evidence/batch-h/<run-id>/` directory.
- Inspect generated images directly. Use cua-driver and the GUI lease to
  open synthetic outputs and compare native appearance when shared rendering
  or coordinate behavior changes. No desktop/native test may claim success
  from a model-only harness.
- Update `docs/ops.md`, `skill/SKILL.md`, the manual parity checklist and stale
  roadmap entries. Publish a compact feature/verification map and runnable
  CLI/MCP examples, including optional MCP setup. Coordinate README links with
  its owner; leave the dated audit unchanged.
- Run the full `.venv/bin/python -m pytest tests/ -q` suite: exit 0. Obtain
  independent review of consequential default changes and the evidence binding.
  Repeat affected checks after any review fix or integration conflict.
- Hand back the tested commit, operation/verification coverage, CLI and MCP
  logs, actual images, GUI evidence where applicable and any explicit limits.
  A failed or unavailable required verification leaves the batch incomplete.

## Completion gate

H is ready only when MCP delivers inspectable page images, all supported edits
are discoverable, consequential entry points obey the documented policy, each
operation has a demonstrated post-save verification route, and all required
checks pass on the recorded commit. The orchestrator then integrates H and
rechecks the combined result before dispatching D/E/F.

After H lands, [Batch I](agent-skill-batch.md) restructures the skill into a
small entry file with mode and scenario references. H still ships the accurate
verification instructions required here; I owns the later progressive-disclosure
work and tests its recipes against the implemented interfaces.
