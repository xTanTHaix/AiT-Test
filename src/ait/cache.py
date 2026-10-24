"""BLAKE2b content cache with Merkle directory aggregation.

The cache exists for one reason: an unchanged module must not be re-parsed or
re-analysed. Every row is keyed by content hash *and* ruleset fingerprint, so a
catalog change invalidates old verdicts instead of silently serving stale ones.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .models import (
    SCHEMA_VERSION,
    Diagnostic,
    Severity,
    diagnostic_from_dict,
    diagnostic_to_dict,
)

CACHE_DIRNAME = ".ait_cache"
CACHE_FILENAME = "cache.db"
DIGEST_SIZE = 32
_MAX_REBUILD_ATTEMPTS = 1
_SQLITE_MAGIC = b"SQLite format 3\x00"
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    digest TEXT NOT NULL,
    ruleset TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    findings TEXT NOT NULL,
    failed INTEGER NOT NULL DEFAULT 0,
    scanned_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_failed ON files(failed);
"""


def file_digest(payload: bytes) -> str:
    """Return the BLAKE2b digest of raw file bytes."""
    return hashlib.blake2b(payload, digest_size=DIGEST_SIZE).hexdigest()


def merkle_root(digests: Iterable[str]) -> str:
    """Aggregate leaf digests into one deterministic Merkle root.

    Leaves are sorted before concatenation so the root depends on the *set* of
    module contents, never on filesystem iteration order.
    """
    ordered = sorted(item for item in digests if item)
    if not ordered:
        return ""
    material = "".join(ordered).encode("ascii")
    return hashlib.blake2b(material, digest_size=DIGEST_SIZE).hexdigest()


class CacheStore:
    """SQLite-backed finding cache with graceful degradation on any DB fault."""

    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.directory = self.root / CACHE_DIRNAME
        self.path = self.directory / CACHE_FILENAME
        self.enabled = bool(enabled)
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._rebuilds = 0
        if self.enabled:
            self._open()

    # ---------------------------------------------------------------- lifecycle
    def _open(self) -> None:
        """Open the database, creating the cache directory when required."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.enabled = False
            return
        if self.path.exists() and not self._has_sqlite_header():
            self._discard_files()
            if self.path.exists():
                self.enabled = False
                return
        connection = self._connect()
        if connection is None:
            self.enabled = False
            return
        self._connection = connection
        try:
            self._ensure_schema()
        except sqlite3.DatabaseError:
            self._recreate()

    def _connect(self) -> Optional[sqlite3.Connection]:
        """Open a connection with the required pragmas, or return ``None``."""
        connection: Optional[sqlite3.Connection] = None
        try:
            connection = sqlite3.connect(
                str(self.path),
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            if connection is not None:
                # A half-open handle can fail to close as well; the caller gets
                # ``None`` either way and the scan continues without a cache.
                with contextlib.suppress(sqlite3.Error):
                    connection.close()
            return None
        return connection

    def _has_sqlite_header(self) -> bool:
        """Return True when the file on disk starts with the SQLite magic string."""
        try:
            with open(self.path, "rb") as handle:
                return handle.read(16) == _SQLITE_MAGIC
        except OSError:
            return False

    def _discard_files(self) -> None:
        """Delete the database plus its WAL sidecars, ignoring locked files."""
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(self.path) + suffix).unlink()
            except OSError:
                continue

    def _ensure_schema(self) -> None:
        """Create tables and invalidate the cache when the schema version moves."""
        with self._lock:
            assert self._connection is not None
            self._connection.executescript(_SCHEMA_SQL)
            row = self._connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                return
            if str(row[0]) != str(SCHEMA_VERSION):
                self._connection.execute("DELETE FROM files")
                self._connection.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )

    def _recreate(self) -> None:
        """Rebuild a corrupted database once, then degrade instead of recursing.

        A persistently failing ``_ensure_schema`` (full disk, read-only mount,
        broken page) used to re-enter ``_open`` forever; after one rebuild the
        cache disables itself so the scan itself still succeeds.
        """
        self.close()
        self._discard_files()
        if self.path.exists():
            # The file is still locked by another process: degrade to no cache.
            self.enabled = False
            return
        with self._lock:
            can_rebuild = self._rebuilds < _MAX_REBUILD_ATTEMPTS
            if can_rebuild:
                self._rebuilds += 1
        if not can_rebuild:
            self.enabled = False
            return
        self._open()

    def close(self) -> None:
        """Close the underlying connection if one is open."""
        with self._lock:
            if self._connection is not None:
                # Closing is best effort: a connection already torn down by the
                # sqlite library raises here, and shutdown must not fail on it.
                with contextlib.suppress(sqlite3.Error):
                    self._connection.close()
                self._connection = None

    # ---------------------------------------------------------------- queries
    def lookup(
        self,
        path: str,
        size: int,
        mtime_ns: int,
        ruleset: str,
        digest: Optional[str] = None,
    ) -> Optional[Tuple[List[Diagnostic], bool]]:
        """Return cached findings for an unchanged file, or ``None`` on a miss.

        ``digest`` is the BLAKE2b hash of the bytes the caller is about to analyse.
        When supplied it must match the stored row, so a rewrite that restores the
        original size and mtime (``git checkout``, ``touch -r``, backup restore)
        cannot serve findings that belong to different content.
        """
        if not self.enabled or self._connection is None:
            return None
        query = (
            "SELECT findings, failed FROM files WHERE path = ? AND size = ? "
            "AND mtime_ns = ? AND ruleset = ? AND schema_version = ?"
        )
        parameters: Tuple[object, ...] = (path, size, mtime_ns, ruleset, SCHEMA_VERSION)
        if digest is not None:
            query += " AND digest = ?"
            parameters += (digest,)
        with self._lock:
            try:
                row = self._connection.execute(query, parameters).fetchone()
            except sqlite3.Error:
                return None
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
            findings = [diagnostic_from_dict(item) for item in payload]
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        return findings, bool(row[1])

    def store(
        self,
        path: str,
        size: int,
        mtime_ns: int,
        digest: str,
        ruleset: str,
        findings: Sequence[Diagnostic],
    ) -> None:
        """Persist findings for one file, replacing any previous row."""
        if not self.enabled or self._connection is None:
            return
        failed = int(any(item.severity is Severity.DEFECT for item in findings))
        payload = json.dumps([diagnostic_to_dict(item) for item in findings])
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT OR REPLACE INTO files"
                    "(path, size, mtime_ns, digest, ruleset, schema_version, findings, failed, scanned_at)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        path,
                        size,
                        mtime_ns,
                        digest,
                        ruleset,
                        SCHEMA_VERSION,
                        payload,
                        failed,
                        time.time(),
                    ),
                )
            except sqlite3.Error:
                return

    def last_failed(self, ruleset: str) -> List[str]:
        """Return paths that carried at least one DEFECT under the active ruleset."""
        if not self.enabled or self._connection is None:
            return []
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT path FROM files WHERE failed = 1 AND ruleset = ? "
                    "AND schema_version = ? ORDER BY path",
                    (ruleset, SCHEMA_VERSION),
                ).fetchall()
            except sqlite3.Error:
                return []
        return [str(row[0]) for row in rows]

    def prune_missing(self, known_paths: Iterable[str]) -> None:
        """Drop rows for files that no longer exist, keeping ``--lf`` honest."""
        if not self.enabled or self._connection is None:
            return
        keep = set(known_paths)
        with self._lock:
            try:
                rows = self._connection.execute("SELECT path FROM files").fetchall()
                stale = [str(row[0]) for row in rows if str(row[0]) not in keep]
                for stale_path in stale:
                    self._connection.execute("DELETE FROM files WHERE path = ?", (stale_path,))
            except sqlite3.Error:
                return

    def index_digest(self, ruleset: str) -> Tuple[int, str]:
        """Return ``(indexed_files, merkle_root)`` for the active ruleset."""
        if not self.enabled or self._connection is None:
            return 0, ""
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT digest FROM files WHERE ruleset = ? AND schema_version = ?",
                    (ruleset, SCHEMA_VERSION),
                ).fetchall()
            except sqlite3.Error:
                return 0, ""
        digests = [str(row[0]) for row in rows]
        return len(digests), merkle_root(digests)

    def clear(self) -> None:
        """Delete every cached row (used by ``--no-cache`` bookkeeping and tests)."""
        if not self.enabled or self._connection is None:
            return
        with self._lock:
            try:
                self._connection.execute("DELETE FROM files")
            except sqlite3.Error:
                return
