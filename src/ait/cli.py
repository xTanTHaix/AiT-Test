"""Human entrypoint: pytest-style runner with interactive patch application."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .engine import (
    Engine,
    EngineError,
    UsageError,
    compare_findings,
    decode_source,
    resolve_encoding,
)
from .models import (
    ENGINE_VERSION,
    EXIT_CLEAN,
    EXIT_INTERNAL,
    EXIT_INTERRUPT,
    EXIT_USAGE,
    Diagnostic,
    Patch,
    ScanResult,
    Severity,
    apply_patch,
)
from .reporter import Reporter, configure_stream

BACKUP_DIRNAME = ".ait_backup"


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser with the documented flag set."""
    parser = argparse.ArgumentParser(
        prog="ait",
        description=(
            "High-assurance static invariant, causation and concurrency diagnostics."
        ),
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="file or directory to analyse (default: current directory)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="per-file detail")
    parser.add_argument("-q", "--quiet", action="store_true", help="summary only")
    parser.add_argument(
        "-x",
        "--exitfirst",
        action="store_true",
        dest="exitfirst",
        help="stop at the first DEFECT",
    )
    parser.add_argument(
        "--lf",
        "--last-failed",
        action="store_true",
        dest="last_failed",
        help="re-scan only files that failed in the previous run",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="show unified diffs and apply confirmed patches",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        dest="no_cache",
        help="ignore the cache and re-analyse every file",
    )
    parser.add_argument(
        "--maxfail",
        type=int,
        default=None,
        metavar="N",
        help="render at most N failure blocks",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        dest="no_color",
        help="disable ANSI colour (also honours NO_COLOR)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ait-engine {ENGINE_VERSION}",
    )
    return parser


def _render_advice(reporter: Reporter, findings: Sequence[Diagnostic]) -> None:
    """Print ADVICE findings as single dim lines instead of full blocks."""
    for finding in findings:
        if finding.severity is not Severity.ADVICE:
            continue
        reporter.hint(
            f"A {finding.rule_id} {finding.location.format()} — {finding.message}"
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one scan and return the process exit code."""
    configure_stream(sys.stdout)
    configure_stream(sys.stderr)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        return EXIT_USAGE if code != 0 else EXIT_CLEAN

    if args.last_failed and args.no_cache:
        print("ait: --lf needs the cache index; drop --no-cache", file=sys.stderr)
        return EXIT_USAGE
    if args.maxfail is not None and args.maxfail < 0:
        print("ait: --maxfail must be zero or positive", file=sys.stderr)
        return EXIT_USAGE

    reporter = Reporter(
        verbose=args.verbose,
        quiet=args.quiet,
        use_color=False if args.no_color else None,
    )
    engine = Engine(use_cache=not args.no_cache)
    try:
        resolved, root, files = engine.discover(args.target)
        cache = engine.cache_for(root)
        cache_usable = cache is not None and cache.enabled
        if args.last_failed:
            # ``--lf`` narrows the set before the banner so the progress
            # denominator matches the files that are actually analysed. When the
            # index cannot answer, the helper keeps every file and says why.
            files, notice = engine.last_failed_selection(root, files)
            if notice:
                print(f"ait: {notice}", file=sys.stderr)
        indexed, merkle = (
            cache.index_digest(engine.ruleset) if cache_usable else (0, "")
        )
        reporter.session_start(
            target=str(args.target),
            root=str(root),
            file_count=len(files),
            cache_dir=str(cache.directory) if cache_usable else "",
            cached=0,
            indexed=indexed,
            merkle=merkle,
        )
        counter = {"index": 0}

        def on_file(relative: str, findings: Sequence[Diagnostic], cached: bool) -> None:
            counter["index"] += 1
            reporter.file_line(
                relative, findings, counter["index"], len(files), cached
            )

        result = engine.scan(
            args.target,
            exitfirst=args.exitfirst,
            use_cache=not args.no_cache,
            last_failed=args.last_failed,
            progress=on_file,
        )
        blocking = [
            item for item in result.findings if item.severity is not Severity.ADVICE
        ]
        reporter.failures(blocking, limit=args.maxfail)
        _render_advice(reporter, result.findings)
        reporter.parse_failures(result.unparsable)
        if args.fix and blocking:
            result = _fix_session(engine, reporter, result, root, args.target)
        reporter.summary(result)
        return result.exit_code
    except UsageError as exc:
        reporter.error(str(exc))
        return EXIT_USAGE
    except EngineError as exc:
        reporter.error(str(exc))
        return EXIT_INTERNAL
    except KeyboardInterrupt:
        reporter.error("interrupted by user")
        return EXIT_INTERRUPT
    except OSError as exc:
        reporter.error(f"I/O failure: {exc}")
        return EXIT_INTERNAL
    finally:
        engine.close()


def _fix_session(
    engine: Engine,
    reporter: Reporter,
    result: ScanResult,
    root: Path,
    target: str,
) -> ScanResult:
    """Offer each available patch, apply it atomically and verify the result.

    Patches are applied from the bottom of each file upwards so earlier line
    numbers stay valid, and every write is backed up and re-verified. A patch that
    introduces any new finding is rolled back instead of trusted.
    """
    candidates = [item for item in result.findings if item.patch is not None]
    if not candidates:
        reporter.hint("No automatic fix is available for the current findings.")
        return result
    if not _stdin_is_interactive():
        reporter.error("--fix requires an interactive terminal; nothing was modified.")
        return result

    backup_root = root / BACKUP_DIRNAME / (
        time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    )
    by_file: Dict[str, List[Diagnostic]] = {}
    for finding in candidates:
        by_file.setdefault(finding.location.path, []).append(finding)

    applied, skipped = _apply_all(engine, reporter, root, by_file, backup_root)

    reporter.hint("")
    reporter.hint(f"fixes applied: {applied}, skipped: {skipped}")
    if applied == 0:
        return result
    reporter.hint(f"originals backed up to {backup_root}")
    reporter.hint("re-scanning to confirm the final state...")
    return engine.scan(
        target,
        use_cache=False,
        exitfirst=False,
    )


def _patch_order(items: Sequence[Diagnostic]) -> List[Diagnostic]:
    """Order findings bottom-up so applying one never shifts the next patch."""
    return sorted(
        items,
        key=lambda item: item.patch.start_line if item.patch else 0,
        reverse=True,
    )


def _apply_all(
    engine: Engine,
    reporter: Reporter,
    root: Path,
    by_file: Dict[str, List[Diagnostic]],
    backup_root: Path,
) -> Tuple[int, int]:
    """Apply every confirmed patch, journalling an interrupt before re-raising.

    Returns ``(applied, skipped)``. The loop is kept at four control-flow levels
    (try/for/for/if) so the session function stays readable and the engine's own
    H02 rule keeps passing on this file.
    """
    applied = 0
    skipped = 0
    apply_rest = False
    try:
        for relative, items in sorted(by_file.items()):
            path = root / relative
            for finding in _patch_order(items):
                action, apply_rest = _handle_finding(
                    engine, reporter, path, relative, finding, backup_root, apply_rest
                )
                if action == "stop":
                    return applied, skipped
                if action == "applied":
                    applied += 1
                else:
                    skipped += 1
    except KeyboardInterrupt:
        reporter.hint("")
        reporter.hint(
            f"interrupted after {applied} applied fix(es); originals are in {backup_root}"
        )
        raise
    return applied, skipped


def _handle_finding(
    engine: Engine,
    reporter: Reporter,
    path: Path,
    relative: str,
    finding: Diagnostic,
    backup_root: Path,
    apply_rest: bool,
) -> Tuple[str, bool]:
    """Render one patch, ask for confirmation and apply it.

    Returns ``(action, apply_rest)`` where action is ``applied``, ``skipped`` or
    ``stop``. Extracted from the session loop so neither function grows a nest
    deep enough to trip the engine's own H02 rule.
    """
    patch = finding.patch
    assert patch is not None
    reporter.hint("")
    reporter.hint(patch.unified(relative).rstrip("\n"))
    if not apply_rest:
        answer = _ask_confirmation(finding)
        if answer == "q":
            return "stop", apply_rest
        if answer == "a":
            apply_rest = True
        elif answer != "y":
            return "skipped", apply_rest
    ok, message = _apply_single(engine, path, relative, finding, backup_root)
    if ok:
        reporter.hint(f"applied {finding.rule_id} to {relative}")
        return "applied", apply_rest
    reporter.error(message)
    return "skipped", apply_rest


def _stdin_is_interactive() -> bool:
    """Return True only when stdin can actually answer a prompt.

    ``isatty()`` alone is not enough on Windows: ``NUL`` and ``subprocess``'s
    ``DEVNULL`` are character devices, so a redirected stdin still reports a TTY
    and the session would render prompts nobody can answer.
    """
    stream = sys.stdin
    try:
        if stream is None or not stream.isatty():
            return False
        return _windows_console_attached(stream) if os.name == "nt" else True
    except (AttributeError, ValueError, OSError):
        return False


def _windows_console_attached(stream) -> bool:
    """Return True when ``stream``'s handle is a real Windows console."""
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        kernel32 = ctypes.windll.kernel32
        return bool(kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))
    except (AttributeError, ImportError, OSError, ValueError):
        return False


def _ask_confirmation(finding: Diagnostic) -> str:
    """Read the interactive y/N/a/q answer, treating closed stdin as quit."""
    try:
        return input(
            f"Apply {finding.rule_id} at {finding.location.format()}? [y/N/a/q] "
        ).strip().lower()
    except EOFError:
        return "q"


def _apply_single(
    engine: Engine,
    path: Path,
    relative: str,
    finding: Diagnostic,
    backup_root: Path,
) -> Tuple[bool, str]:
    """Apply one patch after proving it does not introduce any new finding."""
    patch: Optional[Patch] = finding.patch
    if patch is None:
        return False, f"{relative}: no patch payload"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return False, f"{relative}: cannot read ({exc})"
    source = decode_source(raw)
    try:
        patched = apply_patch(source, patch)
    except ValueError as exc:
        return False, f"{relative}: {exc}"
    try:
        before = engine.analyze_source(relative, source)
        after = engine.analyze_source(relative, patched)
    except UsageError as exc:
        return False, f"{relative}: patched source rejected ({exc})"
    _resolved, introduced = compare_findings(before, after)
    if introduced:
        labels = ", ".join(sorted({item.rule_id for item in introduced}))
        return False, f"{relative}: fix rejected, it would introduce {labels}"
    encoded = _encode_like(patched, raw)
    if encoded is None:
        return False, (
            f"{relative}: cannot preserve the file's encoding, nothing was written"
        )
    backup = backup_root / relative
    temporary: Optional[Path] = None
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        temporary = path.with_name(path.name + ".ait-tmp")
        # Write bytes: ``write_text`` would translate ``\n`` to ``\r\n`` on
        # Windows and silently rewrite the line endings of the whole file.
        temporary.write_bytes(encoded)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        return False, f"{relative}: write failed ({exc})"
    return True, "applied"


_UTF8_BOM = b"\xef\xbb\xbf"


def _encode_like(text: str, original: bytes) -> Optional[bytes]:
    """Re-encode a patched source with the codec the original bytes used.

    Returns ``None`` when the file's own codec cannot represent the patch. The
    caller must then refuse the write: encoding as UTF-8 regardless would keep a
    ``# -*- coding: cp874 -*-`` cookie in place and silently corrupt the file.
    """
    encoding = resolve_encoding(original)
    if encoding == "utf-8-sig" and not original.startswith(_UTF8_BOM):
        # ``utf-8-sig`` also decodes a BOM-less file, so re-encoding with it
        # would silently add a BOM the original never had.
        encoding = "utf-8"
    try:
        data = text.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return None
    if decode_source(data) != text:
        return None
    return data


if __name__ == "__main__":
    raise SystemExit(main())
