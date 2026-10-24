"""Immutable data contracts shared by every AiT-test subsystem.

Every frontend (`cli.py`, `mcp.py`) and every rule speaks exclusively in the
types declared here. Keeping the contract in one frozen module removes the
possibility of a rule emitting a diagnostic shape that the reporter or the
JSON-RPC bridge cannot render, and it lets cached findings be deserialised with
full fidelity (including the preparsed patch payload).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

ENGINE_NAME = "ait-engine"
ENGINE_VERSION = "1.0.0"
SCHEMA_VERSION = 1

EXIT_CLEAN = 0
EXIT_DEFECT = 1
EXIT_INTERRUPT = 2
EXIT_INTERNAL = 3
EXIT_USAGE = 4

SEVERITY_ORDER: Mapping[str, int] = {"DEFECT": 0, "WARNING": 1, "ADVICE": 2}
SEVERITY_MARKER: Mapping[str, str] = {"DEFECT": "F", "WARNING": "W", "ADVICE": "A"}

_RULE_ID_RE = re.compile(r"^AIT-[A-Z][0-9]{2}$")


class Severity(str, Enum):
    """Finding gravity ordered by the attention it demands from a reviewer."""

    DEFECT = "DEFECT"
    WARNING = "WARNING"
    ADVICE = "ADVICE"

    @property
    def rank(self) -> int:
        """Return the sort rank so DEFECT always outranks WARNING and ADVICE."""
        return SEVERITY_ORDER[self.value]

    @property
    def marker(self) -> str:
        """Return the single-character pytest-style progress marker for this severity."""
        return SEVERITY_MARKER[self.value]


@dataclass(frozen=True, slots=True)
class Location:
    """A precise, serialisable source coordinate.

    Columns are zero-based to match ``ast`` while lines stay one-based to match
    every editor and terminal convention, so a trace can be clicked directly.
    """

    path: str
    line: int
    col: int = 0
    end_line: int = 0
    end_col: int = 0

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("Location.path must not be empty")
        if self.line < 1:
            raise ValueError(f"Location.line must be >= 1, received {self.line}")
        if self.col < 0 or self.end_col < 0:
            raise ValueError("Location columns must be non-negative")
        if self.end_line and self.end_line < self.line:
            raise ValueError("Location.end_line must not precede Location.line")

    def format(self) -> str:
        """Render the canonical ``path:line:col`` coordinate used by every frontend."""
        return f"{self.path}:{self.line}:{self.col}"

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "end_line": self.end_line,
            "end_col": self.end_col,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Location":
        """Rebuild a location from its serialised mapping."""
        return cls(
            path=str(payload["path"]),
            line=int(payload["line"]),
            col=int(payload.get("col", 0)),
            end_line=int(payload.get("end_line", 0)),
            end_col=int(payload.get("end_col", 0)),
        )


@dataclass(frozen=True, slots=True)
class Evidence:
    """A single verified observation supporting a diagnostic.

    Rules may not emit a diagnostic without at least one piece of evidence; the
    guard lives in :class:`Diagnostic` so unsupported findings can never reach a
    user and erode trust in the engine.
    """

    location: Location
    note: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {"location": self.location.to_dict(), "note": self.note}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Evidence":
        """Rebuild evidence from its serialised mapping."""
        return cls(
            location=Location.from_dict(payload["location"]),
            note=str(payload["note"]),
        )


@dataclass(frozen=True, slots=True)
class CausationStep:
    """One hop in the backward value-origin trace rendered under a failure block."""

    index: int
    location: Location
    note: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "index": self.index,
            "location": self.location.to_dict(),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CausationStep":
        """Rebuild a causation step from its serialised mapping."""
        return cls(
            index=int(payload["index"]),
            location=Location.from_dict(payload["location"]),
            note=str(payload["note"]),
        )


@dataclass(frozen=True, slots=True)
class Witness:
    """A minimal counterexample snippet that demonstrates the failure mode.

    ``safe_to_execute`` distinguishes snippets that terminate quickly (safe for
    a user or a test harness to run) from snippets that deliberately deadlock or
    recurse. The engine never executes a witness itself.
    """

    summary: str
    code: str
    runnable: bool = True
    safe_to_execute: bool = True

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("Witness.code must not be empty")
        if not self.summary.strip():
            raise ValueError("Witness.summary must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "summary": self.summary,
            "code": self.code,
            "runnable": self.runnable,
            "safe_to_execute": self.safe_to_execute,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Witness":
        """Rebuild a witness from its serialised mapping."""
        return cls(
            summary=str(payload["summary"]),
            code=str(payload["code"]),
            runnable=bool(payload.get("runnable", True)),
            safe_to_execute=bool(payload.get("safe_to_execute", True)),
        )


@dataclass(frozen=True, slots=True)
class Patch:
    """A line-addressed replacement that can be rendered or applied.

    The replacement text is validated by the emitting rule against the real
    source span before it is offered; anything suspicious is dropped rather than
    shown as a fix.
    """

    start_line: int
    end_line: int
    original: str
    replacement: str
    rationale: str

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("Patch line span is invalid")
        if self.original == self.replacement:
            raise ValueError("Patch replacement must differ from the original text")

    def unified(self, path: str) -> str:
        """Render a unified diff with the real file coordinates in the hunk header.

        ``difflib`` numbers hunks from line 1 because it only sees the fragment.
        The header is rewritten using :attr:`start_line` so the diff points at the
        line the reader actually has to open.
        """
        original_lines = self.original.splitlines(True)
        replacement_lines = self.replacement.splitlines(True)
        raw = list(
            difflib.unified_diff(
                original_lines,
                replacement_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=2,
            )
        )
        if not raw:
            return ""
        body = raw[3:]
        old_count = sum(
            1 for line in body if line[:1] in (" ", "-") and not line.startswith("---")
        )
        new_count = sum(
            1 for line in body if line[:1] in (" ", "+") and not line.startswith("+++")
        )
        header = (
            f"@@ -{self.start_line},{old_count} "
            f"+{self.start_line},{new_count} @@\n"
        )
        return "".join([raw[0], raw[1], header, *body])

    def to_dict(self, path: str = "") -> Dict[str, Any]:
        """Serialise to a JSON-safe mapping, preserving the diff for cache replay."""
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "original": self.original,
            "replacement": self.replacement,
            "rationale": self.rationale,
            "diff": self.unified(path) if path else "",
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Patch":
        """Rebuild a patch from its serialised mapping."""
        return cls(
            start_line=int(payload["start_line"]),
            end_line=int(payload["end_line"]),
            original=str(payload["original"]),
            replacement=str(payload["replacement"]),
            rationale=str(payload["rationale"]),
        )


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A single, fully evidenced finding.

    The ``__post_init__`` guard is deliberate: a diagnostic without evidence is
    an opinion, and this engine does not publish opinions.
    """

    rule_id: str
    severity: Severity
    confidence: float
    message: str
    location: Location
    function: str = ""
    symbol: str = ""
    evidence: Tuple[Evidence, ...] = ()
    causation: Tuple[CausationStep, ...] = ()
    witness: Optional[Witness] = None
    patch: Optional[Patch] = None
    snippet: Tuple[Tuple[int, str], ...] = ()

    def __post_init__(self) -> None:
        if not _RULE_ID_RE.match(self.rule_id):
            raise ValueError(f"Malformed rule id: {self.rule_id!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"Confidence for {self.rule_id} must be within [0.0, 1.0], "
                f"received {self.confidence}"
            )
        if not self.message.strip():
            raise ValueError(f"{self.rule_id} emitted an empty message")
        if not self.evidence:
            raise ValueError(f"{self.rule_id} emitted a diagnostic without evidence")

    @property
    def fingerprint(self) -> str:
        """Stable identity used for cache diffing and ``--lf`` bookkeeping."""
        material = "|".join(
            (
                self.rule_id,
                self.location.path,
                str(self.location.line),
                str(self.location.col),
                self.symbol,
            )
        )
        return hashlib.blake2b(material.encode("utf-8"), digest_size=8).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-safe mapping."""
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "confidence": round(self.confidence, 4),
            "message": self.message,
            "location": self.location.to_dict(),
            "function": self.function,
            "symbol": self.symbol,
            "fingerprint": self.fingerprint,
            "evidence": [item.to_dict() for item in self.evidence],
            "causation": [step.to_dict() for step in self.causation],
            "witness": self.witness.to_dict() if self.witness else None,
            "patch": self.patch.to_dict(self.location.path) if self.patch else None,
            "snippet": [{"line": line, "text": text} for line, text in self.snippet],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Diagnostic":
        """Rebuild a diagnostic from its serialised mapping."""
        witness_payload = payload.get("witness")
        patch_payload = payload.get("patch")
        return cls(
            rule_id=str(payload["rule_id"]),
            severity=Severity(str(payload["severity"])),
            confidence=float(payload["confidence"]),
            message=str(payload["message"]),
            location=Location.from_dict(payload["location"]),
            function=str(payload.get("function", "")),
            symbol=str(payload.get("symbol", "")),
            evidence=tuple(
                Evidence.from_dict(item) for item in payload.get("evidence", ())
            ),
            causation=tuple(
                CausationStep.from_dict(item) for item in payload.get("causation", ())
            ),
            witness=Witness.from_dict(witness_payload) if witness_payload else None,
            patch=Patch.from_dict(patch_payload) if patch_payload else None,
            snippet=tuple(
                (int(item["line"]), str(item["text"]))
                for item in payload.get("snippet", ())
            ),
        )


Finding = Diagnostic


@dataclass(slots=True)
class ScanResult:
    """Aggregate outcome of one scan, shared by both frontends."""

    target: str = ""
    root: str = ""
    files_total: int = 0
    files_processed: int = 0
    files_cached: int = 0
    suppressed: int = 0
    findings: List[Diagnostic] = field(default_factory=list)
    unparsable: List[Tuple[str, str]] = field(default_factory=list)
    elapsed_ms: float = 0.0
    exit_code: int = EXIT_CLEAN
    merkle: str = ""

    def tally(self) -> Dict[str, int]:
        """Count findings per severity using every severity key, including zeros."""
        counts = {severity.value: 0 for severity in Severity}
        for item in self.findings:
            counts[item.severity.value] += 1
        return counts

    def file_outcomes(self) -> Dict[str, int]:
        """Count files that passed versus files that carry at least one DEFECT.

        Unparsable files never reach ``files_processed``; they are reported on
        their own line and force exit code 3, so they must not be counted as a
        pass here either.
        """
        failed = {item.location.path for item in self.findings if item.severity is Severity.DEFECT}
        return {"failed": len(failed), "passed": max(0, self.files_processed - len(failed))}

    def to_dict(self, include_findings: bool = True) -> Dict[str, Any]:
        """Serialise the result for JSON-RPC or downstream tooling."""
        payload: Dict[str, Any] = {
            "target": self.target,
            "root": self.root,
            "files_total": self.files_total,
            "files_processed": self.files_processed,
            "files_cached": self.files_cached,
            "suppressed": self.suppressed,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "exit_code": self.exit_code,
            "tally": self.tally(),
            "file_outcomes": self.file_outcomes(),
            "unparsable": [
                {"path": path, "reason": reason} for path, reason in self.unparsable
            ],
            "merkle": self.merkle,
        }
        if include_findings:
            payload["findings"] = [item.to_dict() for item in self.findings]
        return payload


def diagnostic_to_dict(item: Diagnostic) -> Dict[str, Any]:
    """Serialise a diagnostic to a JSON-safe mapping."""
    return item.to_dict()


def diagnostic_from_dict(payload: Mapping[str, Any]) -> Diagnostic:
    """Rebuild a diagnostic from its serialised mapping."""
    return Diagnostic.from_dict(payload)


def sort_diagnostics(items: Sequence[Diagnostic]) -> List[Diagnostic]:
    """Order findings deterministically so repeated scans are byte-stable."""
    return sorted(
        items,
        key=lambda item: (
            item.location.path,
            item.location.line,
            item.location.col,
            item.rule_id,
            item.symbol,
        ),
    )


def apply_patch(source: str, patch: Patch) -> str:
    """Apply a line-addressed patch, refusing any drift between the two.

    The function never trusts a stale patch: it re-reads the live line span and
    compares it against the captured original text, so a file edited after the
    scan cannot be corrupted by an outdated suggestion.
    """
    lines = source.splitlines(True)
    if patch.start_line < 1 or patch.end_line > len(lines):
        raise ValueError(
            f"Patch span {patch.start_line}-{patch.end_line} is outside the file "
            f"({len(lines)} lines)"
        )
    live = "".join(lines[patch.start_line - 1 : patch.end_line])
    if live != patch.original:
        raise ValueError(
            "Patch original text no longer matches the file contents at "
            f"lines {patch.start_line}-{patch.end_line}"
        )
    replacement_lines = patch.replacement.splitlines(True)
    rebuilt = lines[: patch.start_line - 1] + replacement_lines + lines[patch.end_line :]
    return "".join(rebuilt)
