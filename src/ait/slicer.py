"""Compact sub-AST context slices for LLM consumers.

A language model that receives a whole file burns tokens on unrelated code and
loses the thread of the symbol it is reasoning about. This module returns exactly
one symbol: its source, its variable table, its outgoing calls and (bounded) the
bodies of its callees.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from .engine import UsageError, decode_source
from .flow.call_graph import build_call_graph
from .flow.scope_tree import ScopeFrame, build_scope_tree, source_of


def _read_source(path: Path) -> str:
    """Read a Python source file with BOM-safe decoding."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UsageError(f"Cannot read {path}: {exc}") from exc
    return decode_source(raw)


def _resolve_frame(scopes, symbol_name: str) -> ScopeFrame:
    """Resolve a symbol name to exactly one lexical frame."""
    exact = scopes.by_qualname.get(symbol_name)
    if exact is not None:
        return exact
    matches = [
        frame
        for frame in scopes.frames
        if frame.kind in ("function", "class") and frame.name == symbol_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        qualified = ", ".join(frame.qualname for frame in matches)
        raise UsageError(
            f"Symbol `{symbol_name}` is ambiguous; qualify it with a class prefix "
            f"(candidates: {qualified})"
        )
    suffix = [
        frame
        for frame in scopes.frames
        if frame.kind in ("function", "class")
        and frame.qualname.endswith(f".{symbol_name}")
    ]
    if len(suffix) == 1:
        return suffix[0]
    raise UsageError(f"Symbol `{symbol_name}` was not found in this module")


def _variable_table(frame: ScopeFrame) -> List[Dict[str, Any]]:
    """Summarise the frame's bindings as an ordered variable table."""
    rows: List[Dict[str, Any]] = []
    for name, bindings in frame.bindings.items():
        latest = bindings[-1]
        rows.append(
            {
                "name": name,
                "kind": latest.kind,
                "annotation": latest.annotation,
                "line": latest.lineno,
                "value": source_of(latest.value) if latest.value is not None else "",
            }
        )
    rows.sort(key=lambda item: (int(item["line"]), str(item["name"])))
    return rows


def _slice_text(lines: Sequence[str], start: int, end: int) -> str:
    """Return the exact source text for a one-based inclusive line span."""
    return "".join(f"{line}\n" for line in lines[start - 1 : end]).rstrip("\n")


def build_slice(
    file_path: str | Path,
    symbol_name: str,
    depth: int = 1,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a token-efficient structural slice around one symbol."""
    path = Path(file_path).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise UsageError(f"File is not accessible: {file_path}") from exc
    if not resolved.is_file():
        raise UsageError(f"Expected a Python file, received: {file_path}")
    root_dir = Path(root).resolve() if root is not None else resolved.parent
    try:
        relative = resolved.relative_to(root_dir).as_posix()
    except ValueError:
        relative = resolved.name

    source = _read_source(resolved)
    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError as exc:
        raise UsageError(
            f"{relative} does not parse: {exc.msg} (line {exc.lineno})"
        ) from exc
    lines = source.splitlines()
    scopes = build_scope_tree(relative, source, tree)
    graph = build_call_graph(scopes)
    frame = _resolve_frame(scopes, symbol_name)

    start = max(1, int(getattr(frame.node, "lineno", 1) or 1))
    end = int(getattr(frame.node, "end_lineno", start) or start)
    info = graph.functions.get(frame.qualname)
    calls: List[Dict[str, Any]] = []
    if info is not None:
        for site in info.calls:
            calls.append(
                {
                    "callee": site.label,
                    "resolved": site.callee_qualname,
                    "line": site.lineno,
                }
            )
    callers = [
        {
            "caller": qualname,
            "line": site.lineno,
        }
        for qualname, candidate in graph.functions.items()
        for site in candidate.calls
        if site.callee_qualname == frame.qualname
    ]

    expanded: List[Dict[str, Any]] = []
    if depth > 0:
        seen: Set[str] = {frame.qualname}
        frontier = [site.callee_qualname for site in (info.calls if info else [])]
        for level in range(1, depth + 1):
            next_frontier: List[str] = []
            for qualname in frontier:
                if not qualname or qualname in seen:
                    continue
                seen.add(qualname)
                callee_frame = scopes.by_qualname.get(qualname)
                callee_info = graph.functions.get(qualname)
                if callee_frame is None or callee_info is None:
                    continue
                callee_start = max(1, int(getattr(callee_frame.node, "lineno", 1) or 1))
                callee_end = int(getattr(callee_frame.node, "end_lineno", callee_start) or callee_start)
                expanded.append(
                    {
                        "qualname": qualname,
                        "level": level,
                        "signature": callee_info.signature_label(),
                        "lineno": callee_start,
                        "end_lineno": callee_end,
                        "code": _slice_text(lines, callee_start, callee_end),
                    }
                )
                next_frontier.extend(
                    site.callee_qualname for site in callee_info.calls
                )
            frontier = next_frontier

    return {
        "file": relative,
        "symbol": frame.name,
        "qualname": frame.qualname,
        "kind": frame.kind,
        "is_async": frame.is_async,
        "lineno": start,
        "end_lineno": end,
        "signature": info.signature_label() if info is not None else frame.name,
        "docstring": _docstring(frame.node),
        "code": _slice_text(lines, start, end),
        "variables": _variable_table(frame),
        "calls": calls,
        "callers": callers,
        "expanded": expanded,
        "depth": depth,
    }


def _docstring(node: ast.AST) -> str:
    """Return a node's docstring, tolerating forms ``ast`` refuses to inspect.

    ``ast.get_docstring`` raises ``TypeError`` for nodes that cannot own a
    docstring (a ``Lambda`` reached through ``outer.<lambda>`` is the common one),
    so callers must never pass a frame node straight through.
    """
    if not isinstance(
        node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ):
        return ""
    return ast.get_docstring(node, clean=False) or ""
