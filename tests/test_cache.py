"""Cache behaviour: hits, invalidation, corruption recovery and --lf bookkeeping."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests._support import analyze, fixture_text  # noqa: F401  (anchors sys.path)

from ait.cache import CACHE_DIRNAME, CACHE_FILENAME, CacheStore, file_digest, merkle_root
from ait.engine import Engine
from ait.rules.registry import _code_digest, ruleset_fingerprint
from ait.models import (
    CausationStep,
    Diagnostic,
    Evidence,
    Location,
    Patch,
    Severity,
    Witness,
    diagnostic_from_dict,
    diagnostic_to_dict,
)

VULNERABLE = "from typing import Optional\n\ndef use(value: Optional[str]) -> None:\n    print(value.upper())\n"
SAFE = VULNERABLE.replace(
    "    print(value.upper())\n",
    "    if value is None:\n        return\n    print(value.upper())\n",
)


def _seed(root: Path, text: str = VULNERABLE) -> Path:
    path = root / "sample.py"
    path.write_text(text, encoding="utf-8")
    return path


class MerkleTests(unittest.TestCase):
    def test_root_is_order_independent(self) -> None:
        digests = [file_digest(b"one"), file_digest(b"two"), file_digest(b"three")]
        self.assertEqual(merkle_root(digests), merkle_root(list(reversed(digests))))

    def test_root_changes_with_content(self) -> None:
        first = merkle_root([file_digest(b"one")])
        second = merkle_root([file_digest(b"one"), file_digest(b"two")])
        self.assertNotEqual(first, second)

    def test_empty_root_is_empty_string(self) -> None:
        self.assertEqual(merkle_root([]), "")


class CacheRoundTripTests(unittest.TestCase):
    def test_store_and_lookup_preserve_full_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CacheStore(root)
            try:
                finding = Diagnostic(
                    rule_id="AIT-N01",
                    severity=Severity.DEFECT,
                    confidence=0.97,
                    message="optional value dereferenced",
                    location=Location(path="sample.py", line=4, col=4),
                    function="use(value)",
                    symbol="value",
                    evidence=(
                        Evidence(
                            location=Location(path="sample.py", line=4, col=4),
                            note="deref",
                        ),
                    ),
                    causation=(
                        CausationStep(
                            index=1,
                            location=Location(path="sample.py", line=3),
                            note="parameter",
                        ),
                    ),
                    witness=Witness(summary="demo", code="x = 1\n"),
                    patch=Patch(4, 4, "    print(value.upper())\n", "    pass\n", "guard"),
                    snippet=((3, "def use(value):"), (4, "    print(value.upper())")),
                )
                store.store("sample.py", 10, 20, "digest", "rules", [finding])
                payload = store.lookup("sample.py", 10, 20, "rules")
                self.assertIsNotNone(payload)
                findings, failed = payload  # type: ignore[misc]
                self.assertTrue(failed)
                self.assertEqual([item.to_dict() for item in findings], [finding.to_dict()])
                self.assertEqual(
                    diagnostic_to_dict(diagnostic_from_dict(finding.to_dict())),
                    finding.to_dict(),
                )
            finally:
                store.close()

    def test_lookup_misses_when_ruleset_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CacheStore(Path(directory))
            try:
                store.store("sample.py", 1, 2, "digest", "rules-a", [])
                self.assertIsNotNone(store.lookup("sample.py", 1, 2, "rules-a"))
                self.assertIsNone(store.lookup("sample.py", 1, 2, "rules-b"))
            finally:
                store.close()

    def test_lookup_misses_when_stat_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CacheStore(Path(directory))
            try:
                store.store("sample.py", 1, 2, "digest", "rules", [])
                self.assertIsNone(store.lookup("sample.py", 2, 2, "rules"))
                self.assertIsNone(store.lookup("sample.py", 1, 3, "rules"))
            finally:
                store.close()

    def test_lookup_misses_when_digest_changes(self) -> None:
        # A rewrite that restores size and mtime (git checkout, touch -r, backup
        # restore) must not serve the previous content's verdicts.
        with tempfile.TemporaryDirectory() as directory:
            store = CacheStore(Path(directory))
            try:
                store.store("sample.py", 1, 2, "digest-a", "rules", [])
                self.assertIsNotNone(
                    store.lookup("sample.py", 1, 2, "rules", "digest-a")
                )
                self.assertIsNone(store.lookup("sample.py", 1, 2, "rules", "digest-b"))
            finally:
                store.close()

    def test_corrupt_payload_is_treated_as_a_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CacheStore(root)
            try:
                store.store("sample.py", 1, 2, "digest", "rules", [])
                connection = sqlite3.connect(str(root / CACHE_DIRNAME / CACHE_FILENAME))
                connection.execute("UPDATE files SET findings = 'not json'")
                connection.commit()
                connection.close()
                self.assertIsNone(store.lookup("sample.py", 1, 2, "rules"))
            finally:
                store.close()


class EngineCacheTests(unittest.TestCase):
    def test_second_scan_uses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed(root)
            engine = Engine()
            try:
                first = engine.scan(root)
                second = engine.scan(root)
            finally:
                engine.close()
            self.assertEqual(first.files_cached, 0)
            self.assertEqual(first.exit_code, 1)
            self.assertEqual(second.files_cached, 1)
            self.assertEqual(second.files_processed, 1)
            self.assertEqual(
                [item.fingerprint for item in first.findings],
                [item.fingerprint for item in second.findings],
            )

    def test_cache_returns_identical_findings_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed(root)
            first_engine = Engine()
            try:
                first = first_engine.scan(root)
            finally:
                first_engine.close()
            second_engine = Engine()
            try:
                second = second_engine.scan(root)
            finally:
                second_engine.close()
            self.assertEqual(second.files_cached, 1)
            self.assertEqual(
                [item.to_dict() for item in first.findings],
                [item.to_dict() for item in second.findings],
            )

    def test_content_change_invalidates_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _seed(root)
            engine = Engine()
            try:
                engine.scan(root)
                path.write_text(SAFE, encoding="utf-8")
                result = engine.scan(root)
            finally:
                engine.close()
            self.assertEqual(result.files_cached, 0)
            self.assertEqual(result.findings, [])
            self.assertEqual(result.exit_code, 0)

    def test_restored_size_and_mtime_do_not_reuse_stale_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _seed(root)
            engine = Engine()
            try:
                first = engine.scan(root)
                before = path.stat()
                original_size = before.st_size
                replacement = b"value = 1\n"
                replacement += b"\n" * (original_size - len(replacement))
                path.write_bytes(replacement)
                # ``touch -r`` / ``git checkout`` semantics: same size, same mtime.
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                self.assertEqual(path.stat().st_size, before.st_size)
                self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
                second = engine.scan(root)
            finally:
                engine.close()
            self.assertEqual(first.exit_code, 1)
            self.assertEqual(second.files_cached, 0)
            self.assertEqual(second.findings, [])
            self.assertEqual(second.exit_code, 0)

    def test_no_cache_flag_always_reanalyses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed(root)
            engine = Engine(use_cache=False)
            try:
                first = engine.scan(root, use_cache=False)
                second = engine.scan(root, use_cache=False)
            finally:
                engine.close()
            self.assertEqual(first.files_cached, 0)
            self.assertEqual(second.files_cached, 0)
            self.assertFalse((root / CACHE_DIRNAME).exists())

    def test_corrupted_database_is_rebuilt_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed(root)
            engine = Engine()
            try:
                engine.scan(root)
            finally:
                engine.close()
            database = root / CACHE_DIRNAME / CACHE_FILENAME
            database.write_bytes(b"this is not a sqlite database at all")
            engine = Engine()
            try:
                result = engine.scan(root)
            finally:
                engine.close()
            self.assertEqual(result.exit_code, 1)
            self.assertEqual(len(result.findings), 1)

    def test_last_failed_tracks_defect_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.py").write_text(VULNERABLE, encoding="utf-8")
            (root / "clean.py").write_text("value = 1\n", encoding="utf-8")
            engine = Engine()
            try:
                engine.scan(root)
                cache = engine.cache_for(root)
                assert cache is not None
                self.assertEqual(cache.last_failed(engine.ruleset), ["bad.py"])
                filtered = engine.scan(root, last_failed=True)
            finally:
                engine.close()
            self.assertEqual(filtered.files_total, 1)
            self.assertEqual([item.location.path for item in filtered.findings], ["bad.py"])

    def test_prune_missing_drops_deleted_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed(root)
            engine = Engine()
            try:
                engine.scan(root)
                (root / "sample.py").unlink()
                (root / "fresh.py").write_text("value = 1\n", encoding="utf-8")
                result = engine.scan(root)
                cache = engine.cache_for(root)
                assert cache is not None
                self.assertEqual(cache.last_failed(engine.ruleset), [])
            finally:
                engine.close()
            self.assertEqual(result.files_total, 1)

    def test_merkle_root_is_reported_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _seed(root)
            engine = Engine()
            try:
                first = engine.scan(root)
                second = engine.scan(root)
            finally:
                engine.close()
            self.assertEqual(first.merkle, second.merkle)
            self.assertEqual(len(first.merkle), 64)

    def test_disabled_cache_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CacheStore(Path(directory), enabled=False)
            try:
                store.store("a.py", 1, 2, "d", "r", [])
                self.assertIsNone(store.lookup("a.py", 1, 2, "r"))
                self.assertEqual(store.last_failed("r"), [])
                self.assertEqual(store.index_digest("r"), (0, ""))
                store.clear()
                store.prune_missing([])
            finally:
                store.close()


class RulesetFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_and_compact(self) -> None:
        first = ruleset_fingerprint()
        self.assertEqual(first, ruleset_fingerprint())
        self.assertEqual(len(first), 24)

    def test_editing_a_rule_body_changes_its_digest(self) -> None:
        def original(ctx):
            return [1]

        def edited(ctx):
            return [2]

        # Same code shape but a different constant: bytecode-only hashing would
        # have produced the same fingerprint and kept stale cached verdicts.
        self.assertNotEqual(_code_digest(original), _code_digest(edited))

    def test_nested_code_objects_participate_in_the_digest(self) -> None:
        def make(delta):
            return lambda value: value + delta

        def make_other(delta):
            return lambda value: value - delta

        self.assertNotEqual(_code_digest(make), _code_digest(make_other))

    def test_non_function_detector_has_a_stable_placeholder(self) -> None:
        self.assertEqual(_code_digest(len), "0")


if __name__ == "__main__":
    unittest.main()
