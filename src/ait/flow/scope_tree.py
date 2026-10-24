"""Hierarchical symbol table and lexical scope stack.

Rules need to answer three questions fast: which scope am I in, what is the
latest binding of this name, and what does the declared type say about it. This
module builds that index once per file so rules never re-walk the tree, and it
records every binding with its line number so the backward tracer can follow a
value without guessing.
"""

from __future__ import annotations

import ast
import bisect
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_LOCK_FACTORY_NAMES = frozenset(
    {
        "Lock",
        "RLock",
        "Semaphore",
        "BoundedSemaphore",
        "Condition",
        "Event",
        "Barrier",
        "local",
    }
)
_MUTABLE_DEFAULT_FACTORIES = frozenset({"list", "dict", "set", "bytearray", "deque"})


def source_of(node: Optional[ast.AST]) -> str:
    """Render a node back to source text, tolerating synthetic or partial nodes."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError, RecursionError):
        return ""


def annotation_is_optional(annotation: str) -> bool:
    """Return True when an annotation text can evaluate to ``None``.

    Only the *outer* type matters: ``Optional[T]``, ``T | None`` and bare ``None``
    qualify, while ``tuple[int, Optional[int]]`` is a tuple whose *elements* may be
    None, not a nullable tuple. Treating the inner mention as nullable produced
    false positives on every container annotation. Anything richer needs a type
    checker, so the engine stays honest and treats the rest as "unknown".
    """
    text = annotation.strip()
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        text = text[1:-1].strip()
    if not text:
        return False
    if text in ("None", "NoneType"):
        return True
    for prefix in ("Optional[", "typing.Optional["):
        if text.startswith(prefix):
            return True
    for prefix in ("Union[", "typing.Union["):
        if text.startswith(prefix):
            inner = text[text.index("[") + 1 : -1]
            return any(
                part.strip() in ("None", "NoneType") for part in _split_top_level(inner, ",")
            )
    return any(
        part.strip() in ("None", "NoneType") for part in _split_top_level(text, "|")
    )


def _split_top_level(text: str, separator: str) -> List[str]:
    """Split on a separator that appears outside brackets and string literals."""
    parts: List[str] = []
    depth = 0
    quote = ""
    current: List[str] = []
    for character in text:
        if quote:
            current.append(character)
            if character == quote:
                quote = ""
            continue
        if character in "\"'":
            quote = character
            current.append(character)
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
        if character == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(character)
    parts.append("".join(current))
    return parts


@dataclass(slots=True)
class Binding:
    """One occurrence that binds a name inside a scope."""

    name: str
    lineno: int
    kind: str
    node: Optional[ast.AST] = None
    value: Optional[ast.AST] = None
    annotation: str = ""
    mutable_default: bool = False
    default_is_none: bool = False


@dataclass(slots=True)
class AttrInfo:
    """A class-level attribute discovered from annotations or ``self.x`` writes."""

    name: str
    lineno: int
    annotation: str = ""
    assigned_none: bool = False
    lock_factory: bool = False
    owner: str = ""

    @property
    def optional(self) -> bool:
        """Return True when the declared type admits ``None`` or a None literal was bound."""
        return self.assigned_none or annotation_is_optional(self.annotation)


@dataclass(slots=True)
class ScopeFrame:
    """A lexical scope (module, class, function or lambda)."""

    kind: str
    name: str
    qualname: str
    node: ast.AST
    lineno: int
    parent: Optional["ScopeFrame"] = None
    is_async: bool = False
    is_method: bool = False
    is_staticmethod: bool = False
    is_classmethod: bool = False
    decorators: Tuple[str, ...] = ()
    return_annotation: str = ""
    returns_optional: bool = False
    owner_class: str = ""
    bindings: Dict[str, List[Binding]] = field(default_factory=dict)
    globals_declared: set = field(default_factory=set)
    nonlocals_declared: set = field(default_factory=set)
    imports: Dict[str, str] = field(default_factory=dict)
    children: List["ScopeFrame"] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Human-readable qualname annotated with the async marker when relevant."""
        prefix = "async " if self.is_async and self.kind == "function" else ""
        return f"{prefix}{self.qualname}"

    def add_binding(self, binding: Binding) -> None:
        """Append a binding for ``binding.name`` preserving source order."""
        self.bindings.setdefault(binding.name, []).append(binding)

    def latest_binding(self, name: str, before_line: Optional[int] = None) -> Optional[Binding]:
        """Return the newest binding of ``name`` at or before ``before_line``.

        ``before_line`` is inclusive for assignment lookups because a diagnostic
        anchored on the assignment line must still see that assignment.
        """
        candidates = self.bindings.get(name, ())
        if not candidates:
            return None
        if before_line is None:
            return candidates[-1]
        eligible = [item for item in candidates if item.lineno <= before_line]
        if not eligible:
            return None
        return eligible[-1]

    def parameters(self) -> List[Binding]:
        """Return every parameter binding (positional, keyword-only and variadic)."""
        collected: List[Binding] = []
        for bindings in self.bindings.values():
            for binding in bindings:
                if binding.kind in ("param", "kwonly", "vararg", "kwarg"):
                    collected.append(binding)
        collected.sort(key=lambda item: item.lineno)
        return collected


class ScopeTree:
    """Index of every scope and binding inside a single parsed module."""

    def __init__(self, path: str, source: str, tree: ast.Module) -> None:
        self.path = path
        self.source = source
        self.tree = tree
        self.frames: List[ScopeFrame] = []
        self.by_node: Dict[int, ScopeFrame] = {}
        self.by_qualname: Dict[str, ScopeFrame] = {}
        self.class_attrs: Dict[str, Dict[str, AttrInfo]] = {}
        self.imports: Dict[str, str] = {}
        self.module = ScopeFrame(
            kind="module",
            name="<module>",
            qualname="<module>",
            node=tree,
            lineno=1,
        )
        self._register(self.module)
        _ScopeBuilder(self).build()
        self._spans: List[Tuple[int, int, ScopeFrame]] = []
        self._span_starts: List[int] = []
        self._build_span_index()

    def _build_span_index(self) -> None:
        """Precompute a bisectable line-span index for O(log n) scope lookups.

        Rules call ``frame_for`` for every candidate node; a linear scan would
        make deep modules quadratic, so the index is built once per file.
        """
        spans: List[Tuple[int, int, ScopeFrame]] = []
        for frame in self.frames:
            start = getattr(frame.node, "lineno", 0) or 0
            end = getattr(frame.node, "end_lineno", 0) or start
            spans.append((start, max(start, end), frame))
        module_end = max((item[1] for item in spans), default=1)
        indexed = [
            (start, module_end if frame is self.module else end, frame)
            for start, end, frame in spans
        ]
        indexed.sort(key=lambda item: (item[0], -item[1]))
        self._spans = indexed
        self._span_starts = [item[0] for item in indexed]

    # ---------------------------------------------------------------- registry
    def _register(self, frame: ScopeFrame) -> None:
        self.frames.append(frame)
        self.by_node[id(frame.node)] = frame
        self.by_qualname[frame.qualname] = frame

    def add_frame(self, frame: ScopeFrame) -> None:
        """Register a newly created frame (called by the builder during traversal)."""
        self._register(frame)

    # ---------------------------------------------------------------- lookup
    def frame_for(self, node: ast.AST) -> ScopeFrame:
        """Return the innermost frame that lexically owns ``node``."""
        exact = self.by_node.get(id(node))
        if exact is not None:
            return exact
        line = getattr(node, "lineno", None)
        if line is None:
            return self.module
        index = bisect.bisect_right(self._span_starts, int(line)) - 1
        while index >= 0:
            start, end, frame = self._spans[index]
            if start <= int(line) <= end:
                return frame
            index -= 1
        return self.module

    def qualname_for(self, node: ast.AST) -> str:
        """Return the qualname of the scope owning ``node``."""
        return self.frame_for(node).qualname

    def enclosing_function(self, node: ast.AST) -> Optional[ScopeFrame]:
        """Return the nearest enclosing function or method frame."""
        cursor: Optional[ScopeFrame] = self.frame_for(node)
        while cursor is not None:
            if cursor.kind == "function":
                return cursor
            cursor = cursor.parent
        return None

    def enclosing_class(self, frame: Optional[ScopeFrame]) -> Optional[ScopeFrame]:
        """Return the nearest enclosing class frame for ``frame``."""
        cursor = frame
        while cursor is not None:
            if cursor.kind == "class":
                return cursor
            cursor = cursor.parent
        return None

    def resolve(self, name: str, frame: ScopeFrame, before_line: Optional[int] = None) -> Optional[Binding]:
        """Resolve ``name`` lexically, honouring ``global`` declarations."""
        if name in frame.globals_declared:
            return self.module.latest_binding(name, before_line)
        cursor: Optional[ScopeFrame] = frame
        while cursor is not None:
            binding = cursor.latest_binding(name, before_line)
            if binding is not None and binding.kind != "augassign":
                return binding
            cursor = cursor.parent
        return None

    def resolve_import(self, name: str, frame: ScopeFrame) -> str:
        """Resolve a possibly aliased import to its fully qualified module path."""
        cursor: Optional[ScopeFrame] = frame
        while cursor is not None:
            if name in cursor.imports:
                return cursor.imports[name]
            cursor = cursor.parent
        return ""

    def attrs_of(self, class_qualname: str) -> Dict[str, AttrInfo]:
        """Return the attribute table for a class qualname."""
        return self.class_attrs.get(class_qualname, {})

    def functions(self) -> List[ScopeFrame]:
        """Return every function frame in source order."""
        return [frame for frame in self.frames if frame.kind == "function"]

    def locks_in(self, class_qualname: str) -> Tuple[str, ...]:
        """Return the attribute names that hold synchronisation primitives."""
        return tuple(
            name
            for name, info in self.attrs_of(class_qualname).items()
            if info.lock_factory
        )


class _ScopeBuilder(ast.NodeVisitor):
    """Single-pass builder that records scopes, bindings, imports and attributes."""

    def __init__(self, owner: ScopeTree) -> None:
        self.owner = owner
        self.stack: List[ScopeFrame] = [owner.module]

    def build(self) -> None:
        """Populate the scope tree from the parsed module root."""
        self.visit(self.owner.tree)

    # ---------------------------------------------------------------- helpers
    @property
    def current(self) -> ScopeFrame:
        """The frame currently being populated."""
        return self.stack[-1]

    def _child_qualname(self, name: str) -> str:
        parent = self.current
        if parent.kind == "module":
            return name
        return f"{parent.qualname}.{name}"

    def _add_binding(
        self,
        name: str,
        node: ast.AST,
        kind: str,
        value: Optional[ast.AST] = None,
        annotation: str = "",
        mutable_default: bool = False,
        default_is_none: bool = False,
    ) -> None:
        self.current.add_binding(
            Binding(
                name=name,
                lineno=getattr(node, "lineno", self.current.lineno),
                kind=kind,
                node=node,
                value=value,
                annotation=annotation,
                mutable_default=mutable_default,
                default_is_none=default_is_none,
            )
        )

    def _record_target(
        self,
        target: ast.AST,
        value: Optional[ast.AST],
        kind: str = "assign",
        annotation: str = "",
    ) -> None:
        if isinstance(target, ast.Name):
            self._add_binding(target.id, target, kind, value=value, annotation=annotation)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._record_target(element, None, kind, annotation)
            return
        if isinstance(target, ast.Starred):
            self._record_target(target.value, value, kind, annotation)
            return
        if isinstance(target, ast.Attribute):
            self._record_attribute(target, value, annotation)
            return
        if isinstance(target, ast.Subscript):
            base = target.value
            if isinstance(base, ast.Name):
                self._add_binding(
                    base.id,
                    base,
                    "itemwrite",
                    value=target,
                    annotation=annotation,
                )

    def _record_attribute(
        self,
        target: ast.Attribute,
        value: Optional[ast.AST],
        annotation: str = "",
    ) -> None:
        if not isinstance(target.value, ast.Name) or target.value.id != "self":
            return
        class_frame = self.owner.enclosing_class(self.current)
        if class_frame is None:
            return
        table = self.owner.class_attrs.setdefault(class_frame.qualname, {})
        info = table.get(target.attr)
        lock_factory = _is_lock_factory(value)
        if info is None:
            info = AttrInfo(
                name=target.attr,
                lineno=getattr(target, "lineno", 0),
                annotation=annotation,
                assigned_none=isinstance(value, ast.Constant) and value.value is None,
                lock_factory=lock_factory,
                owner=class_frame.qualname,
            )
            table[target.attr] = info
        else:
            if annotation and not info.annotation:
                info.annotation = annotation
            if isinstance(value, ast.Constant) and value.value is None:
                info.assigned_none = True
            if lock_factory:
                info.lock_factory = True

    def _record_class_body_target(
        self, target: ast.AST, value: Optional[ast.AST], annotation: str
    ) -> None:
        if isinstance(target, ast.Name) and self.current.kind == "class":
            table = self.owner.class_attrs.setdefault(self.current.qualname, {})
            table.setdefault(
                target.id,
                AttrInfo(
                    name=target.id,
                    lineno=getattr(target, "lineno", 0),
                    annotation=annotation,
                    assigned_none=isinstance(value, ast.Constant) and value.value is None,
                    lock_factory=_is_lock_factory(value),
                    owner=self.current.qualname,
                ),
            )

    # ---------------------------------------------------------------- scopes
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            qualified = alias.name if alias.asname else alias.name.split(".")[0]
            self.current.imports[bound] = qualified
            self.owner.imports.setdefault(bound, qualified)
            self._add_binding(bound, node, "import")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            bound = alias.asname or alias.name
            qualified = f"{module}.{alias.name}" if module else alias.name
            self.current.imports[bound] = qualified
            self.owner.imports.setdefault(bound, qualified)
            self._add_binding(bound, node, "import")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_target(target, node.value)
            self._record_class_body_target(target, node.value, "")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        annotation = source_of(node.annotation)
        self._record_target(node.target, node.value, annotation=annotation)
        self._record_class_body_target(node.target, node.value, annotation)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._record_target(node.target, node.value, kind="augassign")
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._record_target(node.target, node.value)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._record_target(node.target, None, kind="loop")
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._record_target(node.target, None, kind="loop")
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._record_target(item.optional_vars, item.context_expr, kind="with")
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._record_target(item.optional_vars, item.context_expr, kind="with")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._add_binding(node.name, node, "except")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.current.globals_declared.update(node.names)
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.current.nonlocals_declared.update(node.names)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        self._add_binding(node.name, node, "class")
        frame = ScopeFrame(
            kind="class",
            name=node.name,
            qualname=self._child_qualname(node.name),
            node=node,
            lineno=node.lineno,
            parent=self.current,
            decorators=tuple(source_of(item) for item in node.decorator_list),
        )
        self.current.children.append(frame)
        self.owner.add_frame(frame)
        self.owner.class_attrs.setdefault(frame.qualname, {})
        self.stack.append(frame)
        try:
            self.generic_visit(node)
        finally:
            self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def _visit_function(self, node: ast.AST, is_async: bool) -> None:
        function_node: ast.FunctionDef | ast.AsyncFunctionDef = node  # type: ignore[assignment]
        decorators = tuple(source_of(item) for item in function_node.decorator_list)
        for decorator in function_node.decorator_list:
            self.visit(decorator)
        arguments = function_node.args
        for default in list(arguments.defaults) + [
            item for item in arguments.kw_defaults if item is not None
        ]:
            self.visit(default)
        for annotation_host in (
            list(arguments.posonlyargs)
            + list(arguments.args)
            + list(arguments.kwonlyargs)
            + ([arguments.vararg] if arguments.vararg else [])
            + ([arguments.kwarg] if arguments.kwarg else [])
        ):
            if annotation_host.annotation is not None:
                self.visit(annotation_host.annotation)
        if function_node.returns is not None:
            self.visit(function_node.returns)

        self._add_binding(function_node.name, function_node, "function")
        is_method = self.current.kind == "class"
        frame = ScopeFrame(
            kind="function",
            name=function_node.name,
            qualname=self._child_qualname(function_node.name),
            node=function_node,
            lineno=function_node.lineno,
            parent=self.current,
            is_async=is_async,
            is_method=is_method,
            is_staticmethod="staticmethod" in decorators,
            is_classmethod="classmethod" in decorators,
            decorators=decorators,
            return_annotation=source_of(function_node.returns),
            owner_class=self.current.qualname if is_method else "",
        )
        frame.returns_optional = annotation_is_optional(frame.return_annotation)
        self.current.children.append(frame)
        self.owner.add_frame(frame)
        self.stack.append(frame)
        try:
            positional = list(arguments.posonlyargs) + list(arguments.args)
            defaults = list(arguments.defaults)
            default_offset = len(positional) - len(defaults)
            for index, argument in enumerate(positional):
                default = defaults[index - default_offset] if index >= default_offset else None
                self._add_parameter(frame, argument, default)
            if arguments.vararg is not None:
                self._add_parameter(frame, arguments.vararg, None, kind="vararg")
            for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
                self._add_parameter(frame, argument, default, kind="kwonly")
            if arguments.kwarg is not None:
                self._add_parameter(frame, arguments.kwarg, None, kind="kwarg")
            for statement in function_node.body:
                self.visit(statement)
        finally:
            self.stack.pop()

    def _add_parameter(
        self,
        frame: ScopeFrame,
        argument: ast.arg,
        default: Optional[ast.AST],
        kind: str = "param",
    ) -> None:
        """Record a parameter with its default metadata."""
        mutable = False
        none_default = False
        if default is not None:
            mutable = _is_mutable_literal(default)
            none_default = isinstance(default, ast.Constant) and default.value is None
        frame.add_binding(
            Binding(
                name=argument.arg,
                lineno=getattr(argument, "lineno", frame.lineno),
                kind=kind,
                node=argument,
                value=default,
                annotation=source_of(argument.annotation),
                mutable_default=mutable,
                default_is_none=none_default,
            )
        )

    def visit_Lambda(self, node: ast.Lambda) -> None:
        frame = ScopeFrame(
            kind="lambda",
            name="<lambda>",
            qualname=f"{self.current.qualname}.<lambda>",
            node=node,
            lineno=node.lineno,
            parent=self.current,
        )
        self.current.children.append(frame)
        self.owner.add_frame(frame)
        self.stack.append(frame)
        try:
            self.generic_visit(node)
        finally:
            self.stack.pop()


def _is_mutable_literal(node: ast.AST) -> bool:
    """Return True for defaults that survive across calls (containers and factories)."""
    if isinstance(node, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    if isinstance(node, ast.Call):
        callee = _callable_name(node.func)
        if callee in _MUTABLE_DEFAULT_FACTORIES:
            return True
    return False


def _is_lock_factory(node: Optional[ast.AST]) -> bool:
    """Return True when a value expression constructs a synchronisation primitive."""
    if not isinstance(node, ast.Call):
        return False
    callee = _callable_name(node.func)
    return callee in _LOCK_FACTORY_NAMES or callee.endswith("Lock")


def _callable_name(node: ast.AST) -> str:
    """Return the trailing identifier of a call target (``threading.Lock`` -> ``Lock``)."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def build_scope_tree(path: str, source: str, tree: ast.Module) -> ScopeTree:
    """Construct the scope index for a parsed module."""
    return ScopeTree(path, source, tree)
