"""Hygiene rules: error suppression and control-flow nesting depth."""

from __future__ import annotations

import ast
from typing import List, Optional, Tuple

from ..context import RuleContext
from ..models import Diagnostic, Severity
from .registry import rule

_CONTROL_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Match,
    ast.comprehension,
)
_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_MAX_ALLOWED_DEPTH = 4


@rule(
    "AIT-H01",
    "Hygiene",
    "Bare Except Error Suppression",
    Severity.WARNING,
    "A bare except swallows every failure, including interrupts and system exits.",
)
def check_bare_except(ctx: RuleContext) -> List[Diagnostic]:
    """Detect ``except:`` handlers that suppress every exception type."""
    findings: List[Diagnostic] = []
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is not None:
            continue
        if _reraises_bare(node.body):
            # ``except: raise`` propagates the exception untouched, so nothing is
            # suppressed and reporting it would be a false positive.
            continue
        slows_control_flow = any(
            isinstance(statement, (ast.Continue, ast.Break, ast.Pass))
            for statement in node.body
        )
        findings.append(
            ctx.diagnose(
                rule_id="AIT-H01",
                severity=Severity.WARNING,
                confidence=0.99,
                message=(
                    "Bare `except:` catches every exception, including KeyboardInterrupt "
                    "and SystemExit, and hides the original cause"
                ),
                node=node,
                symbol="except",
                evidence=[
                    ctx.evidence(
                        node,
                        "ExceptHandler declares no exception type"
                        + (" and its body silently continues" if slows_control_flow else ""),
                    )
                ],
                causation=[
                    ctx.step(1, node, "Any raised exception is captured by this handler"),
                    ctx.step(2, node, "No type filter remains, so diagnosis information is lost"),
                ],
                witness=ctx.make_witness("AIT-H01", node, symbol="except"),
                patch=ctx.line_patch(
                    node.lineno,
                    "except:",
                    "except Exception:",
                    "Narrow the handler to Exception so interrupts still propagate",
                ),
            )
        )
    return findings


def _reraises_bare(body: List[ast.stmt]) -> bool:
    """Return True when the handler only re-raises the active exception."""
    return (
        len(body) == 1
        and isinstance(body[0], ast.Raise)
        and body[0].exc is None
    )


@rule(
    "AIT-H02",
    "Hygiene",
    "Nesting Hell (>4 Levels)",
    Severity.ADVICE,
    "Control flow is nested deeper than four levels, making branches hard to test.",
)
def check_deep_nesting(ctx: RuleContext) -> List[Diagnostic]:
    """Report the deepest control-flow nesting point per function."""
    findings: List[Diagnostic] = []
    for frame in ctx.scopes.functions():
        body = list(getattr(frame.node, "body", ()))
        deepest: Tuple[int, Optional[ast.AST]] = (0, None)
        for statement in body:
            if not isinstance(statement, ast.stmt):
                continue
            # A control statement in the body already puts its children one level
            # deep; seeding the walk at 1 keeps the reported depth equal to the
            # number of control statements on the path.
            candidate = _deepest(
                statement, 1 if isinstance(statement, _CONTROL_NODES) else 0
            )
            if candidate[0] > deepest[0]:
                deepest = candidate
        depth, node = deepest
        if node is None or depth <= _MAX_ALLOWED_DEPTH:
            continue
        findings.append(
            ctx.diagnose(
                rule_id="AIT-H02",
                severity=Severity.ADVICE,
                confidence=0.99,
                message=(
                    f"`{frame.qualname}` nests control flow {depth} levels deep; "
                    "extract branches into named functions"
                ),
                node=node,
                symbol=frame.qualname,
                evidence=[
                    ctx.evidence(
                        node,
                        f"Deepest control-flow statement sits at nesting level {depth}",
                    ),
                    ctx.evidence(
                        frame.node,
                        f"`{frame.qualname}` spans levels beyond the {_MAX_ALLOWED_DEPTH}-level budget",
                    ),
                ],
                causation=[
                    ctx.step(1, frame.node, "Function body starts at level zero"),
                    ctx.step(2, node, f"Control flow reaches level {depth} on this branch"),
                ],
                witness=ctx.make_witness("AIT-H02", node, symbol=frame.qualname),
            )
        )
    return findings


def _deepest(node: ast.AST, depth: int) -> Tuple[int, Optional[ast.AST]]:
    """Return the maximum control-flow depth and the node that reaches it."""
    best: Tuple[int, Optional[ast.AST]] = (depth, node)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPE_BOUNDARY):
            continue
        increment = 1 if isinstance(child, _CONTROL_NODES) else 0
        candidate = _deepest(child, depth + increment)
        if candidate[0] > best[0]:
            best = candidate
    return best
