"""AiT-test: high-assurance static invariant, causation and concurrency diagnostics.

The public surface is intentionally small so that both frontends (`cli.py` and
`mcp.py`) share a single, versioned contract. Importing this package must never
pull third-party code: the engine runs on the Python standard library alone.
"""

from __future__ import annotations

from .models import (
    ENGINE_NAME,
    ENGINE_VERSION,
    EXIT_CLEAN,
    EXIT_DEFECT,
    EXIT_INTERNAL,
    EXIT_INTERRUPT,
    EXIT_USAGE,
    CausationStep,
    Diagnostic,
    Evidence,
    Finding,
    Location,
    Patch,
    ScanResult,
    Severity,
    Witness,
    diagnostic_from_dict,
    diagnostic_to_dict,
    sort_diagnostics,
)

__version__ = ENGINE_VERSION

__all__ = [
    "ENGINE_NAME",
    "ENGINE_VERSION",
    "EXIT_CLEAN",
    "EXIT_DEFECT",
    "EXIT_INTERNAL",
    "EXIT_INTERRUPT",
    "EXIT_USAGE",
    "CausationStep",
    "Diagnostic",
    "Evidence",
    "Finding",
    "Location",
    "Patch",
    "ScanResult",
    "Severity",
    "Witness",
    "__version__",
    "diagnostic_from_dict",
    "diagnostic_to_dict",
    "sort_diagnostics",
]
