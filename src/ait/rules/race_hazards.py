"""Concurrency rules: unprotected shared-state mutation and double-checked locking."""

from __future__ import annotations

import ast
from typing import Dict, List, Set, Tuple

from ..context import RuleContext
from ..models import CausationStep, Diagnostic, Severity
from ._util import collect_body, sorted_by_position, statement_ancestor
from .registry import rule

_RACY_WRITE_KINDS = frozenset({"augassign", "mutate", "itemwrite"})

_OP_HINT = {
    "augassign": "LOAD_ATTR -> INPLACE_ADD -> STORE_ATTR",
    "assign": "LOAD_ATTR -> ADD -> STORE_ATTR",
    "itemwrite": "LOAD_ATTR -> STORE_SUBSCR",
}
_DEFAULT_OP_HINT = "LOAD_ATTR -> CALL -> MUTATE"


def _is_read_modify_write(write) -> bool:
    """Return True when one write both reads and stores the same symbol.

    ``assign`` is only racy in its read-modify-write form (``x = x + 1``); a
    plain ``x = value`` has nothing to lose.
    """
    if write.kind in _RACY_WRITE_KINDS:
        return True
    return write.kind == "assign" and write.reads_target


def _lock_suffix(class_qualname: str, lock_name: str) -> str:
    """Render the attribute path used to acquire a class lock."""
    return f"self.{lock_name}" if class_qualname else lock_name


@rule(
    "AIT-R01",
    "Concurrency",
    "Unprotected Shared State Race",
    Severity.DEFECT,
    "Shared state is read-modify-written on a thread-reachable path without holding a lock.",
)
def check_shared_state_race(ctx: RuleContext) -> List[Diagnostic]:
    """Detect unlocked read-modify-write operations reachable from a spawn site.

    Severity is evidence-driven: a DEFECT requires a demonstrable spawn path into
    the mutating function. Without that path a finding is only worth reporting
    when the module shows *some* concurrency intent (a spawn site anywhere, an
    ``async def``, or a lock declared on the class). A plain single-threaded
    class that happens to use ``+=`` is not a race, and reporting it would bury
    the real ones.
    """
    findings: List[Diagnostic] = []
    seen: Set[Tuple[str, str]] = set()
    thread_hint = _has_concurrency_hint(ctx)
    for frame in ctx.scopes.frames:
        if frame.kind != "class":
            continue
        attributes = ctx.scopes.attrs_of(frame.qualname)
        lock_attributes = set(ctx.scopes.locks_in(frame.qualname))
        for child in frame.children:
            if child.kind != "function":
                continue
            info = ctx.graph.functions.get(child.qualname)
            if info is None:
                continue
            for write in info.writes:
                if not write.is_self_attr:
                    continue
                if not _is_read_modify_write(write):
                    continue
                if write.attr in lock_attributes or write.locked_by:
                    continue
                if write.attr not in attributes:
                    continue
                if not (
                    ctx.graph.is_concurrent(info.qualname)
                    or thread_hint
                    or lock_attributes
                ):
                    continue
                marker = (info.qualname, write.attr)
                if marker in seen:
                    continue
                seen.add(marker)
                findings.append(
                    _class_race_finding(ctx, info, write, lock_attributes)
                )
    for info in ctx.graph.functions.values():
        for write in info.writes:
            if not write.is_global:
                continue
            if not _is_read_modify_write(write) or write.locked_by:
                continue
            if not (ctx.graph.is_concurrent(info.qualname) or thread_hint):
                continue
            marker = (info.qualname, write.attr)
            if marker in seen:
                continue
            seen.add(marker)
            findings.append(_global_race_finding(ctx, info, write))
    return findings


def _has_concurrency_hint(ctx: RuleContext) -> bool:
    """Return True when the module shows any evidence of concurrent execution."""
    if any(ctx.graph.spawn_sites.values()):
        return True
    return any(frame.is_async for frame in ctx.scopes.functions())


def _class_race_finding(
    ctx: RuleContext,
    info,
    write,
    lock_attributes: Set[str],
) -> Diagnostic:
    """Build a race diagnostic for a class attribute, reusing one shared shape."""
    sites = ctx.graph.spawn_sites_for(info.qualname)
    concurrent = ctx.graph.is_concurrent(info.qualname)
    lock_mention = (
        f"the class already declares `{sorted(lock_attributes)[0]}`"
        if lock_attributes
        else "the class holds no lock attribute at all"
    )
    confidence = 0.96 if sites else (0.9 if concurrent else 0.6)
    severity = Severity.DEFECT if concurrent else Severity.WARNING
    causation: List[CausationStep] = []
    index = 1
    for site in sites:
        causation.append(
            ctx.step(
                index,
                site.node,
                f"`{info.qualname}` is started concurrently via `{site.callee_text}`",
            )
        )
        index += 1
    if not sites:
        causation.append(
            ctx.step(
                index,
                info.node,
                f"`{info.qualname}` is reachable from a spawn site through the call graph",
            )
        )
        index += 1
    causation.append(
        ctx.step(
            index,
            write.node or info.node,
            f"`self.{write.attr}` is read-modify-written outside any lock region",
        )
    )
    index += 1
    causation.append(
        ctx.step(
            index,
            info.node,
            f"No lock protects this statement ({lock_mention}); interleaved "
            f"{_OP_HINT.get(write.kind, _DEFAULT_OP_HINT)} loses updates",
        )
    )
    patch = None
    if lock_attributes and write.node is not None:
        _nodes, parents = collect_body(info.node)
        statement = statement_ancestor(write.node, parents)
        if statement is not None:
            patch = _lock_wrap_patch(ctx, statement, info, sorted(lock_attributes)[0])
    return ctx.diagnose(
        rule_id="AIT-R01",
        severity=severity,
        confidence=confidence,
        message=(
            f"`self.{write.attr}` is mutated without synchronisation on a "
            f"{'concurrent' if concurrent else 'possibly concurrent'} path"
        ),
        node=write.node or info.node,
        function=info.signature_label(),
        symbol=f"self.{write.attr}",
        evidence=[
            ctx.evidence(
                write.node or info.node,
                f"`{info.qualname}` mutates `self.{write.attr}` via "
                f"`{write.kind}` outside any lock ({lock_mention})",
            )
        ]
        + (
            [ctx.evidence(site.node, f"Spawn site: `{site.callee_text}` at line {site.lineno}")]
            if sites
            else []
        ),
        causation=causation,
        witness=ctx.make_witness(
            "AIT-R01",
            write.node or info.node,
            symbol=write.attr,
            detail={
                "class": info.owner_class.split(".")[-1],
                "attr": write.attr,
                "lock": sorted(lock_attributes)[0] if lock_attributes else "lock",
            },
        ),
        patch=patch,
    )


def _lock_wrap_patch(ctx: RuleContext, statement: ast.stmt, info, lock_name: str):
    """Wrap one statement in the class lock, preserving its own indentation."""
    indent = ctx.indent_at(statement.lineno)
    raw = ctx.statement_text(statement).rstrip("\r\n")
    body_lines = []
    for line in raw.split(ctx.newline):
        if not line.strip():
            body_lines.append(line)
            continue
        stripped = line[len(indent) :] if line.startswith(indent) else line.lstrip()
        body_lines.append(f"{indent}    {stripped}")
    body = ctx.newline.join(body_lines)
    replacement = (
        f"{indent}with {_lock_suffix(info.owner_class, lock_name)}:"
        f"{ctx.newline}{body}{ctx.newline}"
    )
    return ctx.statement_patch(
        statement,
        replacement,
        "Acquire the existing lock around the read-modify-write",
    )


def _global_race_finding(ctx: RuleContext, info, write) -> Diagnostic:
    """Build a race diagnostic for a module-level global mutated from a function."""
    sites = ctx.graph.spawn_sites_for(info.qualname)
    concurrent = ctx.graph.is_concurrent(info.qualname)
    severity = Severity.DEFECT if concurrent else Severity.WARNING
    confidence = 0.94 if sites else (0.88 if concurrent else 0.6)
    causation: List[CausationStep] = []
    index = 1
    for site in sites:
        causation.append(
            ctx.step(index, site.node, f"`{info.qualname}` runs via `{site.callee_text}`")
        )
        index += 1
    causation.append(
        ctx.step(
            index,
            write.node or info.node,
            f"Global `{write.attr}` is mutated through a `global` declaration with no lock",
        )
    )
    return ctx.diagnose(
        rule_id="AIT-R01",
        severity=severity,
        confidence=confidence,
        message=f"Module global `{write.attr}` is mutated without synchronisation",
        node=write.node or info.node,
        function=info.signature_label(),
        symbol=write.attr,
        evidence=[
            ctx.evidence(
                write.node or info.node,
                f"`{info.qualname}` mutates the module global "
                f"`{write.attr}` via `{write.kind}`",
            )
        ],
        causation=causation,
        witness=ctx.make_witness(
            "AIT-R01",
            write.node or info.node,
            symbol=write.attr,
            detail={"class": "SharedState", "attr": write.attr, "lock": "lock"},
        ),
    )


def _is_lock_with(ctx: RuleContext, node: ast.With, owner: str) -> bool:
    """Return True when any context manager of ``node`` is a lock."""
    return any(
        ctx.graph.is_lock_expression(item.context_expr, owner) for item in node.items
    )


def _nested_conditions(node: ast.With) -> List[ast.If]:
    """Return every ``if`` anywhere inside one ``with`` block."""
    return [child for child in ast.walk(node) if isinstance(child, ast.If)]


@rule(
    "AIT-R02",
    "Concurrency",
    "Double-Check Lock Anti-Pattern",
    Severity.WARNING,
    "State is tested outside a lock and retested inside it without atomics.",
)
def check_double_checked_lock(ctx: RuleContext) -> List[Diagnostic]:
    """Detect a condition tested both before and inside the same lock region."""
    findings: List[Diagnostic] = []
    for qualname, info in sorted(ctx.graph.functions.items()):
        nodes, parents = collect_body(info.node)
        locked_ifs: List[Tuple[str, ast.If, ast.With]] = []
        outer_tests: Dict[str, ast.If] = {}
        for node in sorted_by_position(nodes):
            if isinstance(node, ast.With) and _is_lock_with(
                ctx, node, info.owner_class
            ):
                locked_ifs.extend(
                    (ast.unparse(child.test), child, node)
                    for child in _nested_conditions(node)
                )
            elif isinstance(node, ast.If) and not info.locked_at(node.lineno):
                outer_tests.setdefault(ast.unparse(node.test), node)
        for test_text, inner, with_node in locked_ifs:
            outer = outer_tests.get(test_text)
            if outer is None or outer is inner:
                continue
            findings.append(
                ctx.diagnose(
                    rule_id="AIT-R02",
                    severity=Severity.WARNING,
                    confidence=0.85,
                    message=(
                        f"Condition `{test_text}` is checked outside the lock and again "
                        "inside it without an atomic synchronisation primitive"
                    ),
                    node=inner,
                    symbol=test_text,
                    evidence=[
                        ctx.evidence(outer, f"First check of `{test_text}` runs outside the lock"),
                        ctx.evidence(
                            inner,
                            f"Second check repeats `{test_text}` inside `{ast.unparse(with_node.items[0].context_expr)}`",
                        ),
                    ],
                    causation=[
                        ctx.step(1, outer, "State is observed before acquiring the lock"),
                        ctx.step(2, inner, "The same state is re-observed after acquiring it"),
                    ],
                    witness=ctx.make_witness("AIT-R02", inner, symbol=test_text),
                )
            )
    return findings
