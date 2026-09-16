# CLI and MCP application workflow acceptance — 2026-09-16

The current source provides discoverable CLI and MCP routes for the meaningful
editor workflows below. This is candidate acceptance, not a release record.
Published v0.1.1 is unchanged. OCR is outside this batch.

Code under test: `7720fb380182e65ca3a50bfd6c73fdaba9b07318`, branch
`codex/full-cli-mcp-parity`, worktree `b2ec`. Implementation commits are
`7506f99`, `46fe476`, `a7621ca`, `9c124e2`, and `7720fb3`.
Linear: [DEV-195](https://linear.app/helmus/issue/DEV-195).
The root task owns combined integration and issue closure.

## Coverage

CLI discovery uses `workflow-schema`; calls use `workflow NAME --args JSON`.
MCP discovery uses `workflow_schema`; calls use `run_workflow`.
The existing operation schema and engine remain the document write path.

| Capability | Route and result | Evidence |
|---|---|---|
| Document search | `search`: paginated text hits, canonical coordinates and source fingerprint | Real CLI and stdio MCP; rotated/cropped targets and invalid requests |
| Signature library | `signatures`: list, inspect, add/import, confirmed remove/replace | Both interfaces; hashes, refusal and concurrent-writer checks |
| Human signature recording | `record_signature`: start, status, cancel | Real native synthetic stroke saved; both interfaces verified asset hash; cancellation, startup death and replacement races tested |
| Live editor | `editor`: explicit session/revision, proposal opening, pending stage/select/move/replace/delete, page operations, view/search, history, Save and close | Both interfaces against visible GTK; stale revisions, source conflicts and confirmation refusals; native crop/Save read back independently |
| Export and sharing | `export`: copy, ZIP, flatten and confirmed app/clipboard handoffs | Both interfaces; new-output protection, snapshot fingerprint, helper errors; delivery never asserted |
| Page clipboard | `clipboard` plus existing extract/insert/delete operations | Both interfaces with isolated helpers; required native clipboard suite passed |
| Saved PDF edits | Existing read/render and operation catalog | Required regression suite; shared engine remains authoritative |
| Installation | GUI and CLI together, four command aliases, optional MCP | Isolated release/checkout clean and upgrade installs, real installed MCP, base without MCP, Arch payload |

The full contract and remaining presentation controls are in
[the application workflow specification](../ops.md#application-workflows).
Agent routes are in `skill/references/desktop-workflows.md` and the CLI/MCP mode
references. No headless signature drawing or external delivery is claimed.

## Final checks

All commands ran in the candidate worktree unless stated otherwise.

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest tests/ -q` | Exit 0: **460 passed**, 410 dependency deprecation warnings, 95.02 seconds |
| Final clipped/repeated crop CLI/MCP regressions | 8 passed; independent root recheck also 8 passed |
| Documentation and packaging checks | 11 passed, 2.53 seconds |
| `.venv/bin/python -m build --wheel --no-isolation` | Exit 0; final candidate wheel built |
| Isolated installer harness `packaging/namespace.sh` in evidence archive | Exit 0; release clean/upgrade, checkout plain-venv repair/upgrade, aliases, CLI discovery and actual installed MCP passed |
| Isolated Arch harness `packaging/arch-only.sh` in evidence archive | Exit 0; actual `package()` payload, four executable entrypoints and staged CLI passed using final wheel |
| Installer/PKGBUILD shell syntax, identical PKGBUILDs, desktop validation and `git diff --check` | Exit 0 |

The native post-review check used Cua Driver 0.28.1 and the absolute worktree
launcher. Native crop rebased a pending form fill and note. Native Save preserved
their targets and control text. Fresh CLI and stdio MCP reads verified the saved
CropBox, widget value, note and text. MCP rendering matched the CLI PNG bytes;
the saved render was visually inspected. A separate native recorder completed
a synthetic stroke. CLI and MCP status and library inspection verified its hash.
All owned windows and driver processes closed. The shared GUI lease was released
after the required suite passed.

## Evidence and limits

Local evidence root:
`/home/david/.local/state/omapreview/coordination/evidence/full-parity/`.
`native/postfix-acceptance-2026-09-16.md` records the exact native interactions,
fixtures, screenshots and saved read/render checks. `checks/` contains final
test/build/install logs. `packaging/` contains the isolated installer scripts,
candidate archive, wheel and installed protocol outputs. Earlier unsuccessful
probes remain available and are superseded by the final results above.

Fixtures and signatures were synthetic. Installation ran inside a private
filesystem namespace with the same HOME string. Package-manager and desktop
refresh steps were simulated using existing host dependencies. CLI, wheel
installation, optional MCP installation/protocol and Arch packaging were real.
The host install, shell configuration and published release were not replaced.
External email/LocalSend/clipboard handoff tests used isolated helper programs;
no recipient received a file.

Editor sessions and recording require GTK and a graphical session. Editor history
and recorder job status are local runtime state. A timed-out mutation requires
fresh inspection before retry. External handoff success does not prove delivery.
Future OCR must extend the shared operation contract and run heavy work outside
the GTK loop; the recording handoff is not a general job service.
