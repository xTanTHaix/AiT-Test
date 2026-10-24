"""Root launcher for the MCP stdio bridge (`python mcp.py`)."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ait.mcp import main  # noqa: E402  (import must follow the sys.path anchor)

if __name__ == "__main__":
    raise SystemExit(main())
