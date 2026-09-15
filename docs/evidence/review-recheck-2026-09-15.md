# Review recheck — 2026-09-15

## Snapshot and result

This is a dated assessment of the supplied review, not a record of fixes.

- Reviewed snapshot: `8de8f895217c7d4bd9c62f6b9dc0172e7cc146ca`.
- Rechecked snapshot: `784d15893a1c2073144c349ab4775b30a20ce80e` (`main`).
- `git ls-remote origin refs/heads/main` returned the rechecked SHA.
- Nine commits separate the snapshots. Seven of the eight backend files in the review manifest remain byte-identical. `page_preview.py` changed for empty launch, without correcting the reported failures.
- Environment: omarchy-air; Python 3.14.7; PyMuPDF 1.28.2; GTK 4.22.5.
- Result: **24 findings have confirmed defects; R23 remains a source-backed performance concern; R26 is fixed.**

The report is substantially sound. Its 22 recorded defect observations, covering 18 findings, reproduce on the newer checkout and library. Its three controls also retain their expected outcomes. Additional checks establish the six remaining functional findings through current Editor methods, extracted callback/wrapper bodies, or the native GTK type check. The broad performance claim has not been benchmarked.

## Finding-by-finding assessment

“Callback” below means the unchanged function body ran with UI side effects replaced by recording functions. It does not establish native event delivery. “Now”, “Next”, and “Later” are recommendations for discussion, not approved implementation scope.

| ID | Status / suggested timing | Current evidence and consequence |
|---|---|---|
| R01 | Confirmed / Now | [Pointer conversion](../../src/omepreview/gui.py#L1123) omits derotation. The engine probe retains `SECRET` at the selected display location while reporting no text remnant. A correct unrotated redaction control succeeds. |
| R02 | Confirmed / Now | [Save](../../src/omepreview/gui.py#L702) publishes separate operations. Both the supplied harness and the real Editor reproduce partial commit: disk rotation 90°, preview 180° after failure. The real-Editor trigger removes a synthetic signature after preview and before Save. |
| R03 | Confirmed / Now | [Extraction](../../src/omepreview/engine.py#L444) publishes inside the batch. All four cases reproduce: existing output replaced before failure; extract-only rewrites source; output collision loses excerpt; source alias reduces a two-page input to one page before failure. |
| R04 | Confirmed / Now | [Operation sorting](../../src/omepreview/engine.py#L519) moves both annotation deletions after a page move. Both SECOND-page notes disappear; both FIRST-page notes survive. |
| R05 | Confirmed callback / Now | [Reload](../../src/omepreview/gui.py#L3527) drops the pending rotation, retains history, and leaves a note intended for FIRST pointing at SECOND after an external move. The toast says unsaved items were kept. |
| R06 | Confirmed real Editor / Now | [Proposal import](../../src/omepreview/gui.py#L777) loses `apply_now: false` and white fill, and drops a rotate operation. Actual copy-save removes `SECRET` and leaves rotation at 0°. |
| R07 | Confirmed / Now | [Validation](../../src/omepreview/ops.py#L186) converts the string `"false"` to `True`; applying it removes the synthetic text. Valid JSON boolean `false` is a distinct case. |
| R08 | Confirmed fault injection / Now | [History restoration](../../src/omepreview/gui.py#L614) truncates the live path. Simulated disk-full leaves 8 of 1,115 bytes and a closed document handle. |
| R09 | Confirmed / Now | [History entries](../../src/omepreview/gui.py#L602) lose pasted input resources and the identity baseline. Both the supplied harness and the real Editor fail undo after save deletes the paste source. The second probe finds two preview pages but one identity. |
| R10 | Confirmed native API / Now | [Clipboard reader](../../src/omepreview/page_clipboard.py#L147) raises `Must be GObject.GType, not str` on this host. This is also one of the earlier repository-suite failures. The nested blocking loop needs review, but no UI hang was induced. |
| R11 | Confirmed callback / Now | [Key handler](../../src/omepreview/gui.py#L3120) consumes Ctrl+Z and Page Down with no action when the sidebar is open. The same handler executes undo/navigation with the sidebar closed. |
| R12 | Confirmed model / Next | [Move dispatch](../../src/omepreview/gui.py#L1007) raises `KeyError: 'strokes'` for both a hit field-fill ghost and a hit annotation-deletion ghost. The drag callback reaches this method; native dragging was not exercised. |
| R13 | Confirmed / Next | [Dry-run dispatch](../../src/omepreview/engine.py#L354) skips state changes that later operations depend on. Redact-then-highlight previews successfully and fails on execution. |
| R14 | Confirmed / Next | [Final verification](../../src/omepreview/engine.py#L334) checks the old page position after a move. It rejects unrelated `KEEP` text. This reproduction is a false rejection, not evidence that a secret escaped verification. |
| R15 | Confirmed / Next | [Insert preview](../../src/omepreview/page_preview.py#L254) shows APPROVED; replacing the external source before Save inserts REPLACED. The preview is not bound to immutable input bytes. |
| R16 | Confirmed / Next | [Form fill](../../src/omepreview/engine.py#L72) changes the first same-name widget on page 1; the intended empty widget on page 2 stays empty. GUI `to_ops()` discards page/rectangle information. |
| R17 | Confirmed / Next | [Page copying](../../src/omepreview/engine.py#L305) drops an internal link even when both linked pages are imported or extracted. Ordinary page reordering retains the form widget in the separate control. |
| R18 | Confirmed / Next | [Main save](../../src/omepreview/engine.py#L497) removes AES-256 owner-password protection from an input with an empty user password. This is preservation loss, not demonstrated access to a password-locked document. |
| R19 | Confirmed narrow failure / Next | [Grid rendering](../../src/omepreview/render.py#L33) does not reject a negative step. The bounded probe reaches x=-100 after 100 lines, moving away from termination. Broader numeric and resource limits need concrete budgets rather than a blanket redesign. |
| R20 | Confirmed / Next | [Snapshot](../../src/omepreview/render.py#L54) and ZIP creation use 0644 under umask 022. The actual [ZIP callback](../../src/omepreview/gui.py#L2839), with clipboard submission replaced by a sink, overwrites an unrelated same-stem archive. No cross-user disclosure was demonstrated. |
| R21 | Confirmed wrapper behavior / Next | [Generic MCP apply](../../src/omepreview/mcp_server.py#L42) removes text with default arguments; the dedicated redaction wrapper preserves source bytes and asks for confirmation. Current wrapper bodies were executed directly; no MCP protocol session was run. |
| R22 | Confirmed / Next | [Signature import](../../src/omepreview/signature.py#L72) replaces a valid synthetic signature with invalid SVG text and reports success. Subsequent decoding raises `FileDataError`. |
| R23 | Source-backed risk / Measure first | [Thumbnail refresh](../../src/omepreview/gui_pages.py#L118) eagerly renders every page twice. Preview replay, synchronous search/recording, and full-byte history are visible in code. No latency or memory measurements establish user impact or a suitable budget yet. |
| R24 | Confirmed / Later | [Preview rebuild](../../src/omepreview/page_preview.py#L182) leaves one untracked scratch PDF after rejecting page 99. The temporary test directory contains the leak. |
| R25 | Confirmed / Later | [Inserted-page classification](../../src/omepreview/page_preview.py#L278) marks an original blank page as inserted after rotation alone. This is a misleading badge, not demonstrated PDF corruption. |
| R26 | Fixed | The [README](../../README.md) now includes `omapreview apply document.pdf --ops edits.json -o out.pdf`. The exact argument form parses and applies to a generated PDF while preserving its source. Fixed by the later README rewrite. |

## Recommended work boundaries

Start with document safety and two small daily-use repairs. Each item below needs its own acceptance brief before implementation. Items touching `engine.py` or `gui.py` need integration coordination.

1. **Atomic Save and history restoration — R02, R08.** Stage the full requested save and publish once. Keep bytes, pending state and history coherent after failure. A save retry must apply each operation once.
2. **Safe extraction outputs — R03.** Preserve extract-only inputs; reject aliases/collisions; avoid publishing excerpts before batch success. Define multi-output failure behavior explicitly: a filesystem rename is atomic for one path, not several outputs.
3. **Rotated editing coordinates — R01.** Test pointer-to-PDF and PDF-to-overlay transforms for 0/90/180/270° and nonzero crop origins. Verify the intended text disappears after reopen.
4. **Lossless proposals and strict flags — R06, R07.** Preserve supported operation intent, reject unsupported variants visibly, and reject invalid boolean types.
5. **Stable edit targets and history — split R04, R05, R09 into focused items.** Sequential annotation targets, external-change conflicts, and self-contained undo each need explicit behavior and distinct tests. Address R14/R15/R16 alongside the relevant identity/input contracts.
6. **Clipboard and keyboard repairs — separate R10 and R11 tasks.** Both are concrete, small enough to start early, and affect ordinary editing. R12 is another bounded interaction repair.

Then address preview/MCP consistency (R13/R21), document preservation and output handling (R17/R18/R20/R22), and bounded input validation (the proven R19 case first). R24/R25 can follow or join relevant preview work. Measure R23 before choosing workers, caches, or history limits.

Do not turn each mitigation paragraph into required architecture. In particular, cryptographic confirmation binding in R21 and a broad concurrency rewrite in R23 are design suggestions, not established prerequisites for correcting the demonstrated defects.

## Commands and evidence

The original review bundle remains unchanged. Adapted runners, additional checks and raw JSON live in the machine-local [recheck folder](/home/david/Downloads/omapreview-review-2026-09-15/omapreview-review-2026-09-15/recheck-784d158).

```bash
git ls-remote origin refs/heads/main
git log --oneline 8de8f895217c7d4bd9c62f6b9dc0172e7cc146ca..784d158
.venv/bin/python /home/david/Downloads/omapreview-review-2026-09-15/omapreview-review-2026-09-15/recheck-784d158/run_probes.py --repo /home/david/workspace/personal/omapreview --output /home/david/Downloads/omapreview-review-2026-09-15/omapreview-review-2026-09-15/recheck-784d158/results
.venv/bin/python /home/david/Downloads/omapreview-review-2026-09-15/omapreview-review-2026-09-15/recheck-784d158/additional_checks.py
```

Both probe commands exited 0. This means the diagnostic runners completed; it does **not** mean the product passed those scenarios. The JSON records the defects.

Harness adaptations: include the current `has_document()` method in the AST loader; redirect signature-store functions to synthetic temporary directories without changing the user's HOME. The original save/undo method bodies remain unchanged. Additional checks use the real Editor constructor, proposal loader and `to_ops()` where specified.

The earlier full-suite run at this same source SHA returned **237 passed, 2 failed, 1 warning**. Those failures were the native clipboard API and `test_add_and_list_svg`. Re-running only the signature test with an isolated `XDG_CONFIG_HOME` returned **1 passed in 0.30s**. Its earlier extra `default` entry is test-environment contamination through the legacy store, distinct from the corrupt-import defect R22. The whole suite was not repeated for this source-unchanged assessment.

The bound Linear project was read. The project-filtered `linear issue list` returned no issues; that CLI command lists the current user's issues, so this does not prove there are no unassigned or other-assignee issues. No issue-completion status is inferred from it.

## Review quality and limits

- The review separates executed observations from source analysis and includes useful controls. That distinction holds up under recheck.
- The original partial-save probe supplies normalized operations through a `to_ops` adapter, so it did not by itself prove the normal ghost path. The additional real-Editor signature-removal case closes that gap.
- The ordinary redaction control removes its synthetic secret. The findings do not show that every redaction fails.
- Actual native clipboard argument validation ran. Other GUI checks exercise model methods or extracted callback logic, not GTK event delivery, drawing or file-monitor timing.
- No real user PDF or signature was used. No package build, adversarial PDF corpus, full MCP session, hardware test, performance benchmark or deployment was performed.
- This checks the supplied findings at the recorded SHA. It is not a complete audit of changes introduced after the original review.

External contract checks: [PyMuPDF page coordinates](https://pymupdf.readthedocs.io/en/latest/page.html#modifying-pages) require unrotated coordinates; [GTK clipboard `read_value_async`](https://docs.gtk.org/gdk4/method.Clipboard.read_value_async.html) accepts a GType, not a MIME string. Both agree with the local code and runtime evidence.
