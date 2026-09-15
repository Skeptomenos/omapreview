"""Render pages to images — including the agent's coordinate grid.

snapshot() draws an optional labeled grid in PDF-point coordinates over the
page before rasterizing. An agent (or human) can look at the image and read
off exactly the numbers that place_signature / text_box / ink expect, since
the grid IS the ops coordinate system.
"""

from __future__ import annotations

import math
from pathlib import Path

import pymupdf

GRID_COLOR = (0.85, 0.2, 0.2)
MIN_GRID_STEP = 1.0
MAX_GRID_STEP = 10_000.0
MAX_GRID_LINES = 4_000
MIN_SNAPSHOT_SCALE = 0.05
MAX_SNAPSHOT_SCALE = 4.0
MAX_SNAPSHOT_DIMENSION = 16_384
MAX_SNAPSHOT_PIXELS = 40_000_000


def _bounded_positive(
    value: float, label: str, minimum: float, maximum: float
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def page_view_matrix(zoom: float) -> pymupdf.Matrix:
    """Scale-only matrix for on-screen rasters.

    ``Page.get_pixmap`` already honours ``/Rotate``. ``prerotate(page.rotation)``
    applies it a second time, so a 90° rotate looks like 180°.
    """
    z = float(zoom)
    return pymupdf.Matrix(z, z)


def _page_dimensions(width: float, height: float) -> tuple[float, float]:
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
        for value in (width, height)
    ):
        raise ValueError(
            f"page dimensions must be finite and positive, got {width}x{height}"
        )
    return float(width), float(height)


def _validate_snapshot_budget(
    width: float, height: float, scale: float
) -> tuple[int, int]:
    """Return conservative output dimensions or reject before pixmap allocation."""
    width, height = _page_dimensions(width, height)
    pixel_width = math.ceil(width * scale)
    pixel_height = math.ceil(height * scale)
    pixels = pixel_width * pixel_height
    if (
        pixel_width > MAX_SNAPSHOT_DIMENSION
        or pixel_height > MAX_SNAPSHOT_DIMENSION
        or pixels > MAX_SNAPSHOT_PIXELS
    ):
        raise ValueError(
            f"snapshot would render {pixel_width:,}x{pixel_height:,} pixels "
            f"({pixels:,} total); limits are {MAX_SNAPSHOT_DIMENSION:,} pixels "
            f"per side and {MAX_SNAPSHOT_PIXELS:,} total. Reduce --scale or "
            "crop the PDF"
        )
    return pixel_width, pixel_height


def _grid_line_count(length: float, step: float) -> int:
    return max(0, math.ceil(length / step) - 1)


def _validate_grid_budget(
    width: float, height: float, step: float
) -> tuple[int, int]:
    """Return vertical/horizontal line counts or reject before page drawing."""
    width, height = _page_dimensions(width, height)
    vertical = _grid_line_count(width, step)
    horizontal = _grid_line_count(height, step)
    total = vertical + horizontal
    if total > MAX_GRID_LINES:
        raise ValueError(
            f"grid would draw {total:,} lines and {total * 2:,} labels; "
            f"limit is {MAX_GRID_LINES:,} lines. Increase --grid or crop the PDF"
        )
    return vertical, horizontal


def raster_page(page: pymupdf.Page, zoom: float = 1.0, *, alpha: bool = False):
    """Pixmap of *page* at *zoom*, with ``/Rotate`` applied once."""
    return page.get_pixmap(matrix=page_view_matrix(zoom), alpha=alpha)


def snapshot(
    pdf: str | Path,
    page: int = 1,
    output: str | Path | None = None,
    grid: float | None = None,
    scale: float = 2.0,
) -> dict:
    """Render `page` (1-based) to PNG. grid=N overlays labeled lines every N
    points. Returns {"output", "page", "size": [w, h] in points}."""
    scale = _bounded_positive(
        scale, "scale", MIN_SNAPSHOT_SCALE, MAX_SNAPSHOT_SCALE
    )
    if grid is not None:
        grid = _bounded_positive(
            grid, "grid", MIN_GRID_STEP, MAX_GRID_STEP
        )
    pdf = Path(pdf)
    if not pdf.is_file():
        raise FileNotFoundError(f"no such PDF: {pdf}")
    doc = pymupdf.open(str(pdf))
    try:
        if page < 1 or page > doc.page_count:
            raise ValueError(f"page {page} out of range (document has {doc.page_count})")
        pg = doc[page - 1]
        size = [pg.rect.width, pg.rect.height]
        _validate_snapshot_budget(size[0], size[1], scale)

        if grid is not None:
            _draw_grid(pg, grid)

        if output is None:
            suffix = f"-p{page}-grid.png" if grid is not None else f"-p{page}.png"
            output = pdf.with_name(pdf.stem + suffix)
        pix = raster_page(pg, scale)
        pix.save(str(output))
        return {"output": str(output), "page": page, "size": size}
    finally:
        doc.close()


def _draw_grid(pg: pymupdf.Page, step: float) -> None:
    step = _bounded_positive(step, "grid", MIN_GRID_STEP, MAX_GRID_STEP)
    width, height = pg.rect.width, pg.rect.height
    vertical, horizontal = _validate_grid_budget(width, height, step)
    shape = pg.new_shape()

    for index in range(1, vertical + 1):
        x = index * step
        shape.draw_line((x, 0), (x, height))
    for index in range(1, horizontal + 1):
        y = index * step
        shape.draw_line((0, y), (width, y))
    shape.finish(color=GRID_COLOR, width=0.4, stroke_opacity=0.55)
    shape.commit()

    # Labels on both edges so a crop of the image still carries coordinates.
    for index in range(1, vertical + 1):
        x = index * step
        for ly in (10, height - 4):
            pg.insert_text((x + 1, ly), str(int(x)), fontsize=6, color=GRID_COLOR)
    for index in range(1, horizontal + 1):
        y = index * step
        for lx in (2, width - 22):
            pg.insert_text((lx, y - 1), str(int(y)), fontsize=6, color=GRID_COLOR)
