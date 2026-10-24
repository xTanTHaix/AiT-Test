"""Inter-procedural call graph, spawn topology and lock-region map.

Concurrency findings are only credible when the engine can show *who* runs
concurrently and *where* the shared state is touched. This module builds that
evidence: resolved call edges, thread/process/task spawn sites, ``with lock``
regions and the mutating writes that sit outside them.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .scope_tree import ScopeFrame, ScopeTree, source_of

MUTATING_METHODS: frozenset[str] = frozenset(
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
        "write",
        "writelines",
        "seek",
        "truncate",
        "__setitem__",
        "__delitem__",
    }
)

_LOCK_NAME_HINTS: Tuple[str, ...] = ("lock", "mutex", "semaphore", "gate")
_SPAWN_CALLEES: Tuple[str, ...] = (
    "threading.Thread",
    "multiprocessing.Process",
    "multiprocessing.context.Process",
    "concurrent.futures.ThreadPoolExecutor",
    "concurrent.futures.ProcessPoolExecutor",
)
_SUBMIT_SUFFIXES: Tuple[str, ...] = (".submit", ".map", ".apply_async")
_THREAD_HOP_CALLEES: Tuple[str, ...] = ("asyncio.to_thread", "asyncio.run_coroutine_threadsafe")


@dataclass(slots=True)
class CallSite:
    """One resolved call edge observed inside a function body."""

    callee_text: str
    callee_qualname: str
    lineno: int
    col: int
    node: ast.Call

    @property
    def label(self) -> str:
        """Prefer the fully resolved qualname, falling back to source text."""
        return self.callee_qualname or self.callee_text


@dataclass(slots=True)
class LockRegion:
    """A line span during which a synchronisation primitive is held."""

    lock_expr: str
    start_line: int
    end_line: int


def _reads_target(value: ast.AST, target: ast.AST) -> bool:
    """Return True when ``value`` loads the same symbol that ``target`` writes.

    ``x = x + 1`` and ``self.x = self.x + 1`` are read-modify-write sequences just
    as much as ``x += 1``: the load and the store are separate steps that a
    second thread can interleave.
    """
    if isinstance(target, ast.Name):
        return any(
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id == target.id
            for node in ast.walk(value)
        )
    if isinstance(target, ast.Attribute):
        base = source_of(target.value)
        return bool(base) and any(
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and node.attr == target.attr
            and source_of(node.value) == base
            for node in ast.walk(value)
        )
    if isinstance(target, ast.Subscript):
        text = source_of(target)
        return bool(text) and any(
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and source_of(node) == text
            for node in ast.walk(value)
        )
    return False


@dataclass(slots=True)
class WriteRecord:
    """A mutation of shared state discovered inside one function."""

    target: str
    attr: str
    lineno: int
    col: int
    kind: str
    is_self_attr: bool
    is_global: bool
    reads_target: bool = False
    locked_by: str = ""
    node: Optional[ast.AST] = None


@dataclass(slots=True)
class FunctionInfo:
    """Resolved metadata for a single function or method."""

    qualname: str
    name: str
    node: ast.AST
    lineno: int
    end_lineno: int
    is_async: bool
    is_method: bool
    owner_class: str
    parameters: Tuple[str, ...]
    calls: List[CallSite] = field(default_factory=list)
    lock_regions: List[LockRegion] = field(default_factory=list)
    writes: List[WriteRecord] = field(default_factory=list)

    def locked_at(self, lineno: int) -> str:
        """Return the lock expression held at ``lineno``, or an empty string."""
        for region in self.lock_regions:
            if region.start_line <= lineno <= region.end_line:
                return region.lock_expr
        return ""

    def signature_label(self) -> str:
        """Render ``name(param, ...)`` for failure blocks and slices."""
        return f"{self.name}({', '.join(self.parameters)})"


class _BodyAnalyzer(ast.NodeVisitor):
    """Collect calls, lock regions and shared-state writes for one function.

    Nested ``def``/``class``/``lambda`` nodes are intentionally not traversed:
    they own their own :class:`FunctionInfo`, and attributing their bodies to the
    parent would fabricate concurrency evidence that does not exist.
    """

    def __init__(self, owner: "CallGraph", info: FunctionInfo, scopes: ScopeTree) -> None:
        self.owner = owner
        self.info = info
        self.scopes = scopes
        self.pending_calls: List[Tuple[ast.expr, ast.Call]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in list(node.args.defaults) + [
            item for item in node.args.kw_defaults if item is not None
        ]:
            self.visit(default)

    def visit_Call(self, node: ast.Call) -> None:
        self.pending_calls.append((node.func, node))
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("acquire", "release"):
            parent = source_of(node.func.value)
            if node.func.attr == "acquire":
                self.info.lock_regions.append(
                    LockRegion(
                        lock_expr=parent,
                        start_line=node.lineno,
                        end_line=self.info.end_lineno,
                    )
                )
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            expression = item.context_expr
            if self.owner.is_lock_expression(expression, self.info.owner_class):
                self.info.lock_regions.append(
                    LockRegion(
                        lock_expr=source_of(expression),
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                    )
                )
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        for item in node.items:
            expression = item.context_expr
            if self.owner.is_lock_expression(expression, self.info.owner_class):
                self.info.lock_regions.append(
                    LockRegion(
                        lock_expr=source_of(expression),
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                    )
                )
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._record_target(node.target, "augassign", node.lineno, node.col_offset)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_target(
                target,
                "assign",
                node.lineno,
                node.col_offset,
                reads_target=_reads_target(node.value, target),
            )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_target(
            node.target,
            "assign",
            node.lineno,
            node.col_offset,
            reads_target=node.value is not None
            and _reads_target(node.value, node.target),
        )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, ast.Store):
            self._record_target(node, "itemwrite", node.lineno, node.col_offset)
        self.generic_visit(node)

    def _record_target(
        self,
        target: ast.AST,
        kind: str,
        lineno: int,
        col: int,
        *,
        reads_target: bool = False,
    ) -> None:
        text = source_of(target)
        if not text:
            return
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            is_self = target.value.id == "self"
            self.info.writes.append(
                WriteRecord(
                    target=text,
                    attr=target.attr,
                    lineno=lineno,
                    col=col,
                    kind=kind,
                    is_self_attr=is_self,
                    is_global=False,
                    reads_target=reads_target,
                    node=target,
                )
            )
            return
        if isinstance(target, ast.Name):
            binding = self.scopes.resolve(target.id, self.info_frame)
            resolved_global = (
                binding is not None
                and binding.kind != "param"
                and binding.lineno < self.info.lineno
            ) or target.id in self.info_frame.globals_declared
            self.info.writes.append(
                WriteRecord(
                    target=text,
                    attr=target.id,
                    lineno=lineno,
                    col=col,
                    kind=kind,
                    is_self_attr=False,
                    is_global=resolved_global,
                    reads_target=reads_target,
                    node=target,
                )
            )

    @property
    def info_frame(self) -> ScopeFrame:
        """Resolve the scope frame backing the function being analyzed."""
        return self.scopes.by_qualname[self.info.qualname]


class CallGraph:
    """Immutable index of call edges, spawn sites and shared-state writes."""

    def __init__(self, scopes: ScopeTree) -> None:
        self.scopes = scopes
        self.functions: Dict[str, FunctionInfo] = {}
        self.spawn_sites: Dict[str, List[CallSite]] = {}
        self.concurrent: Set[str] = set()
        self._pending: Dict[str, List[Tuple[ast.expr, ast.Call]]] = {}
        self._qualified_by_parent: Dict[Tuple[str, str], str] = {}
        self._build()

    # ---------------------------------------------------------------- queries
    def is_concurrent(self, qualname: str) -> bool:
        """Return True when ``qualname`` is reachable from a spawn site."""
        return qualname in self.concurrent

    def callees(self, qualname: str) -> List[str]:
        """Return resolved callee qualnames for a function."""
        info = self.functions.get(qualname)
        if info is None:
            return []
        return [site.callee_qualname for site in info.calls if site.callee_qualname]

    def spawn_sites_for(self, qualname: str) -> List[CallSite]:
        """Return every spawn site that starts ``qualname`` on another thread/process."""
        return list(self.spawn_sites.get(qualname, ()))

    def class_locks(self, class_qualname: str) -> Tuple[str, ...]:
        """Return synchronisation attribute names declared by a class."""
        return self.scopes.locks_in(class_qualname)

    def is_lock_expression(self, expression: ast.AST, class_qualname: str) -> bool:
        """Decide whether a context-manager expression is a synchronisation primitive.

        Exact matches against declared lock attributes are authoritative. The
        name-shape heuristic is a deliberate second tier: it only ever *suppresses*
        findings, and a false positive here can hide a defect, so it is limited to
        unambiguous suffixes such as ``self._lock`` or ``self.state_mutex``.
        """
        text = source_of(expression)
        if not text:
            return False
        if class_qualname:
            for lock_name in self.class_locks(class_qualname):
                if text.endswith(f".{lock_name}") or text == lock_name:
                    return True
        lowered = text.lower()
        for hint in _LOCK_NAME_HINTS:
            if lowered.endswith(hint) or f"_{hint}" in lowered or f".{hint}" in lowered:
                return True
        return False

    # ---------------------------------------------------------------- build
    def _build(self) -> None:
        for frame in self.scopes.functions():
            info = FunctionInfo(
                qualname=frame.qualname,
                name=frame.name,
                node=frame.node,
                lineno=frame.lineno,
                end_lineno=getattr(frame.node, "end_lineno", frame.lineno) or frame.lineno,
                is_async=frame.is_async,
                is_method=frame.is_method,
                owner_class=frame.owner_class,
                parameters=tuple(binding.name for binding in frame.parameters()),
            )
            self.functions[info.qualname] = info
            parent = frame.parent.qualname if frame.parent is not None else ""
            self._qualified_by_parent[(parent, frame.name)] = info.qualname

        for qualname, info in self.functions.items():
            analyzer = _BodyAnalyzer(self, info, self.scopes)
            for statement in getattr(info.node, "body", ()):  # type: ignore[attr-defined]
                analyzer.visit(statement)
            self._pending[qualname] = analyzer.pending_calls
            for write in info.writes:
                write.locked_by = info.locked_at(write.lineno)

        for qualname, info in self.functions.items():
            for expression, call in self._pending.get(qualname, ()):
                text = self.resolve_text(expression, info)
                self._index_spawn(info, text, call)
                info.calls.append(
                    CallSite(
                        callee_text=text,
                        callee_qualname=self.resolve_qualname(expression, info),
                        lineno=call.lineno,
                        col=call.col_offset,
                        node=call,
                    )
                )
        self._close_concurrency_set()

    def resolve_text(self, expression: ast.AST, info: FunctionInfo) -> str:
        """Render a call target expression, expanding import aliases when possible."""
        if isinstance(expression, ast.Name):
            declared = self.scopes.resolve_import(expression.id, self.scopes.by_qualname[info.qualname])
            return declared or expression.id
        if isinstance(expression, ast.Attribute):
            base = expression.value
            text = source_of(expression)
            if isinstance(base, ast.Name):
                declared = self.scopes.resolve_import(
                    base.id, self.scopes.by_qualname[info.qualname]
                )
                if declared:
                    return f"{declared}{text[len(base.id):]}"
            return text
        return source_of(expression)

    def resolve_qualname(self, expression: ast.AST, info: FunctionInfo) -> str:
        """Resolve a callee expression to a qualname defined inside this module.

        Attribute receivers are resolved in three escalating steps: ``self`` (the
        enclosing class), a class named directly (``Counter.bump``), and a local
        variable whose binding is a constructor call (``counter = Counter()``).
        A unique method-name fallback runs last so ``Thread(target=state.bump)``
        still resolves when the receiver type cannot be inferred — but only when
        exactly one function in the module carries that name.
        """
        if isinstance(expression, ast.Name):
            frame = self.scopes.by_qualname[info.qualname]
            for candidate in (frame, frame.parent, self.scopes.module):
                if candidate is None:
                    continue
                if (candidate.qualname, expression.id) in self._qualified_by_parent:
                    return self._qualified_by_parent[(candidate.qualname, expression.id)]
            return ""
        if isinstance(expression, ast.Attribute):
            receiver = expression.value
            if isinstance(receiver, ast.Name) and receiver.id == "self":
                key = (info.owner_class, expression.attr)
                return self._qualified_by_parent.get(key, "")
            if isinstance(receiver, ast.Name):
                key = (receiver.id, expression.attr)
                if key in self._qualified_by_parent:
                    return self._qualified_by_parent[key]
                inferred = self._constructor_owner(receiver.id, info, expression)
                if inferred:
                    key = (inferred, expression.attr)
                    if key in self._qualified_by_parent:
                        return self._qualified_by_parent[key]
                return self._unique_method(expression.attr)
            return ""
        return ""

    def _constructor_owner(
        self, name: str, info: FunctionInfo, expression: ast.AST
    ) -> str:
        """Infer the class of a local variable from ``name = Class(...)``."""
        frame = self.scopes.by_qualname.get(info.qualname)
        if frame is None:
            return ""
        binding = self.scopes.resolve(
            name, frame, getattr(expression, "lineno", None)
        )
        if binding is None or not isinstance(binding.value, ast.Call):
            return ""
        owner_text = source_of(binding.value.func)
        if not owner_text:
            return ""
        tail = owner_text.rsplit(".", 1)[-1]
        matches = [
            candidate
            for candidate in self.scopes.frames
            if candidate.kind == "class" and candidate.name == tail
        ]
        if len(matches) == 1:
            return matches[0].qualname
        return ""

    def _unique_method(self, method_name: str) -> str:
        """Return a method qualname when exactly one function carries the name."""
        candidates = [
            qualname
            for (parent, name), qualname in self._qualified_by_parent.items()
            if name == method_name and parent
        ]
        if len(candidates) == 1:
            return candidates[0]
        return ""

    def _index_spawn(self, info: FunctionInfo, text: str, call: ast.Call) -> None:
        """Record a spawn site when ``call`` starts work on another thread or process."""
        lowered = text
        is_spawn = lowered in _THREAD_HOP_CALLEES
        if not is_spawn:
            for suffix in _SUBMIT_SUFFIXES:
                if lowered.endswith(suffix):
                    is_spawn = True
                    break
        if not is_spawn:
            for callee in _SPAWN_CALLEES:
                if lowered == callee or lowered.endswith(f".{callee.split('.')[-1]}"):
                    is_spawn = True
                    break
        if not is_spawn:
            return
        target_expr: Optional[ast.AST] = None
        for keyword in call.keywords:
            if keyword.arg == "target":
                target_expr = keyword.value
        if target_expr is None and call.args:
            target_expr = call.args[0]
        if target_expr is None:
            return
        resolved = self.resolve_qualname(target_expr, info)
        if not resolved:
            return
        site = CallSite(
            callee_text=self.resolve_text(target_expr, info),
            callee_qualname=resolved,
            lineno=call.lineno,
            col=call.col_offset,
            node=call,
        )
        self.spawn_sites.setdefault(resolved, []).append(site)

    def _close_concurrency_set(self) -> None:
        """Expand the concurrent set through the call graph (bounded to 3 hops)."""
        frontier = {qualname for qualname in self.spawn_sites}
        self.concurrent = set(frontier)
        for _ in range(3):
            next_frontier: Set[str] = set()
            for qualname in frontier:
                for callee in self.callees(qualname):
                    if callee in self.functions and callee not in self.concurrent:
                        next_frontier.add(callee)
            if not next_frontier:
                break
            self.concurrent |= next_frontier
            frontier = next_frontier


def build_call_graph(scopes: ScopeTree) -> CallGraph:
    """Construct the call graph for a parsed module."""
    return CallGraph(scopes)
