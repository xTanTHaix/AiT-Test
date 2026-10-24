"""Shared fixtures and helpers for the AiT-test suite.

The suite uses ``unittest`` only. ``conftest.py`` exists for pytest users and
does nothing beyond anchoring ``sys.path``; importing this module does the same
job for ``python -m unittest``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ait.engine import Engine  # noqa: E402  (import follows the path anchor)
from ait.models import Diagnostic  # noqa: E402

BAD_FIXTURES = (
    "bad_process.py",
    "bad_race.py",
    "bad_async.py",
    "bad_mutation.py",
    "bad_null.py",
    "bad_hygiene.py",
)

CLEAN_FIXTURES = (
    "clean_process.py",
    "clean_race.py",
    "clean_async.py",
    "clean_mutation.py",
    "clean_null.py",
    "clean_hygiene.py",
)


def analyze(source: str, name: str = "sample.py") -> List[Diagnostic]:
    """Analyse source text with a throwaway engine (no cache, no filesystem)."""
    engine = Engine(use_cache=False)
    try:
        return engine.analyze_source(name, source)
    finally:
        engine.close()


def analyze_fixture(filename: str) -> List[Diagnostic]:
    """Analyse one fixture file by name."""
    engine = Engine(use_cache=False)
    try:
        result = engine.scan(FIXTURES / filename, use_cache=False)
    finally:
        engine.close()
    return result.findings


def rule_ids(findings: Iterable[Diagnostic]) -> Set[str]:
    """Return the set of rule ids present in ``findings``."""
    return {item.rule_id for item in findings}


def ids_in_order(findings: Sequence[Diagnostic]) -> List[str]:
    """Return rule ids in the order the engine sorted them."""
    return [item.rule_id for item in findings]


def fixture_text(filename: str) -> str:
    """Return the exact UTF-8 text of a fixture file."""
    return (FIXTURES / filename).read_text(encoding="utf-8")
