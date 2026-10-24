"""Backward value-origin tracing for causation paths.

A finding without a path is a rumour. This module walks assignments backwards
from the break point to the origin of the value, so every diagnostic can show
which statement introduced the risky value and how it travelled.
"""

from __future__ import annotations

import ast
from typing import List, Set, Tuple

from ..models import CausationStep, Location
from .scope_tree import Binding, ScopeFrame, ScopeTree, annotation_is_optional, source_of

MAX_TRACE_DEPTH = 6


def _location_of(path: str, node: ast.AST) -> Location:
    """Build a :class:`Location` from any AST node using one-based lines."""
    return Location(
        path=path,
        line=max(1, int(getattr(node, "lineno", 1) or 1)),
        col=int(getattr(node, "col_offset", 0) or 0),
        end_line=int(getattr(node, "end_lineno", 0) or 0),
        end_col=int(getattr(node, "end_col_offset", 0) or 0),
    )


def describe_binding(binding: Binding) -> str:
    """Render one human-readable hop for a binding.

    The note explains *why* the value is interesting rather than restating the
    assignment syntax: parameters record their declared type, calls record the
    producer, and literals are named explicitly so a reviewer can grep for them.
    """
    value = binding.value
    if binding.kind in ("param", "kwonly", "vararg", "kwarg"):
        declared = binding.annotation or "unannotated"
        suffix = " (default None)" if binding.default_is_none else ""
        return f"`{binding.name}` arrives as a parameter ({declared}){suffix}"
    if binding.kind == "import":
        return f"`{binding.name}` is bound by an import statement"
    if isinstance(value, ast.Constant):
        rendered = repr(value.value)
        return f"`{binding.name}` is assigned the literal {rendered}"
    if isinstance(value, ast.Call):
        callee = source_of(value.func)
        return f"`{binding.name}` receives the return value of `{callee}()`"
    if isinstance(value, ast.Attribute):
        return f"`{binding.name}` reads attribute `{source_of(value)}`"
    if isinstance(value, ast.Subscript):
        return f"`{binding.name}` reads item `{source_of(value)}`"
    if isinstance(value, ast.Name):
        return f"`{binding.name}` is an alias of `{value.id}`"
    rendered = source_of(value) if value is not None else binding.annotation
    return f"`{binding.name}` is bound to `{rendered or binding.kind}`"


def trace_symbol(
    scopes: ScopeTree,
    path: str,
    frame: ScopeFrame,
    symbol: str,
    before_line: int,
    max_depth: int = MAX_TRACE_DEPTH,
) -> List[CausationStep]:
    """Trace ``symbol`` backwards from ``before_line`` to its origin.

    Each step is ordered oldest-first so the rendered block reads as a causal
    chain. Cycles and unbounded alias chains are cut at ``max_depth``.
    """
    raw: List[CausationStep] = []
    seen: Set[Tuple[int, int]] = set()
    current_name = symbol
    current_line = max(1, before_line)
    current_frame = frame

    for _ in range(max_depth):
        binding = scopes.resolve(current_name, current_frame, current_line)
        if binding is None:
            break
        marker = (id(binding), current_line)
        if marker in seen:
            break
        seen.add(marker)
        raw.append(
            CausationStep(
                index=len(raw) + 1,
                location=_location_of(path, binding.node or current_frame.node),
                note=describe_binding(binding),
            )
        )
        value = binding.value
        if isinstance(value, ast.Name):
            current_name = value.id
            current_line = binding.lineno
            continue
        if isinstance(value, ast.Call):
            producer = _producer_note(scopes, value)
            if producer:
                raw.append(
                    CausationStep(
                        index=len(raw) + 1,
                        location=_location_of(path, value),
                        note=producer,
                    )
                )
            break
        break

    raw.reverse()
    return [
        CausationStep(index=index + 1, location=step.location, note=step.note)
        for index, step in enumerate(raw)
    ]


def _producer_note(scopes: ScopeTree, call: ast.Call) -> str:
    """Describe a producing call, naming an optional return type when one is declared."""
    name = source_of(call.func)
    tail = name.split(".")[-1]
    for frame in scopes.functions():
        if frame.name == tail:
            if frame.returns_optional:
                return (
                    f"`{name}()` is declared to return "
                    f"`{frame.return_annotation}`, so the result can be None"
                )
            return f"`{name}()` returns `{frame.return_annotation or 'an unannotated value'}`"
    lowered = name.lower()
    if lowered.endswith(".get") or lowered.endswith("get("):
        return f"`{name}()` returns None for a missing key"
    if tail in ("match", "search", "fullmatch") or tail in ("getenv", "get"):
        return f"`{name}()` returns None when nothing matches"
    return f"`{name}()` is the producer of this value"


def optional_sources(
    scopes: ScopeTree, path: str, frame: ScopeFrame, symbol: str, before_line: int
) -> Tuple[bool, List[CausationStep]]:
    """Return whether ``symbol`` can be None and the evidence path proving it.

    The decision only accepts positive evidence: an optional annotation, an
    explicit ``None`` literal, a producer known to return ``None``, or an alias
    that inherits one of those. Absence of evidence means "unknown", never "safe"
    and never "unsafe".
    """
    binding = scopes.resolve(symbol, frame, before_line)
    if binding is None:
        return False, []
    if not _binding_is_optional(scopes, frame, binding):
        return False, []
    return True, trace_symbol(scopes, path, frame, symbol, before_line)


def _binding_is_optional(
    scopes: ScopeTree, frame: ScopeFrame, binding: Binding, depth: int = 0
) -> bool:
    """Decide whether one binding can evaluate to ``None``, following aliases."""
    if depth > MAX_TRACE_DEPTH:
        return False
    if binding.default_is_none:
        return True
    value = binding.value
    if isinstance(value, ast.Constant) and value.value is None:
        return True
    if annotation_is_optional(binding.annotation):
        return True
    if isinstance(value, ast.Call):
        tail = source_of(value.func).split(".")[-1]
        if tail in ("get", "match", "search", "fullmatch", "getenv"):
            return True
        for candidate in scopes.functions():
            if candidate.name == tail and candidate.returns_optional:
                return True
        return False
    if isinstance(value, ast.Name):
        inner = scopes.resolve(value.id, frame, binding.lineno)
        if inner is None or inner is binding:
            return False
        return _binding_is_optional(scopes, frame, inner, depth + 1)
    return False
