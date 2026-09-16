"""omepreview MCP server — the agent's native doorway.

Every tool here is a thin wrapper over the same op engine the CLI and GUI
use; nothing is agent-only or human-only. Run with `omepreview-mcp` (stdio), or
register with Claude Code:

    claude mcp add omepreview -- omepreview-mcp

Safety posture: consequential tools default to a dry run that returns the
resolved edit for confirmation. Pass the explicit confirmation flag (or
``dry_run=false`` for the generic tool) after approval to write it.
"""

from __future__ import annotations

import base64
import json
from functools import wraps

import pymupdf
from pydantic import StrictBool, StrictFloat, StrictInt

try:
    from mcp.types import CallToolResult, ImageContent, TextContent
except ImportError:  # MCP SDK v2 also exposes these through mcp_types.
    from mcp_types import CallToolResult, ImageContent, TextContent

try:  # MCP SDK v2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # SDK v1
    from mcp.server.fastmcp import FastMCP as _Server

try:  # MCP SDK v2
    from mcp.server.mcpserver.exceptions import ToolError as _ToolError
except ImportError:  # MCP SDK v1
    from mcp.server.fastmcp.exceptions import ToolError as _ToolError

from . import engine, pages as pages_mod, read, render, signature
from .ops import OpError, operation_catalog

mcp = _Server("omepreview")
StrictNumber = StrictInt | StrictFloat

_EXPECTED_TOOL_ERRORS = (
    OpError,
    FileNotFoundError,
    PermissionError,
    IsADirectoryError,
    ValueError,
)
if hasattr(pymupdf, "FileDataError"):
    _EXPECTED_TOOL_ERRORS += (pymupdf.FileDataError,)


def _tool_boundary(fn):
    """Expose anticipated user/input failures without leaking unexpected crashes."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _EXPECTED_TOOL_ERRORS as exc:
            raise _ToolError(str(exc)) from exc
        except _ToolError:
            raise
        except Exception:
            raise _ToolError(f"Error executing tool {fn.__name__}") from None

    return wrapped


def _tool(*tool_args, **tool_kwargs):
    """Register a tool with the same schema and an actionable error boundary."""
    def register(fn):
        mcp.tool(*tool_args, **tool_kwargs)(_tool_boundary(fn))
        # Keep direct Python callers on the established function contract.
        # Only calls crossing the MCP server boundary need ToolError mapping.
        return fn

    return register


def _supports_structured_result() -> bool:
    fields = getattr(CallToolResult, "model_fields", None)
    if fields is None:
        fields = getattr(CallToolResult, "__fields__", {})
    return "structured_content" in fields or "structuredContent" in fields


def _strict_bool(value: bool, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean, got {value!r}")
    return value


def _apply_guarded(
    path: str,
    ops: list[dict],
    *,
    output: str | None,
    confirm: bool,
    operation: str,
) -> dict:
    """Apply one dedicated mutation with the shared explicit-confirmation policy."""
    confirm = _strict_bool(confirm, "confirm")
    result = engine.apply(path, ops, output=output, dry_run=not confirm)
    if not confirm:
        result["needs_confirmation"] = (
            f"Dry run only. Re-call with confirm=true after the user approves "
            f"this {operation}."
        )
    return result


@_tool()
def operation_schema() -> dict:
    """Return the complete versioned catalog for all supported operations.

    Call this before constructing ``apply_ops`` payloads. It lists required
    fields, variants, defaults and examples from the same operation vocabulary
    used by the engine and GUI.
    """
    return operation_catalog()


@_tool()
def render_page(
    path: str,
    page: StrictInt = 1,
    scale: StrictNumber = 2.0,
    grid: StrictNumber | None = None,
    clip: list[StrictNumber] | None = None,
) -> CallToolResult:
    """Render a PDF page as a PNG image with source-bound geometry metadata.

    ``page`` is 1-based. ``clip`` is [x0,y0,x1,y1] in unrotated CropBox-local
    PDF points, the same coordinate system used by ``read_pdf`` and edits.
    The result contains an actual image content block, a JSON metadata text
    block and structured metadata. Rendering is read-only.
    """
    png, metadata = render.render_page_bytes(
        path, page=page, scale=scale, grid=grid, clip=clip
    )
    encoded = base64.b64encode(png)
    if len(encoded) > render.MAX_RENDER_BASE64_BYTES:
        raise ValueError(
            f"base64 render response is {len(encoded):,} bytes; limit is "
            f"{render.MAX_RENDER_BASE64_BYTES:,}"
        )
    image = ImageContent(
        type="image", data=encoded.decode("ascii"), mimeType="image/png"
    )
    metadata_text = TextContent(
        type="text", text=json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    )
    content = [metadata_text, image]
    if _supports_structured_result():
        return CallToolResult(content=content, structuredContent=metadata)
    # FastMCP 1.2 accepts content blocks and lists, but serializes a returned
    # CallToolResult as one text block. Keep the actual image visible there.
    return content


@_tool()
def read_pdf(path: str, pages: list[int] | None = None, text_only: StrictBool = False) -> dict:
    """Read a PDF's structure: pages, text blocks with bounding boxes, form
    fields, and annotations. Coordinates are PDF points, origin top-left —
    the same system every other omepreview tool accepts."""
    return read.extract(path, pages=pages, text_only=text_only)


@_tool()
def list_form_fields(path: str) -> list[dict]:
    """List every form field (name, type, current value, page, rect)."""
    return read.form_fields(path)


@_tool()
def apply_ops(path: str, ops: list[dict], output: str | None = None, dry_run: StrictBool = True) -> dict:
    """Apply a list of operations in one atomic edit. The default is a dry run
    so a batch containing a consequential operation cannot write without an
    explicit ``dry_run=false``. See omepreview's ops spec. Prefer this over
    many single calls when making several edits."""
    dry_run = _strict_bool(dry_run, "dry_run")
    result = engine.apply(path, ops, output=output, dry_run=dry_run)
    if dry_run:
        result["needs_confirmation"] = (
            "Dry run only. Re-call with dry_run=false after the user approves."
        )
    return result


@_tool()
def highlight(
    path: str,
    page: int,
    match: str,
    style: str = "highlight",
    output: str | None = None,
) -> dict:
    """Highlight (or underline/strikeout/squiggly) every occurrence of
    `match` on `page`."""
    return engine.apply(
        path, [{"op": "highlight", "page": page, "match": match, "style": style}], output=output
    )


@_tool()
def add_note(
    path: str,
    page: int,
    x: float,
    y: float,
    text: str,
    output: str | None = None,
) -> dict:
    """Attach a sticky-note comment at (x, y) on `page`."""
    return engine.apply(
        path, [{"op": "note", "page": page, "at": [x, y], "text": text}], output=output
    )


@_tool()
def fill_field(
    path: str,
    field: str,
    value: str,
    output: str | None = None,
) -> dict:
    """Fill one form field by name. Errors list the document's real field
    names if the name doesn't match."""
    return engine.apply(
        path, [{"op": "fill_field", "field": field, "value": value}], output=output
    )


@_tool()
def place_signature(
    path: str,
    page: int,
    x: float,
    y: float,
    width: float = 180,
    signature_name: str = "default",
    date: StrictBool = False,
    confirmed: StrictBool = False,
    output: str | None = None,
) -> dict:
    """Place the user's saved signature with its top-left corner at (x, y).

    By default this is a DRY RUN returning the resolved placement rect —
    show it to the user (or open the PDF) and call again with confirmed=true
    once approved. Signing a document is consequential: never set
    confirmed=true without the user's go-ahead."""
    confirmed = _strict_bool(confirmed, "confirmed")
    date = _strict_bool(date, "date")
    op = {
        "op": "place_signature",
        "page": page,
        "at": [x, y],
        "width": width,
        "signature": signature_name,
        "date": date,
    }
    result = engine.apply(path, [op], output=output, dry_run=not confirmed)
    if not confirmed:
        result["needs_confirmation"] = (
            "Dry run only. Re-call with confirmed=true after the user approves this placement."
        )
    return result


@_tool()
def list_signatures() -> list[str]:
    """Names of the user's saved signatures (SVG in ~/Downloads/omapreview/signature/; also reads ~/.config/omepreview/signatures/)."""
    return signature.list_names()


@_tool()
def list_pages(path: str) -> dict:
    """List every page in a PDF with number, size, and rotation."""
    return pages_mod.list_pages(path)


@_tool()
def delete_pages(
    path: str,
    pages: list[int],
    output: str | None = None,
    confirm: StrictBool = False,
) -> dict:
    """Delete pages by 1-based number. Defaults to dry-run — pass confirm=true
    after the user approves."""
    confirm = _strict_bool(confirm, "confirm")
    result = engine.apply(
        path,
        [{"op": "delete_pages", "pages": pages}],
        output=output,
        dry_run=not confirm,
    )
    if not confirm:
        result["needs_confirmation"] = (
            "Dry run only. Re-call with confirm=true after the user approves."
        )
    return result


@_tool()
def rotate_pages(
    path: str,
    pages: list[int],
    degrees: int,
    output: str | None = None,
    dry_run: StrictBool = False,
) -> dict:
    """Rotate pages by 90, 180, 270, or -90 degrees."""
    dry_run = _strict_bool(dry_run, "dry_run")
    return engine.apply(
        path,
        [{"op": "rotate_pages", "pages": pages, "degrees": degrees}],
        output=output,
        dry_run=dry_run,
    )


@_tool()
def move_pages(
    path: str,
    pages: list[int],
    after: int,
    output: str | None = None,
    dry_run: StrictBool = False,
) -> dict:
    """Reorder pages: move `pages` to after page `after` (0 = beginning)."""
    dry_run = _strict_bool(dry_run, "dry_run")
    return engine.apply(
        path,
        [{"op": "move_pages", "pages": pages, "after": after}],
        output=output,
        dry_run=dry_run,
    )


@_tool()
def insert_pages(
    path: str,
    after: int,
    output: str | None = None,
    source: str | None = None,
    source_pages: list[int] | None = None,
    blank_count: int | None = None,
    blank_width: float = 595,
    blank_height: float = 842,
    image: str | None = None,
    dry_run: StrictBool = False,
) -> dict:
    """Insert pages after `after` from a source PDF, blank sheet(s), or image."""
    dry_run = _strict_bool(dry_run, "dry_run")
    op: dict = {"op": "insert_pages", "after": after}
    if source is not None:
        op["source"] = source
        if source_pages is not None:
            op["source_pages"] = source_pages
    elif image is not None:
        op["image"] = image
    elif blank_count is not None:
        op["blank"] = {
            "count": blank_count,
            "width": blank_width,
            "height": blank_height,
        }
    else:
        raise ValueError("insert_pages needs source, image, or blank_count")
    return engine.apply(path, [op], output=output, dry_run=dry_run)


@_tool()
def extract_pages(
    path: str,
    pages: list[int],
    to: str,
    dry_run: StrictBool = False,
) -> dict:
    """Write selected pages to a new PDF at `to`. The source file is unchanged."""
    dry_run = _strict_bool(dry_run, "dry_run")
    return engine.apply(
        path,
        [{"op": "extract_pages", "pages": pages, "to": to}],
        dry_run=dry_run,
    )


@_tool()
def delete_annotation(
    path: str,
    page: int,
    index: int,
    output: str | None = None,
    confirm: StrictBool = False,
) -> dict:
    """Delete one annotation on `page` by 0-based `index` (see omepreview read).

    Defaults to dry-run — pass confirm=true after the user approves."""
    confirm = _strict_bool(confirm, "confirm")
    result = engine.apply(
        path,
        [{"op": "delete_annotation", "page": page, "index": index}],
        output=output,
        dry_run=not confirm,
    )
    if not confirm:
        result["needs_confirmation"] = (
            "Dry run only. Re-call with confirm=true after the user approves."
        )
    return result


@_tool()
def redact(
    path: str,
    page: int,
    match: str | None = None,
    rect: list[float] | None = None,
    fill: list[float] | None = None,
    output: str | None = None,
    confirm: StrictBool = False,
) -> dict:
    """Permanently remove text or image pixels in a region on `page`.

    Provide exactly one of `match` (text search) or `rect` ([x0,y0,x1,y1]).
    Defaults to dry-run — pass confirm=true after the user approves."""
    confirm = _strict_bool(confirm, "confirm")
    op: dict = {"op": "redact", "page": page}
    if match is not None:
        op["match"] = match
    elif rect is not None:
        op["rect"] = rect
    else:
        raise ValueError("redact needs match or rect")
    if fill is not None:
        op["fill"] = fill
    result = engine.apply(path, [op], output=output, dry_run=not confirm)
    if not confirm:
        result["needs_confirmation"] = (
            "Dry run only. Re-call with confirm=true after the user approves."
        )
    return result


@_tool()
def crop_pages(
    path: str,
    pages: list[int],
    rect: list[float],
    output: str | None = None,
    dry_run: StrictBool = False,
) -> dict:
    """Set the PDF CropBox for ``pages`` to ``rect`` ([x0, y0, x1, y1] in the
    page's current coordinate space, top-left origin). Does not trim content —
    it changes the visible page box."""
    dry_run = _strict_bool(dry_run, "dry_run")
    return engine.apply(
        path,
        [{"op": "crop_pages", "pages": pages, "rect": rect}],
        output=output,
        dry_run=dry_run,
    )


@_tool()
def add_shape(
    path: str,
    page: int,
    shape: str,
    from_point: list[float] | None = None,
    to_point: list[float] | None = None,
    rect: list[float] | None = None,
    color: list[float] | None = None,
    width: float = 2.0,
    output: str | None = None,
) -> dict:
    """Add a Line, Square, or Circle PDF annotation on `page`.

    `shape` is one of line, arrow, rect, oval. Line and arrow need
    `from_point` and `to_point` ([x, y]); rect and oval need `rect`
    ([x0, y0, x1, y1]). Coordinates are PDF points, top-left origin."""
    op: dict = {"op": "shape", "page": page, "shape": shape, "width": width}
    if shape in ("line", "arrow"):
        if not from_point or not to_point:
            raise ValueError("line and arrow need from_point and to_point")
        op["from"] = from_point
        op["to"] = to_point
    elif rect:
        op["rect"] = rect
    else:
        raise ValueError("rect and oval need rect")
    if color:
        op["color"] = color
    return engine.apply(path, [op], output=output)


@_tool()
def flatten_pdf(
    path: str,
    output: str | None = None,
    confirm: StrictBool = False,
) -> dict:
    """Bake all annotations and form fields into page content (irreversible
    in the output file; the input is preserved when `output` is given).

    Refuses when pending PDF redaction annotations exist — baking their
    appearance does not remove the underlying text. Apply a reviewed redact
    or delete those annotations first. Defaults to dry-run — pass confirm=true
    after the user approves.
    """
    confirm = _strict_bool(confirm, "confirm")
    result = engine.flatten(path, output=output, dry_run=not confirm)
    if not confirm:
        result["needs_confirmation"] = (
            "Dry run only. Re-call with confirm=true after the user approves flattening."
        )
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
