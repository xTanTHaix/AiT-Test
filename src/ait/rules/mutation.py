"""Mutation rules: hidden in-place parameter mutation and shared mutable defaults."""

from __future__ import annotations

import ast
from typing import Dict, List, Optional, Set

from ..context import RuleContext
from ..models import Diagnostic, Severity
from ._util import collect_body, is_docstring_statement, sorted_by_position
from .registry import rule

_MUTATING_METHODS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "remove",
        "pop",
        "clear",
        "sort",
        "reverse",
        "update",
        "setdefault",
        "popitem",
        "add",
        "discard",
        "difference_update",
        "intersection_update",
        "symmetric_difference_update",
    }
)
_COPY_CONSTRUCTORS: Dict[str, str] = {
    "list": "list",
    "List": "list",
    "MutableSequence": "list",
    "dict": "dict",
    "Dict": "dict",
    "MutableMapping": "dict",
    "set": "set",
    "Set": "set",
    "MutableSet": "set",
}


def _first_body_statement(node: ast.AST) -> Optional[ast.stmt]:
    """Return the first non-docstring statement of a function body."""
    body = list(getattr(node, "body", ()))
    for statement in body:
        if is_docstring_statement(statement):
            continue
        if isinstance(statement, ast.stmt):
            return statement
    return None


@rule(
    "AIT-M01",
    "Mutation",
    "Direct Parameter In-Place Mutation",
    Severity.DEFECT,
    "A parameter container is mutated in place, silently changing caller-owned state.",
)
def check_parameter_mutation(ctx: RuleContext) -> List[Diagnostic]:
    """Detect in-place mutation of a caller-owned parameter container."""
    findings: List[Diagnostic] = []
    for frame in ctx.scopes.functions():
        parameters = {
            binding.name: binding
            for binding in frame.parameters()
            if binding.kind in ("param", "kwonly") and binding.name not in ("self", "cls")
        }
        if not parameters:
            continue
        nodes, _parents = collect_body(frame.node)
        rebound = {
            name: [
                binding.lineno
                for binding in frame.bindings.get(name, ())
                if binding.kind in ("assign", "loop", "with", "except")
            ]
            for name in parameters
        }
        reported: Set[str] = set()
        for node in sorted_by_position(nodes):
            name = ""
            method = ""
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
            ):
                name = node.func.value.id
                method = node.func.attr
                if method not in _MUTATING_METHODS:
                    continue
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
            ):
                name = node.value.id
                method = "__setitem__"
            if name not in parameters or name in reported:
                continue
            if any(line <= node.lineno for line in rebound.get(name, ())):
                continue
            reported.add(name)
            binding = parameters[name]
            patch = _copy_patch(ctx, frame, binding)
            findings.append(
                ctx.diagnose(
                    rule_id="AIT-M01",
                    severity=Severity.DEFECT,
                    confidence=0.95 if method != "__setitem__" else 0.85,
                    message=(
                        f"Parameter `{name}` is mutated in place via `{method}`, so the "
                        "caller's object changes underneath it"
                    ),
                    node=node,
                    symbol=name,
                    evidence=[
                        ctx.evidence(
                            node,
                            f"`{name}.{method}()` mutates the caller-owned container",
                        ),
                        ctx.evidence(
                            binding.node,
                            f"`{name}` is a parameter of `{frame.qualname}` and is never rebound locally",
                        ),
                    ],
                    causation=[
                        ctx.step(1, binding.node, f"`{name}` is received by reference from the caller"),
                        ctx.step(2, node, "In-place mutation writes through that shared reference"),
                    ],
                    witness=ctx.make_witness("AIT-M01", node, symbol=name),
                    patch=patch,
                )
            )
    return findings


def _copy_patch(ctx: RuleContext, frame, binding) -> Optional[object]:
    """Offer a defensive-copy insertion when the annotation names a known container."""
    constructor = _COPY_CONSTRUCTORS.get(binding.annotation.strip())
    if constructor is None:
        return None
    target = _first_body_statement(frame.node)
    if target is None:
        return None
    return ctx.insertion_patch(
        target,
        [f"{binding.name} = {constructor}({binding.name})"],
        "Copy the parameter so the caller's container is not modified",
    )


@rule(
    "AIT-M02",
    "Mutation",
    "Mutable Default Argument Trap",
    Severity.DEFECT,
    "A container default is created once at definition time and shared by every call.",
)
def check_mutable_default(ctx: RuleContext) -> List[Diagnostic]:
    """Detect list/dict/set defaults that persist state across calls."""
    findings: List[Diagnostic] = []
    for frame in ctx.scopes.functions():
        for binding in frame.parameters():
            if not binding.mutable_default or binding.value is None:
                continue
            kind = type(binding.value).__name__
            if isinstance(binding.value, ast.Call):
                kind = f"{ast.unparse(binding.value.func)}()"
            findings.append(
                ctx.diagnose(
                    rule_id="AIT-M02",
                    severity=Severity.DEFECT,
                    confidence=0.99,
                    message=(
                        f"Mutable default `{kind}` for `{binding.name}` is created once and "
                        "shared across every call"
                    ),
                    node=binding.value,
                    symbol=binding.name,
                    evidence=[
                        ctx.evidence(
                            binding.value,
                            f"Default value of `{binding.name}` is a mutable {kind}",
                        ),
                        ctx.evidence(
                            frame.node,
                            f"`{frame.qualname}` builds the default once at function definition",
                        ),
                    ],
                    causation=[
                        ctx.step(
                            1,
                            binding.value,
                            "Default object is instantiated when the def statement executes",
                        ),
                        ctx.step(
                            2,
                            frame.node,
                            "Every later call reuses that same object, so state leaks between calls",
                        ),
                    ],
                    witness=ctx.make_witness("AIT-M02", binding.value, symbol=binding.name),
                )
            )
    return findings
