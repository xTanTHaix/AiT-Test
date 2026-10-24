"""Root launcher so `python cli.py <path>` works straight from a fresh clone."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ait.cli import main  # noqa: E402  (import must follow the sys.path anchor)

if __name__ == "__main__":
    raise SystemExit(main())
