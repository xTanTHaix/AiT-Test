"""Pytest-style ANSI stream renderer.

The terminal is the primary interface, so the reporter streams one compact line
per module and only expands the full evidence block when something is wrong. All
colour is optional: a non-TTY, ``NO_COLOR`` or a legacy console falls back to
plain text without losing information.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional, Sequence, TextIO

from .models import ENGINE_NAME, ENGINE_VERSION, Diagnostic, ScanResult, Severity
from .rules import all_rules

WIDTH = 80
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
MAGENTA = "\x1b[35m"

_SEVERITY_COLOR: Dict[str, str] = {
    "DEFECT": RED,
    "WARNING": YELLOW,
    "ADVICE": CYAN,
}


def configure_stream(stream: TextIO) -> None:
    """Force UTF-8 output where the platform allows it.

    Windows consoles default to a legacy codepage; without this, a witness that
    contains non-ASCII text raises ``UnicodeEncodeError`` mid-report.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return


def _enable_windows_ansi() -> bool:
    """Enable virtual terminal processing on the Windows console."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_virtual_terminal = 0x0004
        return bool(
            kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal)
        )
    except (OSError, AttributeError, ValueError):
        return False


def supports_color(stream: TextIO) -> bool:
    """Decide whether ANSI colour is safe for this stream."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if not getattr(stream, "isatty", lambda: False)():
        return False
    return _enable_windows_ansi()


def _banner(text: str) -> str:
    """Centre a section title between pytest-style rules."""
    painted = f" {text} "
    if len(painted) >= WIDTH:
        return painted
    left = (WIDTH - len(painted)) // 2
    right = WIDTH - len(painted) - left
    return f"{'=' * left}{painted}{'=' * right}"


class Reporter:
    """Streaming renderer for a scan session."""

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        *,
        verbose: bool = False,
        quiet: bool = False,
        use_color: Optional[bool] = None,
        error_stream: Optional[TextIO] = None,
    ) -> None:
        self.stream: TextIO = stream or sys.stdout
        configure_stream(self.stream)
        # Errors never share the report stream: a usage failure must stay
        # visible when stdout is piped into a file or another tool.
        self.error_stream: TextIO = error_stream or sys.stderr
        configure_stream(self.error_stream)
        self.verbose = verbose
        self.quiet = quiet
        self.color = supports_color(self.stream) if use_color is None else bool(use_color)
        self._rules = {spec.rule_id: spec for spec in all_rules()}
        self._started = time.perf_counter()

    # ---------------------------------------------------------------- primitives
    def paint(self, text: str, color: str) -> str:
        """Wrap text in an ANSI colour when the stream supports it."""
        return f"{color}{text}{RESET}" if self.color else text

    def _write(self, text: str = "") -> None:
        self.stream.write(text + "\n")

    def severity_label(self, severity: Severity) -> str:
        """Render a severity word with its category colour."""
        return self.paint(severity.value, _SEVERITY_COLOR.get(severity.value, ""))

    # ---------------------------------------------------------------- session
    def session_start(
        self,
        *,
        target: str,
        root: str,
        file_count: int,
        cache_dir: str,
        cached: int,
        indexed: int,
        merkle: str,
    ) -> None:
        """Print the pytest-style session header."""
        if self.quiet:
            return
        self._write(self.paint(_banner("AiT-test session starts"), BOLD))
        self._write(
            f"platform {sys.platform} -- Python {sys.version.split()[0]}, "
            f"{ENGINE_NAME}-{ENGINE_VERSION}"
        )
        if cache_dir:
            index_note = (
                f"Indexed: {indexed} modules, {cached} unchanged [cached]"
                if indexed
                else f"Indexed: {indexed} modules"
            )
            self._write(f"cachedir: {cache_dir} ({index_note})")
            if merkle:
                self._write(f"index: {merkle[:16]}...")
        self._write(f"rootdir: {root}")
        self._write(f"target: {target}")
        self._write(f"collecting {file_count} files to analyze...")
        self._write()

    def file_line(
        self,
        relative: str,
        findings: Sequence[Diagnostic],
        index: int,
        total: int,
        cached: bool = False,
    ) -> None:
        """Stream one pytest-style progress row for a module."""
        if self.quiet:
            return
        markers = self._markers(findings)
        percent = int(round((index / total) * 100)) if total else 100
        name = relative if len(relative) <= 48 else "..." + relative[-45:]
        suffix = " (cached)" if cached and self.verbose else ""
        self._write(
            f"{name} {self._paint_markers(markers)}"
            f"{' ' * max(1, 8 - len(markers))}[{percent:3d}%]{suffix}"
        )
        for finding in findings:
            if finding.severity is not Severity.DEFECT or not self.verbose:
                continue
            self._write(
                "    "
                + self.paint(f"{finding.rule_id} {finding.location.format()}", RED)
            )

    def _markers(self, findings: Sequence[Diagnostic]) -> str:
        """One marker per rule that ran: a dot for clean, a letter for a hit."""
        hit: Dict[str, Severity] = {}
        for finding in findings:
            current = hit.get(finding.rule_id)
            if current is None or finding.severity.rank < current.rank:
                hit[finding.rule_id] = finding.severity
        markers: List[str] = []
        for rule_id in sorted(self._rules):
            severity = hit.get(rule_id)
            markers.append(severity.marker if severity is not None else ".")
        return "".join(markers)

    def _paint_markers(self, markers: str) -> str:
        """Colour markers individually without disturbing column alignment."""
        if not self.color:
            return markers
        painted = []
        for marker in markers:
            if marker == "F":
                painted.append(f"{RED}{marker}{RESET}")
            elif marker == "W":
                painted.append(f"{YELLOW}{marker}{RESET}")
            elif marker == "A":
                painted.append(f"{CYAN}{marker}{RESET}")
            else:
                painted.append(f"{DIM}.{RESET}")
        return "".join(painted)

    # ---------------------------------------------------------------- failures
    def failures(self, findings: Sequence[Diagnostic], limit: Optional[int] = None) -> None:
        """Render full evidence blocks for every finding."""
        if not findings or self.quiet:
            return
        self._write()
        self._write(self.paint(_banner("FAILURES"), BOLD))
        shown = findings if limit is None else list(findings)[:limit]
        for finding in shown:
            self._failure_block(finding)
        if limit is not None and len(findings) > limit:
            self._write()
            self._write(
                self.paint(
                    f"... {len(findings) - limit} further findings suppressed by --maxfail",
                    DIM,
                )
            )

    def _failure_block(self, finding: Diagnostic) -> None:
        """Render one diagnostic with its slice, trace, witness and diff."""
        title = self._rules[finding.rule_id].title if finding.rule_id in self._rules else ""
        headline = f"[{finding.rule_id}] {title}"
        self._write()
        self._write(self.paint(_banner(headline), _SEVERITY_COLOR[finding.severity.value] + BOLD))
        self._write(f"File: {finding.location.format()}")
        self._write(
            f"Severity: {self.severity_label(finding.severity)} "
            f"(Confidence: {finding.confidence:.2f})"
        )
        if finding.function:
            self._write(f"Function: {finding.function}")
        if finding.symbol:
            self._write(f"Symbol: {finding.symbol}")
        self._write()
        self._write(f"  {finding.message}")
        self._write()
        self._render_slice(finding)
        if finding.causation:
            self._write()
            self._write(self.paint("--- Causation Trace ---", BOLD))
            for step in finding.causation:
                self._write(f"  Step {step.index} | {step.location.format()}")
                self._write(f"         | {step.note}")
        if finding.witness is not None:
            self._write()
            self._write(self.paint("--- Minimal Repro Witness ---", BOLD))
            self._write(self.paint(f"  {finding.witness.summary}", DIM))
            if not finding.witness.safe_to_execute:
                self._write(
                    self.paint(
                        "  WARNING: running this snippet is expected to hang or recurse.",
                        YELLOW,
                    )
                )
            for line in finding.witness.code.rstrip("\n").splitlines():
                self._write(f"  {line}")
        if finding.patch is not None:
            self._write()
            self._write(self.paint("--- Suggested Fix ---", BOLD))
            diff = finding.patch.unified(finding.location.path)
            for line in diff.rstrip("\n").splitlines():
                color = GREEN if line.startswith("+") and not line.startswith("+++") else RED if line.startswith("-") and not line.startswith("---") else DIM
                self._write(f"  {self.paint(line, color)}")

    def _render_slice(self, finding: Diagnostic) -> None:
        """Render a short numbered source slice with the target line marked."""
        if not finding.snippet:
            self._write(self.paint(f"  {finding.location.path}", DIM))
            return
        focus = finding.location.line
        for lineno, text in finding.snippet:
            marker = ">" if lineno == focus else " "
            rendered = f"  {marker} {lineno:4d} | {text}"
            self._write(rendered if marker == ">" else self.paint(rendered, DIM))

    # ---------------------------------------------------------------- tail
    def parse_failures(self, unparsable: Sequence[tuple]) -> None:
        """Render files that could not be parsed as a dedicated error section."""
        if not unparsable or self.quiet:
            return
        self._write()
        self._write(self.paint(_banner("COLLECTION ERRORS"), RED + BOLD))
        for relative, reason in unparsable:
            self._write(self.paint(f"E {relative}", RED))
            self._write(f"  {reason}")

    def summary(self, result: ScanResult) -> None:
        """Print the closing tally line with elapsed time and cache statistics."""
        tally = result.tally()
        elapsed = (time.perf_counter() - self._started) * 1000.0
        outcomes = result.file_outcomes()
        head = (
            f"{outcomes['failed']} failed, {outcomes['passed']} passed in "
            f"{elapsed:.2f}ms"
        )
        if result.files_cached:
            head += f" (cached {result.files_cached}/{result.files_total})"
        if result.unparsable:
            head += f", {len(result.unparsable)} unparsable"
        if result.suppressed:
            head += f", {result.suppressed} suppressed"
        self._write()
        self._write(self.paint(_banner(head), BOLD))
        self._write(
            "  "
            + " · ".join(
                f"{self.severity_label(severity)} {tally[severity.value]}"
                for severity in Severity
            )
        )
        if result.exit_code != 0:
            self._write(f"  exit code: {result.exit_code}")

    def hint(self, message: str) -> None:
        """Print a dim informational line."""
        self._write(self.paint(message, DIM))

    def error(self, message: str) -> None:
        """Print a red error line to the error stream (stderr by default)."""
        self.error_stream.write(self.paint(f"ERROR: {message}", RED) + "\n")
