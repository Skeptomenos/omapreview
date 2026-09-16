"""Editorial 18×18 cairo glyphs used on the overlay rail.

Undo / redo curve over the top, with heads at the upper-left and
upper-right respectively.
"""

from __future__ import annotations

import cairo


def _ink(ctx, fg, width: float = 1.65) -> None:
    if len(fg) == 4:
        ctx.set_source_rgba(*fg)
    else:
        ctx.set_source_rgba(*fg, 1.0)
    ctx.set_line_width(width)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)


def paint_undo(ctx, fg) -> None:
    """Upper arc returning to an arrowhead at the upper-left."""
    _ink(ctx, fg)
    # Tail on the right; the arch joins the head without a gap.
    ctx.move_to(14.0, 13.0)
    ctx.curve_to(16.0, 5.0, 7.0, 2.0, 3.0, 7.0)
    ctx.move_to(3.0, 3.0)
    ctx.line_to(3.0, 7.0)
    ctx.line_to(7.0, 7.0)
    ctx.stroke()


def paint_redo(ctx, fg) -> None:
    """Mirror of undo, with the arrowhead at the upper-right."""
    ctx.save()
    ctx.translate(18.0, 0.0)
    ctx.scale(-1.0, 1.0)
    paint_undo(ctx, fg)
    ctx.restore()
