"""End-to-end engine tests: discovery, parsing, severity policy and diffing."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from tests._support import (
    BAD_FIXTURES,
    CLEAN_FIXTURES,
    FIXTURES,
    SRC_DIR,
    analyze,
    analyze_fixture,
    rule_ids,
)

from ait.engine import (
    Engine,
    UsageError,
    compare_findings,
    decode_source,
    discover_files,
    is_ignored,
)
from ait.models import EXIT_CLEAN, EXIT_DEFECT, EXIT_INTERNAL, Severity


class DiscoveryTests(unittest.TestCase):
    def test_discovers_fixtures_in_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("b.py", "a.py"):
                (root / name).write_text("x = 1\n", encoding="utf-8")
            files = discover_files(root, root)
            self.assertEqual([item.relative for item in files], ["a.py", "b.py"])

    def test_skips_virtualenv_and_cache_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".venv").mkdir()
            (root / ".venv" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
            (root / "kept.py").write_text("x = 1\n", encoding="utf-8")
            files = discover_files(root, root)
            self.assertEqual([item.relative for item in files], ["kept.py"])

    def test_honours_aitignore_globs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".aitignore").write_text("# comment\nlegacy_*.py\nbuild/\n", encoding="utf-8")
            (root / "legacy_thing.py").write_text("x = 1\n", encoding="utf-8")
            (root / "kept.py").write_text("x = 1\n", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "generated.py").write_text("x = 1\n", encoding="utf-8")
            from ait.engine import load_ignore_patterns

            patterns = load_ignore_patterns(root)
            files = discover_files(root, root, patterns)
            self.assertEqual([item.relative for item in files], ["kept.py"])

    def test_ignore_matcher_accepts_globs_and_directories(self) -> None:
        self.assertTrue(is_ignored("pkg/mod.py", ("pkg/*.py",)))
        self.assertTrue(is_ignored("legacy_old.py", ("legacy_*.py",)))
        self.assertFalse(is_ignored("pkg/mod.py", ("other/*.py",)))


class ParsingTests(unittest.TestCase):
    def test_bom_file_is_decoded_and_parsed(self) -> None:
        raw = b"\xef\xbb\xbfvalue = 1\n"
        decoded = decode_source(raw)
        self.assertNotIn("\ufeff", decoded)
        self.assertEqual(decoded, "value = 1\n")
        self.assertEqual(analyze(decoded), [])

    def test_cp874_bytes_do_not_crash_decoding(self) -> None:
        raw = "# ชื่อไฟล์\nvalue = 1\n".encode("cp874")
        decoded = decode_source(raw)
        self.assertIn("value = 1", decoded)

    def test_syntax_error_is_reported_with_line(self) -> None:
        with self.assertRaises(UsageError) as caught:
            analyze("def broken(:\n    pass\n")
        self.assertIn("line", str(caught.exception))

    def test_scan_marks_unparsable_file_and_returns_exit_3(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            engine = Engine(use_cache=False)
            try:
                result = engine.scan(root, use_cache=False)
            finally:
                engine.close()
            self.assertEqual(result.exit_code, EXIT_INTERNAL)
            self.assertEqual([item[0] for item in result.unparsable], ["broken.py"])
            self.assertIn("SyntaxError", result.unparsable[0][1])


class SeverityPolicyTests(unittest.TestCase):
    def test_defect_fixtures_are_detected(self) -> None:
        for name in BAD_FIXTURES:
            with self.subTest(fixture=name):
                self.assertTrue(analyze_fixture(name), f"{name} produced no findings")

    def test_clean_fixtures_produce_no_findings(self) -> None:
        for name in CLEAN_FIXTURES:
            with self.subTest(fixture=name):
                self.assertEqual(analyze_fixture(name), [], f"{name} produced findings")

    def test_race_without_concurrency_evidence_is_not_a_defect(self) -> None:
        findings = analyze(
            "class Box:\n"
            "    def __init__(self):\n"
            "        self.total = 0\n"
            "\n"
            "    def bump(self):\n"
            "        self.total += 1\n"
        )
        raced = [item for item in findings if item.rule_id == "AIT-R01"]
        self.assertEqual(raced, [], "single-threaded mutation must not be reported")

    def test_race_with_spawn_path_is_a_defect(self) -> None:
        findings = analyze(
            "import threading\n"
            "\n"
            "class Box:\n"
            "    def __init__(self):\n"
            "        self._lock = threading.Lock()\n"
            "        self.total = 0\n"
            "\n"
            "    def bump(self):\n"
            "        self.total += 1\n"
            "\n"
            "def run():\n"
            "    box = Box()\n"
            "    threading.Thread(target=box.bump).start()\n"
        )
        raced = [item for item in findings if item.rule_id == "AIT-R01"]
        self.assertEqual(len(raced), 1)
        self.assertIs(raced[0].severity, Severity.DEFECT)
        self.assertGreaterEqual(raced[0].confidence, 0.9)

    def test_every_finding_carries_confidence_and_evidence(self) -> None:
        for name in BAD_FIXTURES:
            for item in analyze_fixture(name):
                with self.subTest(fixture=name, rule=item.rule_id):
                    self.assertTrue(0.0 <= item.confidence <= 1.0)
                    self.assertTrue(item.evidence)
                    self.assertTrue(item.message.strip())
                    self.assertGreaterEqual(item.location.line, 1)


class RegressionDiffTests(unittest.TestCase):
    def test_line_shift_does_not_create_phantom_findings(self) -> None:
        before = analyze(
            "from typing import Optional\n"
            "\n"
            "def use(value: Optional[str]) -> None:\n"
            "    print(value.upper())\n"
        )
        after = analyze(
            "from typing import Optional\n"
            "\n"
            "# a comment that shifts every line by one\n"
            "def use(value: Optional[str]) -> None:\n"
            "    print(value.upper())\n"
        )
        resolved, introduced = compare_findings(before, after)
        self.assertEqual(resolved, [])
        self.assertEqual(introduced, [])

    def test_fixed_finding_is_reported_as_resolved(self) -> None:
        before = analyze(
            "from typing import Optional\n"
            "\n"
            "def use(value: Optional[str]) -> None:\n"
            "    print(value.upper())\n"
        )
        after = analyze(
            "from typing import Optional\n"
            "\n"
            "def use(value: Optional[str]) -> None:\n"
            "    if value is None:\n"
            "        return\n"
            "    print(value.upper())\n"
        )
        resolved, introduced = compare_findings(before, after)
        self.assertEqual(rule_ids(resolved), {"AIT-N01"})
        self.assertEqual(introduced, [])

    def test_new_finding_is_reported_as_introduced(self) -> None:
        before = analyze("value = 1\n")
        after = analyze(
            "def add(tags: list, tag: str) -> None:\n"
            "    tags.append(tag)\n"
        )
        resolved, introduced = compare_findings(before, after)
        self.assertEqual(resolved, [])
        self.assertEqual(rule_ids(introduced), {"AIT-M01"})


class VerifyFixTests(unittest.TestCase):
    def test_verify_fix_reports_clean_candidate(self) -> None:
        engine = Engine(use_cache=False)
        try:
            payload = engine.verify_fix(
                "sample.py",
                "from typing import Optional\n"
                "def use(value: Optional[str]) -> None:\n"
                "    print(value.upper())\n",
                "from typing import Optional\n"
                "def use(value: Optional[str]) -> None:\n"
                "    if value is None:\n"
                "        return\n"
                "    print(value.upper())\n",
            )
        finally:
            engine.close()
        self.assertTrue(payload["clean"])
        self.assertFalse(payload["regression"])
        self.assertEqual(
            [item["rule_id"] for item in payload["resolved"]], ["AIT-N01"]
        )

    def test_verify_fix_rejects_unparsable_candidate(self) -> None:
        engine = Engine(use_cache=False)
        try:
            with self.assertRaises(UsageError):
                engine.verify_fix("sample.py", "x = 1\n", "def broken(:\n")
        finally:
            engine.close()


class ScanSummaryTests(unittest.TestCase):
    def test_directory_scan_tallies_and_exit_code(self) -> None:
        engine = Engine(use_cache=False)
        try:
            result = engine.scan(FIXTURES, use_cache=False)
        finally:
            engine.close()
        self.assertEqual(result.exit_code, EXIT_DEFECT)
        self.assertEqual(result.files_total, len(BAD_FIXTURES) + len(CLEAN_FIXTURES))
        self.assertEqual(result.files_processed, result.files_total)
        tally = result.tally()
        self.assertGreater(tally["DEFECT"], 0)
        self.assertEqual(set(tally), {"DEFECT", "WARNING", "ADVICE"})

    def test_clean_directory_returns_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clean.py").write_text(
                "def add(left: int, right: int) -> int:\n    return left + right\n",
                encoding="utf-8",
            )
            engine = Engine(use_cache=False)
            try:
                result = engine.scan(root, use_cache=False)
            finally:
                engine.close()
            self.assertEqual(result.exit_code, EXIT_CLEAN)
            self.assertEqual(result.findings, [])

    def test_exitfirst_stops_after_first_failing_file(self) -> None:
        engine = Engine(use_cache=False)
        try:
            result = engine.scan(FIXTURES, use_cache=False, exitfirst=True)
        finally:
            engine.close()
        self.assertEqual(result.files_processed, 1)
        self.assertLess(result.files_processed, result.files_total)

    def test_self_scan_of_package_is_clean(self) -> None:
        engine = Engine(use_cache=False)
        try:
            result = engine.scan(SRC_DIR, use_cache=False)
        finally:
            engine.close()
        defects = [item for item in result.findings if item.severity is Severity.DEFECT]
        self.assertEqual(defects, [], f"self-scan defects: {[d.message for d in defects]}")

    def test_analyze_tree_accepts_prebuilt_ast(self) -> None:
        engine = Engine(use_cache=False)
        source = "value = 1\n"
        tree = ast.parse(source)
        try:
            self.assertEqual(engine.analyze_tree("sample.py", source, tree), [])
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main()
