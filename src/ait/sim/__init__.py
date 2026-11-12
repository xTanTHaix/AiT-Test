"""ChronoSim: Zero-dependency Deterministic Simulation Testing (DST) for Python.

Provides single-seed deterministic virtual time, seeded PRNG scheduling,
fault-injecting async channels, and multi-seed flaky bug discovery.
"""

from __future__ import annotations

from .chronosim import (
    ChronoRunner,
    FaultProfile,
    SimChannel,
    SimResult,
    SimSweepResult,
    VirtualClock,
    explore_seeds,
    run_simulation,
)

__all__ = [
    "ChronoRunner",
    "FaultProfile",
    "SimChannel",
    "SimResult",
    "SimSweepResult",
    "VirtualClock",
    "explore_seeds",
    "run_simulation",
]
