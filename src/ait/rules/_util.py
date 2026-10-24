"""Shared AST helpers used by the rule catalog.

These helpers exist so each rule can stay focused on its detection strategy.
Every walk here refuses to descend into nested ``def``/``class``/``lambda``
bodies by default, which prevents a parent function from inheriting concurrency
or mutation evidence that belongs to a different scope.
"""

from __future__ import annotations

import ast
from typing import Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def collect_body(node: ast.AST) -> Tuple[List[ast.AST], Dict[int, ast.AST]]:
    """Return every node in a scope body plus a parent map.

    Nested scopes are returned as opaque leaves: the traversal records the node
    itself but never its children, keeping evidence strictly scope-local.
    """
    nodes: List[ast.AST] = []
    parents: Dict[int, ast.AST] = {}
    stack: List[ast.AST] = [node]
    while stack:
        current = stack.pop()
        nodes.append(current)
        for child in ast.iter_child_nodes(current):
            if isinstance(child, _NESTED_SCOPES):
                continue
            parents[id(child)] = current
            stack.append(child)
    return nodes, parents


def ancestors(node: ast.AST, parents: Mapping[int, ast.AST]) -> Iterator[ast.AST]:
    """Yield parents from nearest to outermost."""
    cursor = parents.get(id(node))
    while cursor is not None:
        yield cursor
        cursor = parents.get(id(cursor))


def statement_ancestor(
    node: ast.AST, parents: Mapping[int, ast.AST]
) -> Optional[ast.stmt]:
    """Return the nearest enclosing statement, including ``node`` when it is one."""
    if isinstance(node, ast.stmt):
        return node
    for candidate in ancestors(node, parents):
        if isinstance(candidate, ast.stmt):
            return candidate
    return None


def is_none_literal(node: Optional[ast.AST]) -> bool:
    """Return True for a literal ``None``."""
    return isinstance(node, ast.Constant) and node.value is None


def call_keyword(call: ast.Call, name: str) -> Optional[ast.AST]:
    """Return the value of a keyword argument, or ``None`` when absent."""
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def is_pipe_value(node: Optional[ast.AST]) -> bool:
    """Return True for ``subprocess.PIPE`` and its aliases."""
    if node is None:
        return False
    text = ast.unparse(node) if hasattr(ast, "unparse") else ""
    return text.endswith("PIPE")


def tail_name(text: str) -> str:
    """Return the trailing identifier of a dotted call target."""
    return text.rsplit(".", 1)[-1] if text else ""


def module_level_calls(tree: ast.Module) -> List[Tuple[ast.Call, bool]]:
    """Collect calls evaluated at import time, flagging ``__main__``-guarded ones.

    Descending is skipped for function, class and lambda bodies because their
    contents are not executed during import.
    """
    collected: List[Tuple[ast.Call, bool]] = []

    def walk(node: ast.AST, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED_SCOPES):
                continue
            if isinstance(child, ast.If) and is_main_guard(child.test):
                for statement in child.body:
                    walk(statement, True)
                for statement in child.orelse:
                    walk(statement, guarded)
                continue
            if isinstance(child, ast.Call):
                collected.append((child, guarded))
            walk(child, guarded)

    walk(tree, False)
    return collected


def is_main_guard(test: ast.AST) -> bool:
    """Return True for ``if __name__ == "__main__":``."""
    if not isinstance(test, ast.Compare):
        return False
    left = test.left
    if not (isinstance(left, ast.Name) and left.id == "__name__"):
        return False
    return any(
        isinstance(item, ast.Constant) and item.value == "__main__"
        for item in test.comparators
    )


def sorted_by_position(nodes: Sequence[ast.AST]) -> List[ast.AST]:
    """Order nodes by source position so diagnostics stay deterministic."""
    return sorted(
        nodes,
        key=lambda item: (
            getattr(item, "lineno", 0),
            getattr(item, "col_offset", 0),
        ),
    )


def is_docstring_statement(node: ast.AST) -> bool:
    """Return True when the statement is a bare string expression."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
