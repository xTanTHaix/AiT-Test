"""Async trap rules: blocking I/O, orphaned tasks and missing await."""

from __future__ import annotations

import ast
from typing import Dict, List, Set, Tuple

from ..context import RuleContext
from ..models import Diagnostic, Severity
from ._util import ancestors, collect_body, sorted_by_position, tail_name
from .registry import rule

_BLOCKING_NETWORK: Dict[str, str] = {
    "requests.get": "HTTP request",
    "requests.post": "HTTP request",
    "requests.put": "HTTP request",
    "requests.patch": "HTTP request",
    "requests.delete": "HTTP request",
    "requests.head": "HTTP request",
    "requests.options": "HTTP request",
    "urllib.request.urlopen": "URL fetch",
    "urllib.request.urlretrieve": "URL download",
    "http.client.HTTPConnection.request": "HTTP request",
    "socket.create_connection": "socket connect",
    "smtplib.SMTP.sendmail": "SMTP delivery",
}
_BLOCKING_SLEEP: Dict[str, str] = {"time.sleep": "thread sleep"}
_BLOCKING_PROCESS: Dict[str, str] = {
    "subprocess.run": "subprocess wait",
    "subprocess.call": "subprocess wait",
    "subprocess.check_call": "subprocess wait",
    "subprocess.check_output": "subprocess wait",
    "subprocess.Popen": "process spawn",
    "os.system": "shell execution",
    "os.popen": "shell execution",
    "os.waitpid": "process wait",
}
_BLOCKING_IO: Dict[str, str] = {
    "open": "file open",
    "input": "stdin read",
    "shutil.copy": "file copy",
    "shutil.copy2": "file copy",
    "shutil.copyfile": "file copy",
    "shutil.copytree": "directory copy",
    "shutil.move": "file move",
    "shutil.rmtree": "directory delete",
    "sqlite3.connect": "database connect",
}
_OFFLOAD_TAILS = frozenset({"to_thread", "run_in_executor", "run_coroutine_threadsafe"})
_KNOWN_COROUTINES = frozenset(
    {
        "asyncio.sleep",
        "asyncio.gather",
        "asyncio.wait",
        "asyncio.wait_for",
        "asyncio.shield",
        "asyncio.open_connection",
        "asyncio.open_unix_connection",
        "asyncio.start_server",
        "asyncio.start_unix_server",
        "asyncio.to_thread",
        "asyncio.Queue.get",
        "asyncio.Queue.put",
        "asyncio.Event.wait",
        "asyncio.Lock.acquire",
        "asyncio.Condition.wait",
        "asyncio.Semaphore.acquire",
    }
)
_ALLOWING_TAILS = frozenset(
    {
        "create_task",
        "ensure_future",
        "gather",
        "wait",
        "wait_for",
        "shield",
        "run",
        "run_until_complete",
        "run_coroutine_threadsafe",
        "to_thread",
        "submit",
        "map",
        "add_done_callback",
    }
)
# One lookup instead of four membership probes: name -> (severity, confidence, category).
_BLOCKING_CALLS: Dict[str, Tuple[Severity, float, str]] = {
    **{
        name: (Severity.DEFECT, 0.96, category)
        for name, category in {
            **_BLOCKING_NETWORK,
            **_BLOCKING_SLEEP,
            **_BLOCKING_PROCESS,
        }.items()
    },
    **{
        name: (Severity.WARNING, 0.82, category)
        for name, category in _BLOCKING_IO.items()
    },
}


@rule(
    "AIT-A01",
    "Async Traps",
    "Blocking I/O in Async Context",
    Severity.DEFECT,
    "A synchronous blocking call inside a coroutine freezes the entire event loop.",
)
def check_blocking_call(ctx: RuleContext) -> List[Diagnostic]:
    """Detect synchronous blocking calls executed directly inside ``async def``."""
    findings: List[Diagnostic] = []
    for qualname, info in sorted(ctx.graph.functions.items()):
        if not info.is_async:
            continue
        nodes, parents = collect_body(info.node)
        for node in sorted_by_position(nodes):
            if not isinstance(node, ast.Call):
                continue
            text = ctx.call_text(node.func)
            details = _BLOCKING_CALLS.get(text)
            if details is None:
                continue
            if _is_offloaded(ctx, node, parents):
                continue
            severity, confidence, category = details
            findings.append(
                ctx.diagnose(
                    rule_id="AIT-A01",
                    severity=severity,
                    confidence=confidence,
                    message=(
                        f"`{text}()` performs a synchronous {category} inside "
                        f"`{info.qualname}` and stalls the event loop"
                    ),
                    node=node,
                    function=info.signature_label(),
                    symbol=text,
                    evidence=[
                        ctx.evidence(
                            node,
                            f"Synchronous `{text}()` called inside the coroutine "
                            f"`{info.qualname}` with no thread offload",
                        )
                    ],
                    causation=[
                        ctx.step(
                            1,
                            info.node,
                            f"`{info.qualname}` runs on the event loop thread",
                        ),
                        ctx.step(
                            2,
                            node,
                            f"`{text}()` blocks that thread; no other task can progress until it returns",
                        ),
                    ],
                    witness=ctx.make_witness(
                        "AIT-A01", node, symbol=text, detail={"call": text}
                    ),
                )
            )
    return findings


def _is_offloaded(ctx: RuleContext, node: ast.AST, parents) -> bool:
    """Return True when a blocking call is handed to a worker thread."""
    for candidate in ancestors(node, parents):
        if isinstance(candidate, ast.Call):
            text = ctx.call_text(candidate.func)
            if tail_name(text) in _OFFLOAD_TAILS:
                return True
    return False


@rule(
    "AIT-A02",
    "Async Traps",
    "Unreferenced Task GC Pruning",
    Severity.WARNING,
    "A task is created as a bare statement, so no strong reference protects it from collection.",
)
def check_orphan_task(ctx: RuleContext) -> List[Diagnostic]:
    """Detect ``create_task``/``ensure_future`` calls whose result is discarded."""
    findings: List[Diagnostic] = []
    for qualname, info in sorted(ctx.graph.functions.items()):
        nodes, _parents = collect_body(info.node)
        for node in sorted_by_position(nodes):
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            text = ctx.call_text(node.value.func)
            if tail_name(text) not in ("create_task", "ensure_future"):
                continue
            findings.append(
                ctx.diagnose(
                    rule_id="AIT-A02",
                    severity=Severity.WARNING,
                    confidence=0.9,
                    message=(
                        f"`{text}()` result is discarded; the event loop only keeps a weak "
                        "reference, so the task can be collected mid-flight"
                    ),
                    node=node,
                    function=info.signature_label(),
                    symbol=text,
                    evidence=[
                        ctx.evidence(
                            node,
                            "Task handle is not stored in any variable, attribute or set",
                        )
                    ],
                    causation=[
                        ctx.step(1, node.value, "Task is scheduled and the only strong reference dies here"),
                        ctx.step(2, node, "Garbage collection may cancel the pending coroutine"),
                    ],
                    witness=ctx.make_witness("AIT-A02", node, symbol=text),
                )
            )
    return findings


@rule(
    "AIT-A03",
    "Async Traps",
    "Missing Await on Coroutine",
    Severity.DEFECT,
    "A coroutine is invoked without await, so the body never executes.",
)
def check_missing_await(ctx: RuleContext) -> List[Diagnostic]:
    """Detect coroutine calls that are not awaited, scheduled or gathered."""
    findings: List[Diagnostic] = []
    local_coroutines = {
        frame.name for frame in ctx.scopes.functions() if frame.is_async
    }
    for qualname, info in sorted(ctx.graph.functions.items()):
        nodes, parents = collect_body(info.node)
        for node in sorted_by_position(nodes):
            if not isinstance(node, ast.Call):
                continue
            text = ctx.call_text(node.func)
            tail = tail_name(text)
            if not _is_coroutine_call(ctx, node, text, tail, local_coroutines):
                continue
            if _is_awaited_or_scheduled(ctx, node, parents):
                continue
            patch = ctx.line_patch(
                node.lineno,
                f"{ast.unparse(node.func)}(",
                f"await {ast.unparse(node.func)}(",
                f"Await `{text}()` so the coroutine actually runs",
            )
            findings.append(
                ctx.diagnose(
                    rule_id="AIT-A03",
                    severity=Severity.DEFECT,
                    confidence=0.95 if text in _KNOWN_COROUTINES else 0.9,
                    message=(
                        f"`{text}()` produces a coroutine object that is never awaited"
                    ),
                    node=node,
                    function=info.signature_label(),
                    symbol=text,
                    evidence=[
                        ctx.evidence(
                            node,
                            f"`{text}` is declared async and the call has no `await`",
                        )
                    ],
                    causation=[
                        ctx.step(1, node, "Coroutine object is created instead of executed"),
                        ctx.step(2, node, "Control flow continues without the coroutine body running"),
                    ],
                    witness=ctx.make_witness("AIT-A03", node, symbol=text),
                    patch=patch,
                )
            )
    return findings


def _is_coroutine_call(
    ctx: RuleContext, node: ast.Call, text: str, tail: str, local_coroutines: Set[str]
) -> bool:
    """Decide whether a call target is a known coroutine function."""
    if text in _KNOWN_COROUTINES:
        return True
    if "." not in text:
        return tail in local_coroutines
    if text.startswith("self."):
        owner = ctx.frame(node).owner_class
        target = ctx.scopes.by_qualname.get(f"{owner}.{tail}")
        return target is not None and target.is_async
    return False


def _is_awaited_or_scheduled(ctx: RuleContext, node: ast.Call, parents) -> bool:
    """Return True when an ancestor awaits, schedules or transparently forwards the coroutine."""
    for candidate in ancestors(node, parents):
        if isinstance(candidate, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
        if isinstance(candidate, ast.Return):
            return True
        if isinstance(candidate, ast.Call):
            text = ctx.call_text(candidate.func)
            if tail_name(text) in _ALLOWING_TAILS:
                return True
    return False
