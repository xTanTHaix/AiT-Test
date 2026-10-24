"""Deep path, scope and invariant trackers.

The modules here build the evidence substrate that rules consume: a lexical
scope index, an inter-procedural call graph with spawn/lock topology, a backward
value tracer and a counterexample synthesizer.
"""

from __future__ import annotations

from .call_graph import CallGraph, CallSite, FunctionInfo, LockRegion, WriteRecord, build_call_graph
from .data_flow import describe_binding, optional_sources, trace_symbol
from .scope_tree import (
    AttrInfo,
    Binding,
    ScopeFrame,
    ScopeTree,
    build_scope_tree,
    source_of,
)
from .witness import WitnessRequest, build_witness, supported_rules

__all__ = [
    "AttrInfo",
    "Binding",
    "CallGraph",
    "CallSite",
    "FunctionInfo",
    "LockRegion",
    "ScopeFrame",
    "ScopeTree",
    "WitnessRequest",
    "WriteRecord",
    "build_call_graph",
    "build_scope_tree",
    "build_witness",
    "describe_binding",
    "optional_sources",
    "source_of",
    "supported_rules",
    "trace_symbol",
]
