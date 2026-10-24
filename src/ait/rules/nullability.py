"""Nullability rule: unshielded dereference of a value that can be None."""

from __future__ import annotations

import ast
from typing import List, Set, Tuple

from ..context import RuleContext
from ..flow.data_flow import optional_sources
from ..models import CausationStep, Diagnostic, Severity
from ._util import collect_body, sorted_by_position, statement_ancestor
from .registry import rule


_ORDERING_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _risky_operands(node: ast.AST) -> Tuple[ast.AST, ...]:
    """Return operands that raise ``TypeError`` when they hold ``None``.

    Arithmetic and ordering comparisons touch the value; identity and equality
    comparisons do not, so ``x is None`` must never count as a dereference.
    """
    if isinstance(node, ast.BinOp):
        return node.left, node.right
    if isinstance(node, ast.Compare) and any(
        isinstance(operator, _ORDERING_OPS) for operator in node.ops
    ):
        return (node.left,) + tuple(node.comparators)
    return ()


def _deref_symbol(node: ast.AST) -> Tuple[str, bool]:
    """Return ``(symbol, is_attribute)`` for a dereferenced expression.

    Only expressions that actually touch the value qualify: attribute reads,
    subscript reads, calling a value as a function, and the operands of
    arithmetic or ordering comparisons. Passing a value around is not a
    dereference and must never be reported.
    """
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        base = node.value
        if isinstance(base, ast.Name):
            return base.id, False
        if (
            isinstance(base, ast.Attribute)
            and isinstance(base.value, ast.Name)
            and base.value.id == "self"
        ):
            return f"self.{base.attr}", True
        return "", False
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id, False
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        if isinstance(node.value, ast.Name):
            return node.value.id, False
    for operand in _risky_operands(node):
        if isinstance(operand, ast.Name):
            return operand.id, False
    return "", False


def _parameter_confidence(binding) -> float:
    """Grade confidence for a value that arrives as an optional parameter."""
    if binding.annotation:
        return 0.97
    return 0.96 if binding.default_is_none else 0.9


@rule(
    "AIT-N01",
    "Nullability",
    "Unshielded NoneType Dereference",
    Severity.DEFECT,
    "A value that can be None is dereferenced without a guard clause.",
)
def check_null_dereference(ctx: RuleContext) -> List[Diagnostic]:
    """Detect dereferences of provably optional values with no dominating guard.

    Confidence is graded by evidence strength: a declared ``Optional`` type is
    near-certain, while a value produced by ``dict.get`` is strong but not
    absolute. Guards are accepted only when they dominate the usage, so a guard
    that runs after the dereference still counts as a defect.
    """
    findings: List[Diagnostic] = []
    seen: Set[Tuple[str, str, int]] = set()
    for frame in ctx.scopes.functions():
        nodes, parents = collect_body(frame.node)
        for node in sorted_by_position(nodes):
            symbol, is_attribute = _deref_symbol(node)
            if not symbol or symbol in ("self", "cls", "super"):
                continue
            marker = (frame.qualname, symbol, node.lineno)
            if marker in seen:
                continue
            if is_attribute:
                attribute = symbol.split(".", 1)[1]
                owner = frame.owner_class
                info = ctx.scopes.attrs_of(owner).get(attribute) if owner else None
                if info is None or not info.optional:
                    continue
                confidence = 0.95 if info.annotation else 0.88
                steps: List[CausationStep] = []
                if ctx.guard_dominates(
                    frame, symbol, node.lineno, node=node, parents=parents
                ):
                    continue
            else:
                optional, steps = optional_sources(
                    ctx.scopes, ctx.path, frame, symbol, node.lineno
                )
                if not optional:
                    continue
                if ctx.guard_dominates(
                    frame, symbol, node.lineno, node=node, parents=parents
                ):
                    continue
                binding = ctx.scopes.resolve(symbol, frame, node.lineno)
                if binding is None:
                    continue
                confidence = _parameter_confidence(binding)
            statement = statement_ancestor(node, parents)
            patch = None
            if statement is not None:
                patch = ctx.insertion_patch(
                    statement,
                    [
                        f"if {symbol} is None:",
                        f'    raise RuntimeError(f"{symbol} is unexpectedly None")',
                    ],
                    "Fail fast with a guard clause instead of dereferencing None",
                )
            seen.add(marker)
            causation = list(steps)
            causation.append(
                ctx.step(
                    len(causation) + 1,
                    node,
                    f"`{symbol}` is dereferenced here without an `is None` guard",
                )
            )
            findings.append(
                ctx.diagnose(
                    rule_id="AIT-N01",
                    severity=Severity.DEFECT,
                    confidence=confidence,
                    message=(
                        f"`{symbol}` can be None at this point but is dereferenced "
                        "without a guard"
                    ),
                    node=node,
                    symbol=symbol,
                    evidence=[
                        ctx.evidence(
                            node,
                            f"Dereference of `{symbol}` occurs with no dominating None check",
                        ),
                        ctx.evidence(
                            frame.node,
                            f"`{frame.qualname}` contains no guard that protects `{symbol}` "
                            "before this line",
                        ),
                    ],
                    causation=causation,
                    witness=ctx.make_witness("AIT-N01", node, symbol=symbol),
                    patch=patch,
                )
            )
    return findings
