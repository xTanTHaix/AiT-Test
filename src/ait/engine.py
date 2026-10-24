"""Pipeline dispatcher and project tree walker.

The engine owns the only mutable orchestration state in the tool: discovery,
cache lookup, parsing, rule dispatch and result aggregation. Both frontends call
into this module and never re-implement any part of the pipeline.
"""

from __future__ import annotations

import ast
import fnmatch
import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .cache import CacheStore, file_digest, merkle_root
from .context import RuleContext
from .flow.call_graph import build_call_graph
from .flow.scope_tree import build_scope_tree
from .models import (
    EXIT_CLEAN,
    EXIT_DEFECT,
    EXIT_INTERNAL,
    Diagnostic,
    ScanResult,
    Severity,
    sort_diagnostics,
)
from .rules import all_rules, ruleset_fingerprint

SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".ait_cache",
        ".ait_backup",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
        "node_modules",
        "build",
        "dist",
        ".onyxsi",
    }
)
IGNORE_FILENAME = ".aitignore"
SOURCE_ENCODINGS: Tuple[str, ...] = ("utf-8-sig", "cp874", "latin-1")

ProgressCallback = Callable[[str, Sequence[Diagnostic], bool], None]


class UsageError(Exception):
    """Raised when the requested target or argument combination is unusable."""


class EngineError(Exception):
    """Raised when the engine itself cannot complete a scan."""


@dataclass(frozen=True, slots=True)
class FileTarget:
    """One discovered Python file with the stat data used for cache lookup."""

    absolute: Path
    relative: str
    size: int
    mtime_ns: int


def load_ignore_patterns(root: Path) -> Tuple[str, ...]:
    """Read ``.aitignore`` glob patterns from the project root."""
    path = root / IGNORE_FILENAME
    if not path.is_file():
        return ()
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return ()
    patterns: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped.replace("\\", "/"))
    return tuple(patterns)


def is_ignored(relative: str, patterns: Sequence[str]) -> bool:
    """Return True when a relative path matches an ignore pattern."""
    if not patterns:
        return False
    candidate = PurePosixPath(relative)
    for pattern in patterns:
        if fnmatch.fnmatch(relative, pattern):
            return True
        if candidate.match(pattern):
            return True
        if pattern.endswith("/") and relative.startswith(pattern):
            return True
    return False


def discover_files(
    target: Path, root: Path, patterns: Sequence[str] = ()
) -> List[FileTarget]:
    """Discover Python files under ``target`` in deterministic order."""
    collected: List[FileTarget] = []

    def register(path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        if not path.is_file():
            return
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        if is_ignored(relative, patterns):
            return
        collected.append(
            FileTarget(
                absolute=path,
                relative=relative,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
        )

    if target.is_file():
        if target.suffix == ".py":
            register(target)
        return collected
    for current, dirnames, filenames in os.walk(target, topdown=True, followlinks=False):
        current_path = Path(current)
        try:
            current_relative = current_path.relative_to(root).as_posix()
        except ValueError:
            current_relative = ""
        pruned: List[str] = []
        for name in sorted(dirnames):
            if name in SKIP_DIRECTORIES:
                continue
            candidate = f"{current_relative}/{name}" if current_relative else name
            if is_ignored(candidate + "/", patterns) or is_ignored(candidate, patterns):
                continue
            pruned.append(name)
        dirnames[:] = pruned
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            candidate = f"{current_relative}/{filename}" if current_relative else filename
            if is_ignored(candidate, patterns):
                continue
            register(current_path / filename)
    collected.sort(key=lambda item: item.relative)
    return collected


def _describe_parse_failure(exc: BaseException) -> str:
    """Turn a parser failure into a short, operator-readable reason string."""
    if isinstance(exc, SyntaxError):
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"
    if isinstance(exc, RecursionError):
        return "RecursionError: source is nested too deeply to parse"
    if isinstance(exc, ValueError):
        return f"ValueError: {exc}"
    return f"{type(exc).__name__}: {exc}"


def resolve_encoding(raw: bytes) -> str:
    """Return the codec that ``decode_source`` would use for these bytes."""
    for encoding in SOURCE_ENCODINGS:
        try:
            raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return encoding
    return "latin-1"


def decode_source(raw: bytes) -> str:
    """Decode source bytes, stripping a UTF-8 BOM and tolerating legacy codepages.

    Windows tooling writes ``utf-8-sig`` (BOM) and local editors still emit cp874;
    decoding ``utf-8-sig`` first is what stops ``ast.parse`` from failing on a
    valid file with an invisible U+FEFF.
    """
    encoding = resolve_encoding(raw)
    if encoding == "latin-1":
        return raw.decode("latin-1", errors="replace")
    return raw.decode(encoding)


class Engine:
    """Stateful scan orchestrator shared by the CLI and the MCP bridge."""

    def __init__(self, use_cache: bool = True) -> None:
        self.use_cache = use_cache
        self.rules = all_rules()
        self.ruleset = ruleset_fingerprint()
        self._cache: Optional[CacheStore] = None
        self.suppressed_total = 0

    # ---------------------------------------------------------------- lifecycle
    def close(self) -> None:
        """Release the cache connection."""
        if self._cache is not None:
            self._cache.close()
            self._cache = None

    def _cache_for(self, root: Path) -> Optional[CacheStore]:
        """Return a cache bound to ``root``, reusing the open connection when possible."""
        if not self.use_cache:
            return None
        if self._cache is not None and self._cache.root == root:
            return self._cache
        if self._cache is not None:
            self._cache.close()
        self._cache = CacheStore(root, enabled=True)
        return self._cache

    def cache_for(self, root: Path) -> Optional[CacheStore]:
        """Public accessor used by frontends that render cache statistics."""
        return self._cache_for(Path(root))

    def last_failed_selection(
        self, root: Path, files: Sequence[FileTarget]
    ) -> Tuple[List[FileTarget], str]:
        """Narrow ``files`` to the previous failures, or fall back to all of them.

        Returns ``(selection, notice)``. A non-empty notice explains, in one
        operator-readable line, why narrowing could not be honoured: an empty or
        unusable index proves nothing about the previous run, and silently
        analysing zero files would report a clean session.
        """
        cache = self._cache_for(Path(root))
        if cache is None or not cache.enabled:
            return (
                list(files),
                "--lf needs a usable cache; scanning every file instead",
            )
        indexed, _merkle = cache.index_digest(self.ruleset)
        if indexed == 0:
            return (
                list(files),
                "--lf found no previous run in the cache; scanning every file",
            )
        remembered = set(cache.last_failed(self.ruleset))
        if not remembered:
            return (
                list(files),
                "--lf found no failed files in the cache; scanning every file",
            )
        return [item for item in files if item.relative in remembered], ""

    def discover(
        self, target: str | Path, patterns: Optional[Sequence[str]] = None
    ) -> Tuple[Path, Path, List[FileTarget]]:
        """Resolve a target into ``(resolved, root, files)`` without analysing anything."""
        if not str(target).strip():
            # ``Path("")`` is the current directory, so an empty argument would
            # silently scan wherever the tool happens to have been started.
            raise UsageError("Target is empty; pass a file or directory path")
        requested = Path(target).expanduser()
        try:
            resolved = requested.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise UsageError(f"Target is not accessible: {target}") from exc
        if resolved.is_file():
            root = resolved.parent
        elif resolved.is_dir():
            root = resolved
        else:
            raise UsageError(f"Target is neither a file nor a directory: {target}")
        ignore_patterns = tuple(patterns) if patterns is not None else load_ignore_patterns(root)
        return resolved, root, discover_files(resolved, root, ignore_patterns)

    # ---------------------------------------------------------------- analysis
    def analyze_tree(
        self, relative: str, source: str, tree: ast.Module
    ) -> List[Diagnostic]:
        """Run the full rule catalog against one parsed module."""
        scopes = build_scope_tree(relative, source, tree)
        graph = build_call_graph(scopes)
        context = RuleContext(
            path=relative,
            source=source,
            tree=tree,
            lines=source.splitlines(True),
            scopes=scopes,
            graph=graph,
        )
        findings: List[Diagnostic] = []
        for spec in self.rules:
            try:
                produced = spec.check(context)
            except (ValueError, KeyError, IndexError, AttributeError, TypeError) as exc:
                raise EngineError(
                    f"Rule {spec.rule_id} failed while analysing {relative}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            except RecursionError as exc:
                raise EngineError(
                    f"Rule {spec.rule_id} exceeded the recursion budget on {relative}"
                ) from exc
            for item in produced:
                if context.is_suppressed(item.rule_id, item.location.line):
                    self.suppressed_total += 1
                    continue
                findings.append(item)
        return findings

    def analyze_source(self, relative: str, source: str) -> List[Diagnostic]:
        """Analyse raw source text without touching the cache."""
        try:
            tree = ast.parse(source, filename=relative)
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise UsageError(
                f"{relative} does not parse: {_describe_parse_failure(exc)}"
            ) from exc
        return self.analyze_tree(relative, source, tree)

    # ---------------------------------------------------------------- scanning
    def scan(
        self,
        target: str | Path,
        *,
        exitfirst: bool = False,
        use_cache: Optional[bool] = None,
        last_failed: bool = False,
        progress: Optional[ProgressCallback] = None,
        patterns: Optional[Sequence[str]] = None,
    ) -> ScanResult:
        """Scan a file or directory and return an aggregated, deterministic result."""
        started = time.perf_counter()
        self.suppressed_total = 0
        resolved, root, files = self.discover(target, patterns)

        cache_enabled = self.use_cache if use_cache is None else bool(use_cache)
        cache = self._cache_for(root) if cache_enabled else None
        cache_usable = cache is not None and cache.enabled
        if cache_usable and resolved == root and not last_failed:
            # A full-tree scan is the only moment the engine can prove which
            # modules still exist, so that is when stale rows get dropped.
            cache.prune_missing(item.relative for item in files)
        if last_failed:
            # A degraded or empty index cannot prove anything about the previous
            # run. Narrowing to "last failed" would then analyse zero files and
            # report a clean session, so fall back to a full scan instead.
            files, _notice = self.last_failed_selection(root, files)

        result = ScanResult(
            target=str(target),
            root=str(root),
            files_total=len(files),
        )
        digests: List[str] = []
        for item in files:
            try:
                raw = item.absolute.read_bytes()
            except OSError as exc:
                result.unparsable.append((item.relative, f"unreadable: {exc}"))
                continue
            digest = file_digest(raw)
            cached = (
                cache.lookup(
                    item.relative, item.size, item.mtime_ns, self.ruleset, digest
                )
                if cache_usable
                else None
            )
            if cached is not None:
                findings = list(cached[0])
                result.files_cached += 1
            else:
                digests.append(digest)
                source = decode_source(raw)
                try:
                    tree = ast.parse(source, filename=item.relative)
                except (SyntaxError, ValueError, RecursionError) as exc:
                    result.unparsable.append(
                        (item.relative, _describe_parse_failure(exc))
                    )
                    continue
                try:
                    findings = self.analyze_tree(item.relative, source, tree)
                except EngineError as exc:
                    # One hostile or pathological file must not discard the
                    # findings of every other file in the run. It still forces
                    # exit code 3 via ``unparsable``.
                    result.unparsable.append((item.relative, str(exc)))
                    continue
                if cache_usable:
                    cache.store(
                        item.relative,
                        item.size,
                        item.mtime_ns,
                        digest,
                        self.ruleset,
                        findings,
                    )
            result.findings.extend(findings)
            result.files_processed += 1
            if progress is not None:
                progress(item.relative, findings, cached is not None)
            if exitfirst and any(
                finding.severity is Severity.DEFECT for finding in findings
            ):
                break

        result.findings = sort_diagnostics(result.findings)
        result.suppressed = self.suppressed_total
        result.elapsed_ms = (time.perf_counter() - started) * 1000.0
        if cache_usable:
            # Only the root is needed here. ``files_cached`` already counts this
            # run's hits, while the index also holds rows from earlier runs.
            result.merkle = cache.index_digest(self.ruleset)[1]
        else:
            result.merkle = merkle_root(digests)
        if result.unparsable:
            result.exit_code = EXIT_INTERNAL
        elif any(item.severity is Severity.DEFECT for item in result.findings):
            result.exit_code = EXIT_DEFECT
        else:
            result.exit_code = EXIT_CLEAN
        return result

    # ---------------------------------------------------------------- verification
    def verify_fix(
        self, relative: str, original_source: str, candidate_source: str
    ) -> Dict[str, object]:
        """Re-run the catalog over a candidate patch and report the rule delta.

        This is static re-verification, not a semantic proof: it demonstrates that
        the identified rule no longer fires and that no new rule fires, which is
        the strongest claim AST analysis can honestly make.
        """
        before = self.analyze_source(relative, original_source)
        after = self.analyze_source(relative, candidate_source)
        resolved, introduced = compare_findings(before, after)
        remaining = [item for item in after if item.severity is Severity.DEFECT]
        return {
            "file": relative,
            "resolved": [item.to_dict() for item in resolved],
            "introduced": [item.to_dict() for item in introduced],
            "remaining_defects": [item.to_dict() for item in remaining],
            "regression": bool(introduced),
            "clean": not remaining,
        }


def _occupancy(findings: Sequence[Diagnostic]) -> Dict[Tuple[str, str], List[Diagnostic]]:
    """Group findings by rule and symbol so shifted line numbers cannot fake changes."""
    table: Dict[Tuple[str, str], List[Diagnostic]] = {}
    for finding in findings:
        table.setdefault((finding.rule_id, finding.symbol), []).append(finding)
    return table


def compare_findings(
    before: Sequence[Diagnostic], after: Sequence[Diagnostic]
) -> Tuple[List[Diagnostic], List[Diagnostic]]:
    """Return ``(resolved, introduced)`` using per-rule/per-symbol occupancy counts.

    Fingerprints include line numbers, so a patch that shifts code would make
    every downstream finding look new. Comparing occupancy instead keeps the
    regression gate honest.
    """
    before_map = _occupancy(before)
    after_map = _occupancy(after)
    resolved: List[Diagnostic] = []
    introduced: List[Diagnostic] = []
    for key, items in before_map.items():
        surplus = len(items) - len(after_map.get(key, ()))
        if surplus > 0:
            resolved.extend(items[:surplus])
    for key, items in after_map.items():
        surplus = len(items) - len(before_map.get(key, ()))
        if surplus > 0:
            introduced.extend(items[:surplus])
    return resolved, introduced
