"""Process safety rules: pipe deadlocks, unreaped children, import-time spawns."""

from __future__ import annotations

import ast
from typing import Dict, List, Set, Tuple

from ..context import RuleContext
from ..models import Diagnostic, Severity
from ._util import (
    ancestors,
    call_keyword,
    collect_body,
    is_pipe_value,
    module_level_calls,
    sorted_by_position,
    statement_ancestor,
)
from .registry import rule

_REAPER_METHODS = frozenset({"wait", "poll", "communicate", "join"})
_SPAWN_SUFFIXES = (".Process", ".Thread", ".Pool", ".ProcessPoolExecutor")


def _is_popen(ctx: RuleContext, call: ast.Call) -> bool:
    """Return True for ``subprocess.Popen`` regardless of import aliasing."""
    text = ctx.call_text(call.func)
    return text == "Popen" or text.endswith(".Popen")


def _is_process(ctx: RuleContext, call: ast.Call) -> bool:
    """Return True for ``multiprocessing.Process`` constructions."""
    text = ctx.call_text(call.func)
    return text == "Process" or text.endswith(".Process")


def _assigned_name(call: ast.Call, parents: Dict[int, ast.AST]) -> str:
    """Return the single name a construction call is bound to, if any."""
    parent = parents.get(id(call))
    if isinstance(parent, ast.Assign) and len(parent.targets) == 1:
        target = parent.targets[0]
        if isinstance(target, ast.Name):
            return target.id
    if isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
        return parent.target.id
    return ""


def _inside_with(call: ast.Call, parents: Dict[int, ast.AST]) -> bool:
    """Return True when a construction call runs inside a ``with`` statement."""
    for candidate in ancestors(call, parents):
        if isinstance(candidate, (ast.With, ast.AsyncWith)):
            return True
    return False


@rule(
    "AIT-Z01",
    "Process Safety",
    "Subprocess Pipe Deadlock Hazard",
    Severity.DEFECT,
    "A pipe-backed Popen is waited on without draining, so a chatty child deadlocks the parent.",
)
def check_pipe_deadlock(ctx: RuleContext) -> List[Diagnostic]:
    """Detect ``Popen(stdout/stderr=PIPE)`` followed by ``.wait()`` without ``communicate()``."""
    findings: List[Diagnostic] = []
    for qualname, info in sorted(ctx.graph.functions.items()):
        nodes, parents = collect_body(info.node)
        constructions: List[Tuple[ast.Call, str, bool, bool]] = []
        for node in sorted_by_position(nodes):
            if not isinstance(node, ast.Call) or not _is_popen(ctx, node):
                continue
            if _inside_with(node, parents):
                continue
            piped_out = is_pipe_value(call_keyword(node, "stdout"))
            piped_err = is_pipe_value(call_keyword(node, "stderr"))
            if not (piped_out or piped_err):
                continue
            variable = _assigned_name(node, parents)
            if not variable:
                continue
            constructions.append((node, variable, piped_out, piped_err))
        if not constructions:
            continue
        drained: Set[str] = set()
        for node in nodes:
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "communicate"
                and isinstance(node.func.value, ast.Name)
            ):
                drained.add(node.func.value.id)
        waits: Dict[str, ast.Call] = {}
        for node in sorted_by_position(nodes):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "wait"
                and isinstance(node.func.value, ast.Name)
            ):
                waits.setdefault(node.func.value.id, node)
        for construction, variable, piped_out, piped_err in constructions:
            if variable in drained or variable not in waits:
                continue
            wait_call = waits[variable]
            channels = [
                name
                for name, present in (("stdout", piped_out), ("stderr", piped_err))
                if present
            ]
            evidence = [
                ctx.evidence(
                    construction,
                    f"`{variable}` is created with {', '.join(f'{item}=PIPE' for item in channels)}",
                ),
                ctx.evidence(
                    wait_call,
                    f"`{variable}.wait()` blocks the parent before the pipes are drained",
                ),
            ]
            causation = [
                ctx.step(
                    1,
                    construction,
                    f"Subprocess starts with a pipe-backed {channels[0]}; the OS buffer "
                    "is bounded (64 KiB on POSIX, ~4 KiB anonymous pipes on Windows)",
                ),
                ctx.step(
                    2,
                    wait_call,
                    "Direct `wait()` waits for termination while the child may be blocked "
                    "writing into the undrained pipe: a circular wait",
                ),
            ]
            patch = _pipe_deadlock_patch(ctx, wait_call, variable, parents)
            findings.append(
                ctx.diagnose(
                    rule_id="AIT-Z01",
                    severity=Severity.DEFECT,
                    confidence=0.98 if len(channels) == 2 else 0.93,
                    message=(
                        f"`{variable}.wait()` can deadlock: the child writes into a pipe "
                        "that nobody drains"
                    ),
                    node=wait_call,
                    symbol=variable,
                    evidence=evidence,
                    causation=causation,
                    witness=ctx.make_witness("AIT-Z01", wait_call, symbol=variable),
                    patch=patch,
                )
            )
    return findings


def _pipe_deadlock_patch(
    ctx: RuleContext, wait_call: ast.Call, variable: str, parents: Dict[int, ast.AST]
):
    """Replace a terminal ``wait()`` with ``communicate()`` when the shape allows it."""
    statement = statement_ancestor(wait_call, parents)
    if statement is None:
        return None
    indent = ctx.indent_at(int(getattr(statement, "lineno", 1) or 1))
    drain = (
        f"{indent}{variable}_stdout, {variable}_stderr = {variable}.communicate()"
        f"{ctx.newline}"
    )
    rationale = "Drain both pipes with communicate() before reading the exit status"
    if isinstance(statement, ast.Return):
        replacement = drain + f"{indent}return {variable}.returncode{ctx.newline}"
    elif isinstance(statement, ast.Expr):
        replacement = drain
    else:
        return None
    return ctx.statement_patch(statement, replacement, rationale)


@rule(
    "AIT-Z02",
    "Process Safety",
    "Unreaped Zombie Process Hazard",
    Severity.DEFECT,
    "A child process is created without any wait/poll/communicate/join on the owning scope.",
)
def check_unreaped_child(ctx: RuleContext) -> List[Diagnostic]:
    """Detect child processes that are never reaped inside their creating scope."""
    findings: List[Diagnostic] = []
    for qualname, info in sorted(ctx.graph.functions.items()):
        nodes, parents = collect_body(info.node)
        constructions: List[Tuple[ast.Call, str, str]] = []
        for node in sorted_by_position(nodes):
            if not isinstance(node, ast.Call):
                continue
            kind = ""
            if _is_popen(ctx, node):
                kind = "Popen"
            elif _is_process(ctx, node):
                kind = "Process"
            if not kind:
                continue
            if _inside_with(node, parents):
                continue
            variable = _assigned_name(node, parents)
            if not variable:
                continue
            daemon = call_keyword(node, "daemon")
            if kind == "Process" and isinstance(daemon, ast.Constant) and daemon.value is True:
                continue
            constructions.append((node, variable, kind))
        if not constructions:
            continue
        reaped: Set[str] = set()
        released: Set[str] = set()
        for node in nodes:
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            receiver = node.func.value.id
            if node.func.attr in _REAPER_METHODS:
                reaped.add(receiver)
            elif node.func.attr in ("terminate", "kill"):
                released.add(receiver)
        owned: Set[str] = set()
        for node in nodes:
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Name):
                owned.add(node.value.id)
        for construction, variable, kind in constructions:
            if variable in reaped or variable in released or variable in owned:
                continue
            findings.append(
                ctx.diagnose(
                    rule_id="AIT-Z02",
                    severity=Severity.DEFECT,
                    confidence=0.9,
                    message=(
                        f"`{kind}` handle `{variable}` is never reaped "
                        "(no wait/poll/communicate/join in this scope)"
                    ),
                    node=construction,
                    symbol=variable,
                    evidence=[
                        ctx.evidence(
                            construction,
                            f"Child `{variable}` is created here and ownership is not transferred",
                        ),
                        ctx.evidence(
                            info.node,
                            f"`{info.signature_label()}` contains no reaper for `{variable}`",
                        ),
                    ],
                    causation=[
                        ctx.step(1, construction, "Child process starts and becomes unreapable "
                                 "by anyone except the OS once this scope drops the handle"),
                        ctx.step(
                            2,
                            info.node,
                            "Process exit without wait/join leaves a zombie entry in the process table",
                        ),
                    ],
                    witness=ctx.make_witness("AIT-Z02", construction, symbol=variable),
                )
            )
    return findings


@rule(
    "AIT-Z03",
    "Process Safety",
    "Unguarded Multiprocess Spawn",
    Severity.WARNING,
    "A process or thread is spawned at import time outside an if __name__ == '__main__' guard.",
)
def check_import_time_spawn(ctx: RuleContext) -> List[Diagnostic]:
    """Detect spawn calls executed during module import under spawn-capable platforms."""
    findings: List[Diagnostic] = []
    for call, guarded in module_level_calls(ctx.tree):
        if guarded:
            continue
        text = ctx.call_text(call.func)
        if not (text in _SPAWN_SUFFIXES or text.endswith(_SPAWN_SUFFIXES)):
            continue
        findings.append(
            ctx.diagnose(
                rule_id="AIT-Z03",
                severity=Severity.WARNING,
                confidence=0.95,
                message=(
                    f"`{text}` runs at import time; on Windows spawn mode every child "
                    "re-imports this module and spawns again"
                ),
                node=call,
                symbol=text,
                evidence=[
                    ctx.evidence(
                        call,
                        "Spawn call sits at module scope, so it executes on every import "
                        "including interpreter start-up for spawned children",
                    )
                ],
                causation=[
                    ctx.step(
                        1,
                        call,
                        "Module import executes the spawn statement unconditionally",
                    ),
                    ctx.step(
                        2,
                        ctx.tree,
                        "A spawned child re-imports the module and reaches this statement again",
                    ),
                ],
                witness=ctx.make_witness("AIT-Z03", call, symbol=text),
            )
        )
    return findings
