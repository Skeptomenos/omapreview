"""Optional MCP entry point with useful setup guidance even in base installs."""
import sys


def main():
    try:
        import mcp  # noqa: F401
    except ImportError:
        print('omapreview: optional MCP dependency is missing. For a user install run: '
              '~/.local/share/omapreview/venv/bin/python -m pip install "mcp>=1.2". '
              'For a checkout use .venv/bin/pip install -e ".[mcp]". '
              'For Arch install python-mcp. Then run omapreview mcp.', file=sys.stderr)
        raise SystemExit(1)
    from .mcp_server import main as serve
    serve()
