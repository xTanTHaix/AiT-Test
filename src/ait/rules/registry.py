"""Rule registry and ruleset fingerprinting.

The fingerprint is part of the cache key: editing a rule must invalidate cached
findings, otherwise a user who upgrades the catalog keeps seeing verdicts from
the previous ruleset.
"""

from __future__ import annotations

import hashlib
import types
from dataclasses import dataclass
from typing import Callable, Dict, Sequence, Tuple

from ..context import RuleContext
from ..models import ENGINE_VERSION, Diagnostic, Severity

CheckFunction = Callable[[RuleContext], Sequence[Diagnostic]]


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """Declarative identity and detector for one rule."""

    rule_id: str
    category: str
    title: str
    default_severity: Severity
    description: str
    check: CheckFunction


_REGISTRY: Dict[str, RuleSpec] = {}


def rule(
    rule_id: str,
    category: str,
    title: str,
    severity: Severity,
    description: str,
) -> Callable[[CheckFunction], CheckFunction]:
    """Register a detector function under a stable rule id."""

    def decorator(function: CheckFunction) -> CheckFunction:
        if rule_id in _REGISTRY:
            raise ValueError(f"Duplicate rule registration: {rule_id}")
        _REGISTRY[rule_id] = RuleSpec(
            rule_id=rule_id,
            category=category,
            title=title,
            default_severity=severity,
            description=description,
            check=function,
        )
        return function

    return decorator


def all_rules() -> Tuple[RuleSpec, ...]:
    """Return every registered rule ordered by id."""
    return tuple(sorted(_REGISTRY.values(), key=lambda spec: spec.rule_id))


def _code_digest(check: CheckFunction) -> str:
    """Hash a detector's bytecode so editing a rule invalidates cached verdicts."""
    code = getattr(check, "__code__", None)
    if not isinstance(code, types.CodeType):
        return "0"
    return hashlib.blake2b(_code_material(code), digest_size=8).hexdigest()


def _code_material(code: types.CodeType) -> bytes:
    """Render one code object (including nested code) as deterministic bytes."""
    parts: list[bytes] = [code.co_code]
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            parts.append(_code_material(constant))
        else:
            parts.append(repr(constant).encode("utf-8", "replace"))
    return b"\x00".join(parts)


def ruleset_fingerprint() -> str:
    """Return a stable hash of the active catalog, its detectors and the engine."""
    material = "\n".join(
        f"{spec.rule_id}|{spec.title}|{spec.default_severity.value}|"
        f"{ENGINE_VERSION}|{_code_digest(spec.check)}"
        for spec in all_rules()
    )
    return hashlib.blake2b(material.encode("utf-8"), digest_size=12).hexdigest()
