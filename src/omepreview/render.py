"""Render pages to images — including the agent's coordinate grid.

snapshot() draws an optional labeled grid in PDF-point coordinates over the
page before rasterizing. An agent (or human) can look at the image and read
off exactly the numbers that place_signature / text_box / ink expect, since
the grid IS the ops coordinate system.
"""

from __future__ import annotations

import math
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pymupdf

GRID_COLOR = (0.85, 0.2, 0.2)
MIN_GRID_STEP = 1.0
MAX_GRID_STEP = 10_000.0
MAX_GRID_LINES = 4_000
MIN_SNAPSHOT_SCALE = 0.05
MAX_SNAPSHOT_SCALE = 4.0
MAX_SNAPSHOT_DIMENSION = 16_384
MAX_SNAPSHOT_PIXELS = 40_000_000
MAX_RENDER_PNG_BYTES = 8_000_000
MAX_RENDER_BASE64_BYTES = ((MAX_RENDER_PNG_BYTES + 2) // 3) * 4


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


def _raster_bounds(rect: pymupdf.Rect, scale: float) -> tuple[int, int, int, int]:
    """Return the integer pixmap origin and dimensions for a display clip."""
    x0 = math.floor(rect.x0 * scale)
    y0 = math.floor(rect.y0 * scale)
    x1 = math.ceil(rect.x1 * scale)
    y1 = math.ceil(rect.y1 * scale)
    return x0, y0, x1 - x0, y1 - y0


def _validate_raster_budget(rect: pymupdf.Rect, scale: float) -> tuple[int, int]:
    """Validate the actual integer allocation implied by a transformed clip."""
    _page_dimensions(rect.width, rect.height)
    _x, _y, pixel_width, pixel_height = _raster_bounds(rect, scale)
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


def _grid_label_positions(
    width: float,
    height: float,
    step: float,
    label_rect: pymupdf.Rect | None = None,
) -> tuple[list[float], list[float]]:
    """Return grid lines whose labels can appear in the requested view."""
    vertical, horizontal = _validate_grid_budget(width, height, step)
    xs = [index * step for index in range(1, vertical + 1)]
    ys = [index * step for index in range(1, horizontal + 1)]
    if label_rect is not None:
        xs = [x for x in xs if label_rect.x0 <= x <= label_rect.x1]
        ys = [y for y in ys if label_rect.y0 <= y <= label_rect.y1]
    return xs, ys


def raster_page(
    page: pymupdf.Page,
    zoom: float = 1.0,
    *,
    alpha: bool = False,
    clip: pymupdf.Rect | None = None,
):
    """Pixmap of *page* at *zoom*, with ``/Rotate`` applied once."""
    return page.get_pixmap(matrix=page_view_matrix(zoom), clip=clip, alpha=alpha)


def _matrix_point(matrix: pymupdf.Matrix, x: float, y: float) -> tuple[float, float]:
    return (
        matrix.a * x + matrix.c * y + matrix.e,
        matrix.b * x + matrix.d * y + matrix.f,
    )


def _matrix_rect(matrix: pymupdf.Matrix, rect: pymupdf.Rect) -> pymupdf.Rect:
    points = (
        _matrix_point(matrix, rect.x0, rect.y0),
        _matrix_point(matrix, rect.x1, rect.y0),
        _matrix_point(matrix, rect.x0, rect.y1),
        _matrix_point(matrix, rect.x1, rect.y1),
    )
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


@contextmanager
def _unrotated_grid_page(page: pymupdf.Page) -> Iterator[None]:
    """Expose the canonical page frame while drawing a temporary grid."""
    rotation = page.rotation
    try:
        if rotation:
            page.set_rotation(0)
        yield
    finally:
        if rotation:
            page.set_rotation(rotation)


def _validate_clip(
    clip: list[float] | tuple[float, float, float, float] | None,
    width: float,
    height: float,
) -> pymupdf.Rect:
    if clip is None:
        return pymupdf.Rect(0, 0, width, height)
    if not isinstance(clip, (list, tuple)) or len(clip) != 4:
        raise ValueError("clip must be [x0, y0, x1, y1] in unrotated CropBox points")
    values: list[float] = []
    for index, value in enumerate(clip):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"clip coordinate {index} must be a finite number")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"clip coordinate {index} must be finite")
        values.append(number)
    x0, y0, x1, y1 = values
    if x1 <= x0 or y1 <= y0:
        raise ValueError("clip must have positive width and height")
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
        raise ValueError(
            f"clip {values!r} is outside the unrotated CropBox "
            f"[0, 0, {width:g}, {height:g}]"
        )
    return pymupdf.Rect(x0, y0, x1, y1)


def _fingerprint(blob: bytes) -> dict[str, str | int]:
    return {
        "algorithm": "sha256",
        "value": hashlib.sha256(blob).hexdigest(),
        "bytes": len(blob),
    }


def render_page_bytes(
    pdf: str | Path,
    *,
    page: int = 1,
    scale: float = 2.0,
    grid: float | None = None,
    clip: list[float] | tuple[float, float, float, float] | None = None,
) -> tuple[bytes, dict]:
    """Render one page from one immutable source snapshot.

    ``clip`` is always expressed in the public unrotated, CropBox-local
    coordinate system. The returned metadata binds the PNG to the exact
    source bytes and describes both the displayed clip and affine transform.
    """
    scale = _bounded_positive(
        scale, "scale", MIN_SNAPSHOT_SCALE, MAX_SNAPSHOT_SCALE
    )
    if grid is not None:
        grid = _bounded_positive(grid, "grid", MIN_GRID_STEP, MAX_GRID_STEP)
    source = Path(pdf)
    if not source.is_file():
        raise FileNotFoundError(f"no such PDF: {source}")
    blob = source.read_bytes()
    doc = pymupdf.open(stream=blob, filetype="pdf")
    try:
        if doc.needs_pass:
            raise ValueError(f"{source} is password-protected; decrypt it first")
        if not isinstance(page, int) or isinstance(page, bool):
            raise ValueError(f"page must be a 1-based integer, got {page!r}")
        if page < 1 or page > doc.page_count:
            raise ValueError(f"page {page} out of range (document has {doc.page_count} pages)")
        pg = doc[page - 1]
        width, height = _page_dimensions(pg.cropbox.width, pg.cropbox.height)
        local_clip = _validate_clip(clip, width, height)
        display_clip = _matrix_rect(pg.rotation_matrix, local_clip)
        pixel_width, pixel_height = _validate_raster_budget(display_clip, scale)
        if grid is not None:
            _validate_grid_budget(width, height, grid)
            with _unrotated_grid_page(pg):
                _draw_grid(pg, grid, label_rect=local_clip)
        pix = raster_page(pg, scale, clip=display_clip)
        png = pix.tobytes("png")
        if len(png) > MAX_RENDER_PNG_BYTES:
            raise ValueError(
                f"rendered PNG is {len(png):,} bytes; limit is "
                f"{MAX_RENDER_PNG_BYTES:,}. Reduce --scale or crop the PDF"
            )

        rotation = pg.rotation
        matrix = pg.rotation_matrix
        # The pixmap's origin is the displayed clip's top-left. The matrix
        # below maps canonical local PDF points directly into PNG pixels.
        transform = [
            matrix.a * scale,
            matrix.b * scale,
            matrix.c * scale,
            matrix.d * scale,
            matrix.e * scale - pix.x,
            matrix.f * scale - pix.y,
        ]
        metadata = {
            "source_fingerprint": _fingerprint(blob),
            "source_path": str(source),
            "page": page,
            "page_count": doc.page_count,
            "pdf_geometry": {
                "coordinate_space": "unrotated CropBox-local points, top-left origin",
                "mediabox": list(pg.mediabox),
                "cropbox": list(pg.cropbox),
                "size": [width, height],
                "rotation": rotation,
                "visible_size": [pg.rect.width, pg.rect.height],
            },
            "clip": list(local_clip),
            "requested_clip": list(clip) if clip is not None else None,
            "display_clip": list(display_clip),
            "pixel_dimensions": [pix.width, pix.height],
            "png_bytes": len(png),
            "scale": scale,
            "grid": grid,
            "coordinate_transform": {
                "from": "unrotated CropBox-local PDF points",
                "to": "top-left PNG pixels",
                "matrix": transform,
                "display_rotation_matrix": [
                    matrix.a,
                    matrix.b,
                    matrix.c,
                    matrix.d,
                    matrix.e,
                    matrix.f,
                ],
                "display_clip_origin": [display_clip.x0, display_clip.y0],
                "pixel_origin": [pix.x, pix.y],
                "scale": scale,
            },
        }
        if grid is not None:
            grid_xs, grid_ys = _grid_label_positions(width, height, grid, local_clip)
            metadata["grid_labels"] = {
                "coordinate_space": "unrotated CropBox-local PDF points",
                "vertical": [format(value, ".6g") for value in grid_xs],
                "horizontal": [format(value, ".6g") for value in grid_ys],
            }
        if pix.width > pixel_width or pix.height > pixel_height:
            raise ValueError(
                "renderer returned pixels beyond the preflight budget; reduce "
                "the clip or report the PyMuPDF version"
            )
        return png, metadata
    finally:
        doc.close()


def snapshot(
    pdf: str | Path,
    page: int = 1,
    output: str | Path | None = None,
    grid: float | None = None,
    scale: float = 2.0,
    clip: list[float] | tuple[float, float, float, float] | None = None,
) -> dict:
    """Render `page` (1-based) to a PNG file, preserving the CLI shape."""
    pdf = Path(pdf)
    if not pdf.is_file():
        raise FileNotFoundError(f"no such PDF: {pdf}")
    png, metadata = render_page_bytes(
        pdf, page=page, scale=scale, grid=grid, clip=clip
    )
    if output is None:
        suffix = f"-p{page}-grid.png" if grid is not None else f"-p{page}.png"
        output = pdf.with_name(pdf.stem + suffix)
    Path(output).write_bytes(png)
    return {
        "output": str(output),
        "page": page,
        # Preserve the historical displayed-page meaning of ``size``. The
        # canonical unrotated geometry is available explicitly below.
        "size": (
            metadata["pdf_geometry"]["visible_size"]
            if clip is None
            else [metadata["display_clip"][2] - metadata["display_clip"][0],
                  metadata["display_clip"][3] - metadata["display_clip"][1]]
        ),
        "pdf_geometry": metadata["pdf_geometry"],
        "rotation": metadata["pdf_geometry"]["rotation"],
        "clip": metadata["clip"],
        "pixel_dimensions": metadata["pixel_dimensions"],
        "source_fingerprint": metadata["source_fingerprint"],
        "coordinate_transform": metadata["coordinate_transform"],
        "grid": grid,
        "grid_labels": metadata.get("grid_labels"),
        "scale": scale,
    }


def _draw_grid(
    pg: pymupdf.Page,
    step: float,
    *,
    label_rect: pymupdf.Rect | None = None,
) -> None:
    step = _bounded_positive(step, "grid", MIN_GRID_STEP, MAX_GRID_STEP)
    width, height = pg.rect.width, pg.rect.height
    vertical, horizontal = _validate_grid_budget(width, height, step)
    label_xs, label_ys = _grid_label_positions(width, height, step, label_rect)
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
    label_ys_positions = (10, height - 4)
    label_x_positions = (2, width - 22)
    if label_rect is not None:
        label_ys_positions = (
            min(max(label_rect.y0 + 10, label_rect.y0 + 6), label_rect.y1 - 1),
            min(max(label_rect.y1 - 4, label_rect.y0 + 6), label_rect.y1 - 1),
        )
        label_x_positions = (
            max(label_rect.x0 + 2, label_rect.x0),
            min(max(label_rect.x1 - 22, label_rect.x0), label_rect.x1 - 1),
        )
    for x in label_xs:
        for ly in label_ys_positions:
            pg.insert_text(
                (x + 1, ly), format(x, ".6g"), fontsize=6, color=GRID_COLOR
            )
    for y in label_ys:
        for lx in label_x_positions:
            pg.insert_text(
                (lx, y - 1), format(y, ".6g"), fontsize=6, color=GRID_COLOR
            )
