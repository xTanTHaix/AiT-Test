"""Shared execution context and evidence toolkit handed to every rule.

Rules are deliberately thin: they recognise a pattern, then call into this
context to attach coordinates, causation chains, witnesses and verified patches.
Keeping that machinery here means all 13 rules emit identical, trustworthy
structures and no rule can quietly invent an unsupported fix.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .flow.call_graph import CallGraph, FunctionInfo
from .flow.scope_tree import ScopeFrame, ScopeTree, source_of
from .flow.witness import WitnessRequest, build_witness
from .models import (
    CausationStep,
    Diagnostic,
    Evidence,
    Location,
    Patch,
    Severity,
    Witness,
    apply_patch,
)

_SUPPRESS_RE = re.compile(r"#\s*ait:\s*disable\s*=\s*([A-Za-z0-9\-,\s_]+)")
_EXIT_NODES = (ast.Return, ast.Raise, ast.Continue, ast.Break)


@dataclass(slots=True)
class RuleContext:
    """Everything a rule needs to inspect one module and prove its findings."""

    path: str
    source: str
    tree: ast.Module
    lines: List[str]
    scopes: ScopeTree
    graph: CallGraph
    options: Mapping[str, object] = field(default_factory=dict)
    module_qualname: str = "<module>"
    _suppression: Dict[int, Set[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.lines and self.source:
            self.lines = self.source.splitlines(True)
        self._suppression = _parse_suppressions(self.lines)

    # ------------------------------------------------------------- coordinates
    @property
    def newline(self) -> str:
        """Return the line ending used by this file (``\\r\\n`` on Windows sources)."""
        return "\r\n" if "\r\n" in self.source else "\n"

    def loc(self, node: ast.AST) -> Location:
        """Build a :class:`Location` for any AST node."""
        return Location(
            path=self.path,
            line=max(1, int(getattr(node, "lineno", 1) or 1)),
            col=int(getattr(node, "col_offset", 0) or 0),
            end_line=int(getattr(node, "end_lineno", 0) or 0),
            end_col=int(getattr(node, "end_col_offset", 0) or 0),
        )

    def frame(self, node: ast.AST) -> ScopeFrame:
        """Return the innermost lexical frame owning ``node``."""
        return self.scopes.frame_for(node)

    def function_info(self, node: ast.AST) -> Optional[FunctionInfo]:
        """Return the resolved call-graph record for the function owning ``node``."""
        return self.graph.functions.get(self.scopes.qualname_for(node))

    def function_label(self, node: ast.AST) -> str:
        """Render ``name(params)`` for the enclosing function, or ``<module>``."""
        info = self.function_info(node)
        if info is not None:
            return info.signature_label()
        frame = self.scopes.enclosing_function(node)
        return frame.label if frame is not None else "<module>"

    def qualname(self, node: ast.AST) -> str:
        """Return the qualname of the scope owning ``node``."""
        return self.scopes.qualname_for(node)

    def line_text(self, lineno: int) -> str:
        """Return one source line (one-based) without its trailing newline."""
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].rstrip("\r\n")
        return ""

    def indent_at(self, lineno: int) -> str:
        """Return the leading whitespace of a source line."""
        text = self.line_text(lineno)
        return text[: len(text) - len(text.lstrip())]

    def statement_span(self, node: ast.AST) -> Tuple[int, int]:
        """Return the inclusive one-based line span of a statement node."""
        start = max(1, int(getattr(node, "lineno", 1) or 1))
        end = int(getattr(node, "end_lineno", start) or start)
        return start, max(start, end)

    def statement_text(self, node: ast.AST) -> str:
        """Return the exact source text of a statement node."""
        start, end = self.statement_span(node)
        return "".join(self.lines[start - 1 : end])

    def code_slice(self, start: int, end: int, context: int = 2) -> List[Tuple[int, str, bool]]:
        """Return ``(lineno, text, is_target)`` rows around a span for rendering."""
        first = max(1, start - context)
        last = min(len(self.lines), end + context)
        return [
            (lineno, self.line_text(lineno), start <= lineno <= end)
            for lineno in range(first, last + 1)
        ]

    # ------------------------------------------------------------- evidence
    def evidence(self, node: ast.AST, note: str) -> Evidence:
        """Wrap a node and explanation into a serialisable evidence record."""
        return Evidence(location=self.loc(node), note=note)

    def step(self, index: int, node: ast.AST, note: str) -> CausationStep:
        """Build one causation hop anchored on ``node``."""
        return CausationStep(index=index, location=self.loc(node), note=note)

    def diagnose(
        self,
        *,
        rule_id: str,
        severity: Severity,
        confidence: float,
        message: str,
        node: ast.AST,
        evidence: Sequence[Evidence],
        symbol: str = "",
        function: str = "",
        causation: Sequence[CausationStep] = (),
        witness: Optional[Witness] = None,
        patch: Optional[Patch] = None,
    ) -> Diagnostic:
        """Assemble a fully evidenced diagnostic.

        ``evidence`` is never optional: a rule that cannot point at least one
        concrete observation is not allowed to speak.
        """
        location = self.loc(node)
        return Diagnostic(
            rule_id=rule_id,
            severity=severity,
            confidence=confidence,
            message=message,
            location=location,
            function=function or self.function_label(node),
            symbol=symbol,
            evidence=tuple(evidence),
            causation=tuple(causation),
            witness=witness,
            patch=patch,
            snippet=tuple(
                (lineno, text)
                for lineno, text, _is_target in self.code_slice(
                    location.line, location.end_line or location.line
                )
            ),
        )

    def make_witness(
        self,
        rule_id: str,
        node: ast.AST,
        symbol: str = "",
        detail: Optional[Mapping[str, str]] = None,
    ) -> Optional[Witness]:
        """Build and syntax-validate a counterexample snippet for a finding."""
        request = WitnessRequest(
            rule_id=rule_id,
            path=self.path,
            lineno=self.loc(node).line,
            symbol=symbol,
            function=self.function_label(node),
            detail=dict(detail or {}),
        )
        return build_witness(request)

    # ------------------------------------------------------------- call helpers
    def call_text(self, expression: ast.AST) -> str:
        """Render a call target with import aliases expanded to module paths."""
        text = source_of(expression)
        if not text:
            return ""
        root = _root_name(expression)
        if not root:
            return text
        frame = self.frame(expression)
        resolved = self.scopes.resolve_import(root, frame)
        if resolved and resolved != root:
            return resolved + text[len(root) :]
        return text

    def resolve_alias(self, name: str, node: ast.AST) -> str:
        """Resolve a bare identifier through the file's import table."""
        return self.scopes.resolve_import(name, self.frame(node)) or name

    def is_self_attribute(self, expression: ast.AST) -> bool:
        """Return True for ``self.<attr>`` style attribute expressions."""
        return (
            isinstance(expression, ast.Attribute)
            and isinstance(expression.value, ast.Name)
            and expression.value.id == "self"
        )

    # ------------------------------------------------------------- flow guards
    def guard_dominates(
        self,
        frame: ScopeFrame,
        name: str,
        line: int,
        node: Optional[ast.AST] = None,
        parents: Optional[Mapping[int, ast.AST]] = None,
    ) -> bool:
        """Return True when a guard protects ``name`` at ``line``.

        Three shapes are recognised, and nothing else:

        1. **Containment** — the usage sits inside a branch whose test proves the
           value is not ``None`` (``if x is not None:``, ``while x is not None:``,
           ``x if isinstance(x, T) else ...``).
        2. **Short-circuit** — the usage sits in a later operand of ``x and
           x.attr`` or ``x is None or x.attr``.
        3. **Early exit** — a preceding ``if x is None: return`` (or ``raise``,
           ``continue``, ``break``, ``sys.exit()``) cannot fall through.

        Anything more creative is deliberately *not* trusted: a guard the engine
        misreads would hide a real crash instead of reporting it.
        """
        if node is not None:
            table = parents if parents is not None else _parent_map(frame.node)
            cursor = table.get(id(node))
            while cursor is not None:
                if _inline_guard(cursor, node, name):
                    return True
                if cursor is frame.node:
                    break
                cursor = table.get(id(cursor))
        for candidate in ast.walk(frame.node):
            if isinstance(candidate, ast.Assert):
                if _is_guard_test(candidate.test, name):
                    end = int(
                        getattr(candidate, "end_lineno", candidate.lineno) or candidate.lineno
                    )
                    if end < line:
                        return True
                continue
            if not isinstance(candidate, (ast.If, ast.While)):
                continue
            if not _is_exit_test(candidate.test, name):
                continue
            if not _always_exits(candidate.body):
                continue
            end = int(getattr(candidate, "end_lineno", candidate.lineno) or candidate.lineno)
            if end < line:
                return True
        return False

    def reassigned_since_guard(
        self, frame: ScopeFrame, name: str, since_line: int, before_line: int
    ) -> bool:
        """Return True when ``name`` was rebound between two lines.

        A rebinding invalidates any earlier guard, which is exactly the case
        where a naive analyzer reports a phantom nullability defect.
        """
        for binding in frame.bindings.get(name, ()):
            if since_line < binding.lineno <= before_line:
                return True
        return False

    # ------------------------------------------------------------- suppression
    def suppressed_rules_at(self, lineno: int) -> Set[str]:
        """Return rule ids suppressed on a given line (``ALL`` covers every rule)."""
        return set(self._suppression.get(lineno, ()))

    def is_suppressed(self, rule_id: str, lineno: int) -> bool:
        """Return True when an inline directive silences this rule on this line.

        Both the canonical id (``AIT-H01``) and the bare suffix (``H01``) are
        accepted: a reviewer who types the rule number they saw in the output must
        not silently get no suppression at all.
        """
        markers = self.suppressed_rules_at(lineno)
        if "ALL" in markers or rule_id in markers:
            return True
        bare = rule_id.rsplit("-", 1)[-1]
        return bare != rule_id and bare in markers

    # ------------------------------------------------------------- patching
    def statement_patch(
        self, node: ast.AST, replacement: str, rationale: str
    ) -> Optional[Patch]:
        """Build a patch that replaces a whole statement, verified by re-parsing."""
        start, end = self.statement_span(node)
        original = "".join(self.lines[start - 1 : end])
        if not original.strip():
            return None
        try:
            patch = Patch(
                start_line=start,
                end_line=end,
                original=original,
                replacement=replacement,
                rationale=rationale,
            )
        except ValueError:
            return None
        return patch if self._patch_parses(patch) else None

    def insertion_patch(
        self, node: ast.AST, inserted: Sequence[str], rationale: str
    ) -> Optional[Patch]:
        """Build a patch that inserts indented lines immediately before ``node``."""
        indent = self.indent_at(int(getattr(node, "lineno", 1) or 1))
        head = "".join(f"{indent}{line}{self.newline}" for line in inserted)
        return self.statement_patch(node, head + self.statement_text(node), rationale)

    def line_patch(
        self, lineno: int, old_fragment: str, new_fragment: str, rationale: str
    ) -> Optional[Patch]:
        """Build a single-line fragment replacement, verified by re-parsing."""
        text = self.line_text(lineno)
        if old_fragment not in text:
            return None
        updated = text.replace(old_fragment, new_fragment, 1)
        if updated == text:
            return None
        return self.statement_patch_span(
            lineno, lineno, updated + self.newline, rationale
        )

    def statement_patch_span(
        self, start: int, end: int, replacement: str, rationale: str
    ) -> Optional[Patch]:
        """Build a span patch from explicit coordinates, verified by re-parsing."""
        original = "".join(self.lines[start - 1 : end])
        if not original.strip():
            return None
        try:
            patch = Patch(
                start_line=start,
                end_line=end,
                original=original,
                replacement=replacement,
                rationale=rationale,
            )
        except ValueError:
            return None
        return patch if self._patch_parses(patch) else None

    def _patch_parses(self, patch: Patch) -> bool:
        """Reject any patch whose result is not syntactically valid Python.

        This is the last line of defence before a suggestion reaches a user or a
        file: no matter how confident a rule is, a patch that breaks the module
        is discarded instead of displayed.
        """
        try:
            candidate = apply_patch(self.source, patch)
            ast.parse(candidate, filename=self.path)
        except (SyntaxError, ValueError, RecursionError):
            return False
        return True


# --------------------------------------------------------------------- helpers
def _root_name(expression: ast.AST) -> str:
    """Return the leftmost identifier of an attribute chain."""
    cursor = expression
    while isinstance(cursor, ast.Attribute):
        cursor = cursor.value
    return cursor.id if isinstance(cursor, ast.Name) else ""


def _expr_name(expression: ast.AST) -> str:
    """Return a comparable name for a Name or Attribute expression."""
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        return source_of(expression)
    return ""


def _matches(expression: ast.AST, name: str) -> bool:
    """Return True when an expression refers to ``name``.

    A bare symbol such as ``worker`` also matches ``self.worker`` so that guards
    written against an attribute still protect the attribute's own name.
    """
    text = _expr_name(expression)
    if not text:
        return False
    if text == name:
        return True
    if "." not in name and text.endswith(f".{name}"):
        return True
    return False


def _mentions(node: ast.AST, name: str) -> bool:
    """Return True when an expression references ``name`` (bare or as an attribute)."""
    return any(_matches(child, name) for child in ast.walk(node))


def _parent_map(root: ast.AST) -> Dict[int, ast.AST]:
    """Return a child-id to parent map for a subtree."""
    table: Dict[int, ast.AST] = {}
    for parent in ast.walk(root):
        for child in ast.iter_child_nodes(parent):
            table[id(child)] = parent
    return table


def _contains(container: Optional[ast.AST], node: ast.AST) -> bool:
    """Identity-based containment test (``ast.walk`` never synthesises nodes)."""
    if container is None:
        return False
    if container is node:
        return True
    return any(child is node for child in ast.walk(container))


def _contains_any(container: object, node: ast.AST) -> bool:
    """Containment test that also accepts a list of statements."""
    if isinstance(container, list):
        return any(_contains(item, node) for item in container)
    if isinstance(container, ast.AST):
        return _contains(container, node)
    return False


def _isinstance_call(test: ast.AST, name: str) -> bool:
    """Return True for ``isinstance(name, T)`` (which also proves non-None)."""
    if not isinstance(test, ast.Call):
        return False
    if not isinstance(test.func, ast.Name) or test.func.id != "isinstance":
        return False
    return bool(test.args) and _matches(test.args[0], name)


def _none_comparison_kind(test: ast.AST, name: str) -> str:
    """Classify ``x is None`` style comparisons as ``is_none`` or ``is_not_none``."""
    if not isinstance(test, ast.Compare):
        return ""
    participants = [test.left, *test.comparators]
    has_name = any(_matches(item, name) for item in participants)
    has_none = any(
        isinstance(item, ast.Constant) and item.value is None for item in participants
    )
    if not (has_name and has_none):
        return ""
    for operator in test.ops:
        if isinstance(operator, (ast.Is, ast.Eq)):
            return "is_none"
        if isinstance(operator, (ast.IsNot, ast.NotEq)):
            return "is_not_none"
    return ""


def _is_guard_test(test: ast.AST, name: str) -> bool:
    """Return True when a *truthy* test result proves ``name`` is not None."""
    if isinstance(test, (ast.Name, ast.Attribute)) and _matches(test, name):
        return True
    if _isinstance_call(test, name):
        return True
    if isinstance(test, ast.Compare):
        return _none_comparison_kind(test, name) == "is_not_none"
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        operand = test.operand
        if isinstance(operand, ast.Compare):
            return _none_comparison_kind(operand, name) == "is_none"
        return False
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(_is_guard_test(value, name) for value in test.values)
    return False


def _is_exit_test(test: ast.AST, name: str) -> bool:
    """Return True when a *truthy* test means ``name`` cannot be used afterwards.

    ``if x is None: return`` and ``if not isinstance(x, str): raise`` both leave
    the remainder of the scope with a value that is definitely not ``None``.
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        operand = test.operand
        if isinstance(operand, (ast.Name, ast.Attribute)) and _matches(operand, name):
            return True
        if _isinstance_call(operand, name):
            return True
        if isinstance(operand, ast.Compare):
            return _none_comparison_kind(operand, name) == "is_not_none"
        return False
    if isinstance(test, ast.Compare):
        return _none_comparison_kind(test, name) == "is_none"
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return any(_is_exit_test(value, name) for value in test.values)
    return False


def _operand_index(operator: ast.BoolOp, node: ast.AST) -> int:
    """Return the index of the operand containing ``node``, or ``-1``."""
    for index, value in enumerate(operator.values):
        if _contains(value, node):
            return index
    return -1


def _inline_guard(ancestor: ast.AST, node: ast.AST, name: str) -> bool:
    """Return True when ``ancestor`` guards ``node`` without leaving the line."""
    if isinstance(ancestor, ast.BoolOp):
        index = _operand_index(ancestor, node)
        if index <= 0:
            return False
        preceding = ancestor.values[:index]
        if isinstance(ancestor.op, ast.And):
            return any(_is_guard_test(value, name) for value in preceding)
        if isinstance(ancestor.op, ast.Or):
            return any(_is_exit_test(value, name) for value in preceding)
        return False
    if isinstance(ancestor, ast.IfExp):
        if _contains(ancestor.body, node):
            return _is_guard_test(ancestor.test, name)
        if _contains(ancestor.orelse, node):
            return _is_exit_test(ancestor.test, name)
        return False
    if isinstance(ancestor, (ast.If, ast.While)):
        if _contains_any(ancestor.body, node):
            return _is_guard_test(ancestor.test, name)
        return False
    return False



def _always_exits(body: Sequence[ast.stmt]) -> bool:
    """Return True when a block cannot fall through to the following statements."""
    if not body:
        return False
    for statement in body:
        if isinstance(statement, _EXIT_NODES):
            return True
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            callee = source_of(statement.value.func)
            if callee.endswith("exit") or callee.endswith("_exit"):
                return True
        if isinstance(statement, ast.If):
            if _always_exits(statement.body) and _always_exits(statement.orelse):
                return True
    return False


def _parse_suppressions(lines: Sequence[str]) -> Dict[int, Set[str]]:
    """Parse inline ``# ait: disable=AIT-X01`` directives into a line index."""
    table: Dict[int, Set[str]] = {}
    for index, text in enumerate(lines, start=1):
        match = _SUPPRESS_RE.search(text)
        if not match:
            continue
        rules = table.setdefault(index, set())
        for chunk in match.group(1).split(","):
            marker = chunk.strip().upper()
            if not marker:
                continue
            rules.add("ALL" if marker in ("ALL", "*") else marker)
    return table
