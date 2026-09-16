# The omepreview editor

The editor lives at [`src/omepreview/gui.py`](../src/omepreview/gui.py) and launches
with `omapreview edit` (empty window) or `omapreview edit doc.pdf`. It is also
the target of `omapreview open` and the `omapreview.desktop` handler (`Exec`
is `omapreview edit %f` — `%f` is omitted on a no-file launcher start).
Open from the app (Ctrl+O). It is GTK4 + cairo + PyMuPDF — pure Python, no
compiled UI code. This directory holds its design notes.

## Design principles (all hold today)

1. **A client, not a fork.** The GUI builds the same JSON ops the CLI and
   MCP server produce and hands them to the same engine on Save. No
   GUI-only capabilities.
2. **Agent proposes, human confirms.** `--ops proposal.json` renders an
   agent's dry-run ops as selected, draggable ghost overlays — nudge with
   mouse or arrow keys, Save applies. The ✦ ask button closes the loop the
   other way: question → `omarchy agent prompt` → agent works → the disk
   watcher reloads the view when the file changes.
3. **Preview.app speed.** Open instantly, thumbnails, fit-width, search,
   sign, save. Nothing else fights for attention.

## Architecture notes

- **Ghost model**: pending items (`sig`, `text`, `note`, `highlight`,
  `ink`) live in `Editor.pending`, draw over the rendered page, and
  convert via `Editor.to_ops()` on Save. Signatures are SVG; the Sign
  tool lists saved ones for drag-and-drop onto the page. Selection =
  identity in that list.
- **Undo across saves**: the undo stack holds two entry kinds — pending
  snapshots and *save boundaries* (pre/post file bytes + the pending list
  that was saved). Undoing a save writes the old bytes back and restores
  the items as ghosts.
- **Rendering**: current page rasterized by PyMuPDF at the fit/zoom scale
  into a cairo surface; ghosts and search-match overlays draw above it in
  page coordinates (ctx scaled by zoom). Two-finger pinch (`Gtk.GestureZoom`)
  live-scales that pixmap around the pinch (else viewport) center; the PDF
  is re-rasterized once on gesture end. Vertical scroll is restored so the
  focal point stays at the same screen height — the scroller is never
  reset to 0 on zoom. Ctrl+scroll keeps the same vertical rule.
- **Sign popover**: horizontal gallery of saved SVGs (preview above the
  name). Drag uses an in-process `Gtk.GestureDrag` on the card (not
  `Gtk.DragSource` / `DropTarget`, which froze the GTK loop on Hyprland).
  On release over the page, a `place_signature` ghost is placed on idle.
  Click-place remains the fallback. Record new / Re-record share one row.
- **Icons**: hand-drawn cairo painters (`paint_*`) in one stroke language,
  colored by the widget's foreground (theme-proof); the pen icon draws in
  the current ink color and doubles as the color indicator.
- **File watcher**: a `Gio.FileMonitor` reloads clean sessions and thumbnails
  on external changes, guarded against omepreview's own writes (save/undo/redo).
  A dirty session keeps its local preview and history, marks a conflict, and
  blocks Save/Undo/Redo until the user reloads or reopens the file.
- **Page preview inputs**: PDF and image pages are copied into private session
  storage when inserted. Later changes or removal of the picked file cannot
  change the pending Save.
- **OCR copy**: Recognize text discovers dependencies and preflights the chosen
  pages off the GTK loop. Approval binds the saved source hash and a new copy
  path. One editor-local worker calls `engine.apply` with phase progress and
  cancellation. The bridge returns a task ID quickly; status and cancel read
  cached state. The editor opens a verified copy automatically only when it
  recognized text, its revision still matches approval, and no undo history
  would be cleared. Otherwise it keeps the current document and offers Open copy.
  Close requests cancel and wait for the worker to stop. OCR is available only
  from current source with the optional extra, not published v0.1.1.
- **Comments**: clicking near a saved annotation (select tool) pops its
  content — values are copied out of the PyMuPDF annot objects inside the
  iteration loop (they can go stale), and the popover opens via
  `GLib.idle_add` so the triggering click can't dismiss it.

## Known limits / next steps

- Saved annotations can be read but not yet moved or deleted from the GUI
  (needs a `delete_annotation` op — see the roadmap).
- Single-page view (no continuous scroll across pages); Up/Down and the
  sidebar make this cheap, but continuous mode may come later.
- At very narrow window widths the toolbar needs an overflow ⋯ menu.
