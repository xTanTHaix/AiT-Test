"""Specialized diagnostic rule catalog.

Importing this package registers every detector. Ordering is by rule id so the
catalog fingerprint, progress markers and terminal output stay deterministic.
"""

from __future__ import annotations

from . import async_traps, hygiene, mutation, nullability, process_safety, race_hazards  # noqa: F401
from .registry import RuleSpec, all_rules, rule, ruleset_fingerprint

__all__ = [
    "RuleSpec",
    "all_rules",
    "async_traps",
    "hygiene",
    "mutation",
    "nullability",
    "process_safety",
    "race_hazards",
    "rule",
    "ruleset_fingerprint",
]
