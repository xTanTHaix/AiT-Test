"""Black-box CLI behaviour: exit codes, flags, cache and the fix session."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._support import FIXTURES, REPO_ROOT

from ait.cache import CACHE_DIRNAME
from ait.cli import (
    BACKUP_DIRNAME,
    _apply_all,
    _apply_single,
    _encode_like,
    _patch_order,
    main as cli_main,
)
from ait.engine import Engine, decode_source, resolve_encoding
from ait.reporter import Reporter, configure_stream

CLI = REPO_ROOT / "cli.py"

VULNERABLE = (
    "from typing import Optional\n"
    "\n"
    "def use(value: Optional[str]) -> None:\n"
    "    print(value.upper())\n"
)

CLEAN = "def add(left: int, right: int) -> int:\n    return left + right\n"


def run_cli(
    *arguments: str,
    cwd: Path = REPO_ROOT,
    input_text: str | None = None,
    stdin=None,
) -> subprocess.CompletedProcess:
    """Run the CLI in a child process with colour disabled and UTF-8 output."""
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["NO_COLOR"] = "1"
    streams = {"input": input_text} if input_text is not None else {}
    if stdin is not None:
        streams["stdin"] = stdin
    return subprocess.run(
        [sys.executable, str(CLI), *arguments, "--no-color"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=180,
        **streams,
    )


def write_source(path: Path, text: str) -> None:
    """Write LF-only source so patch coordinates match on every platform."""
    path.write_bytes(text.encode("utf-8"))


class ColourPolicyTests(unittest.TestCase):
    def test_no_color_environment_variable_beats_a_tty(self) -> None:
        class _TtyBuffer(io.StringIO):
            def isatty(self) -> bool:
                return True

        buffer = _TtyBuffer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clean.py").write_text(CLEAN, encoding="utf-8")
            with mock.patch.dict(os.environ, {"NO_COLOR": "1"}), mock.patch.object(
                sys, "stdout", buffer
            ):
                code = cli_main([str(root), "--no-cache"])
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b[", buffer.getvalue())


class ExitCodeTests(unittest.TestCase):
    def test_clean_target_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clean.py").write_text(CLEAN, encoding="utf-8")
            result = run_cli(str(root), "--no-cache")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("0 failed, 1 passed", result.stdout)
            self.assertIn("DEFECT 0", result.stdout)

    def test_defect_target_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.py").write_text(VULNERABLE, encoding="utf-8")
            result = run_cli(str(root), "--no-cache")
            self.assertEqual(result.returncode, 1)
            self.assertIn("FAILURES", result.stdout)
            self.assertIn("[AIT-N01]", result.stdout)
            self.assertIn("exit code: 1", result.stdout)

    def test_advice_only_target_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deep.py").write_text(
                "def classify(rows, flag) -> str:\n"
                "    for row in rows:\n"
                "        for cell in row:\n"
                "            if flag:\n"
                "                if cell:\n"
                "                    if isinstance(cell, int):\n"
                "                        if cell % 2 == 0:\n"
                "                            return 'even'\n"
                "    return 'other'\n",
                encoding="utf-8",
            )
            result = run_cli(str(root), "--no-cache")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("AIT-H02", result.stdout)

    def test_syntax_error_exits_three(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            result = run_cli(str(root), "--no-cache")
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn("SyntaxError", result.stdout + result.stderr)

    def test_unparsable_file_is_not_counted_as_passed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            result = run_cli(str(root), "--no-cache")
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn("0 failed, 0 passed", result.stdout)
            self.assertIn("1 unparsable", result.stdout)

    def test_empty_target_exits_four(self) -> None:
        result = run_cli("")
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("Target is empty", result.stderr)

    def test_usage_error_is_written_to_stderr_only(self) -> None:
        result = run_cli("definitely-not-a-real-path", "--no-cache")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("ERROR:", result.stderr)

    def test_missing_target_exits_four(self) -> None:
        result = run_cli("definitely-not-a-real-path", "--no-cache")
        self.assertEqual(result.returncode, 4)
        self.assertIn("not accessible", result.stdout + result.stderr)

    def test_unknown_flag_exits_four(self) -> None:
        result = run_cli("--definitely-not-a-flag")
        self.assertEqual(result.returncode, 4)

    def test_lf_without_cache_exits_four(self) -> None:
        result = run_cli("--lf", "--no-cache")
        self.assertEqual(result.returncode, 4)
        self.assertIn("--lf needs the cache", result.stdout + result.stderr)

    def test_negative_maxfail_exits_four(self) -> None:
        result = run_cli("--maxfail", "-1")
        self.assertEqual(result.returncode, 4)

    def test_version_exits_zero(self) -> None:
        result = run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("ait-engine 1.0.0", result.stdout)


class FlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        write_source(self.root / "bad.py", VULNERABLE)
        write_source(self.root / "clean.py", CLEAN)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_quiet_hides_failure_blocks(self) -> None:
        result = run_cli(str(self.root), "--no-cache", "-q")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("FAILURES", result.stdout)
        self.assertNotIn("[AIT-N01]", result.stdout)
        self.assertIn("1 failed", result.stdout)

    def test_exitfirst_analyses_only_the_first_failing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a_bad.py").write_text(VULNERABLE, encoding="utf-8")
            (root / "b_bad.py").write_text(VULNERABLE, encoding="utf-8")
            result = run_cli(str(root), "--no-cache", "-x")
            self.assertEqual(result.returncode, 1)
            self.assertIn("a_bad.py", result.stdout)
            self.assertNotIn("b_bad.py", result.stdout)
            self.assertIn("0 passed", result.stdout)

    def test_maxfail_limits_rendered_blocks(self) -> None:
        two_findings = (
            "from typing import Optional\n"
            "\n"
            "def use(first: Optional[str], second: Optional[str]) -> None:\n"
            "    print(first.upper())\n"
            "    print(second.upper())\n"
        )
        write_source(self.root / "two.py", two_findings)
        result = run_cli(str(self.root), "--no-cache", "--maxfail", "1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("suppressed by --maxfail", result.stdout)
        self.assertEqual(result.stdout.count("[AIT-N01]"), 1)

    def test_verbose_does_not_change_the_exit_code(self) -> None:
        result = run_cli(str(self.root), "--no-cache", "-v")
        self.assertEqual(result.returncode, 1)

    def test_cache_is_created_and_reused(self) -> None:
        first = run_cli(str(self.root))
        self.assertEqual(first.returncode, 1)
        self.assertTrue((self.root / CACHE_DIRNAME).is_dir())
        second = run_cli(str(self.root))
        self.assertEqual(second.returncode, 1)
        self.assertIn("cached", second.stdout)

    def test_no_cache_leaves_no_cache_directory(self) -> None:
        run_cli(str(self.root), "--no-cache")
        self.assertFalse((self.root / CACHE_DIRNAME).exists())

    def test_last_failed_narrows_the_next_run(self) -> None:
        run_cli(str(self.root))
        result = run_cli(str(self.root), "--lf")
        self.assertEqual(result.returncode, 1)
        self.assertIn("collecting 1 files", result.stdout)
        self.assertNotIn("clean.py", result.stdout)

    def test_lf_with_a_degraded_cache_falls_back_to_a_full_scan(self) -> None:
        # A cache that cannot be opened knows nothing about the previous run.
        # Narrowing to "last failed" would analyse zero files and report clean.
        (self.root / CACHE_DIRNAME).write_bytes(b"not a directory")
        result = run_cli(str(self.root), "--lf")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--lf needs a usable cache", result.stderr)
        self.assertIn("collecting 2 files", result.stdout)
        self.assertIn("[AIT-N01]", result.stdout)

    def test_lf_with_an_empty_index_falls_back_to_a_full_scan(self) -> None:
        # Deleting cache.db leaves an enabled but empty index: it proves nothing
        # about the previous run, so --lf must scan every file and say so.
        run_cli(str(self.root))
        (self.root / CACHE_DIRNAME / "cache.db").unlink()
        result = run_cli(str(self.root), "--lf")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no previous run in the cache", result.stderr)
        self.assertIn("collecting 2 files", result.stdout)

    def test_suppressed_findings_are_counted_in_the_summary(self) -> None:
        write_source(
            self.root / "silent.py",
            "def risky():\n"
            "    try:\n"
            "        return 1\n"
            "    except:  # ait: disable=H01\n"
            "        return 0\n",
        )
        result = run_cli(str(self.root), "--no-cache", "-q")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("[AIT-H01]", result.stdout)
        self.assertIn("1 suppressed", result.stdout)

    def test_fix_without_a_terminal_changes_nothing(self) -> None:
        before = (self.root / "bad.py").read_bytes()
        result = run_cli(str(self.root), "--no-cache", "--fix", input_text="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("requires an interactive terminal", result.stdout + result.stderr)
        self.assertEqual((self.root / "bad.py").read_bytes(), before)
        self.assertFalse((self.root / BACKUP_DIRNAME).exists())

    def test_fix_with_devnull_stdin_never_prompts(self) -> None:
        # Windows NUL is a character device, so ``isatty()`` still reports True
        # there; the session must not render a prompt nobody can answer.
        before = (self.root / "bad.py").read_bytes()
        result = run_cli(
            str(self.root), "--no-cache", "--fix", stdin=subprocess.DEVNULL
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("Apply ", result.stdout)
        self.assertIn("requires an interactive terminal", result.stderr)
        self.assertEqual((self.root / "bad.py").read_bytes(), before)
        self.assertFalse((self.root / BACKUP_DIRNAME).exists())

    def test_thai_source_is_analysed_without_crashing(self) -> None:
        (self.root / "thai.py").write_text(
            "# ตรวจสอบภาษาไทย\n"
            "def greet(name: str) -> str:\n"
            "    return f'สวัสดี {name}'\n",
            encoding="utf-8",
        )
        result = run_cli(str(self.root), "--no-cache")
        self.assertEqual(result.returncode, 1)
        self.assertIn("1 failed, 2 passed", result.stdout)


class FixSessionUnitTests(unittest.TestCase):
    """Exercise ``_apply_single`` directly: no TTY is available in CI."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.path = self.root / "bad.py"
        write_source(self.path, VULNERABLE)
        self.engine = Engine(use_cache=False)

    def tearDown(self) -> None:
        self.engine.close()
        self._temporary.cleanup()

    def _finding(self):
        findings = [
            item
            for item in self.engine.analyze_source("bad.py", VULNERABLE)
            if item.patch is not None
        ]
        self.assertEqual(len(findings), 1)
        return findings[0]

    def test_apply_writes_patch_and_backs_up_the_original(self) -> None:
        finding = self._finding()
        backup_root = self.root / BACKUP_DIRNAME / "run"
        ok, message = _apply_single(
            self.engine, self.path, "bad.py", finding, backup_root
        )
        self.assertTrue(ok, message)
        patched = self.path.read_text(encoding="utf-8")
        self.assertIn("if value is None:", patched)
        backup = backup_root / "bad.py"
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding="utf-8"), VULNERABLE)

    def test_patch_is_rejected_when_the_source_drifted(self) -> None:
        finding = self._finding()
        write_source(self.path, VULNERABLE.replace("upper", "lower"))
        ok, message = _apply_single(
            self.engine, self.path, "bad.py", finding, self.root / BACKUP_DIRNAME / "x"
        )
        self.assertFalse(ok)
        self.assertIn("bad.py", message)
        self.assertIn("lower", self.path.read_text(encoding="utf-8"))

    def test_applying_a_patch_preserves_lf_line_endings(self) -> None:
        finding = self._finding()
        ok, _message = _apply_single(
            self.engine, self.path, "bad.py", finding, self.root / BACKUP_DIRNAME / "x"
        )
        self.assertTrue(ok)
        raw = self.path.read_bytes()
        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"\n"))

    def test_applying_a_patch_to_a_crlf_file_keeps_crlf(self) -> None:
        crlf = VULNERABLE.replace("\n", "\r\n")
        self.path.write_bytes(crlf.encode("utf-8"))
        findings = [
            item
            for item in self.engine.analyze_source("bad.py", crlf)
            if item.patch is not None
        ]
        self.assertEqual(len(findings), 1)
        ok, message = _apply_single(
            self.engine,
            self.path,
            "bad.py",
            findings[0],
            self.root / BACKUP_DIRNAME / "x",
        )
        self.assertTrue(ok, message)
        raw = self.path.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n\n", raw)
        self.assertIn(b"if value is None:", raw)

    def test_no_temporary_file_is_left_behind(self) -> None:
        finding = self._finding()
        _apply_single(
            self.engine, self.path, "bad.py", finding, self.root / BACKUP_DIRNAME / "x"
        )
        leftovers = list(self.root.glob("*.ait-tmp"))
        self.assertEqual(leftovers, [])


class PatchLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.path = self.root / "bad.py"
        write_source(self.path, VULNERABLE)
        self.engine = Engine(use_cache=False)
        self.output = io.StringIO()
        configure_stream(self.output)
        self.reporter = Reporter(self.output, verbose=False, quiet=True, use_color=False)

    def tearDown(self) -> None:
        self.engine.close()
        self._temporary.cleanup()

    def _by_file(self):
        finding = next(
            item
            for item in self.engine.analyze_source("bad.py", VULNERABLE)
            if item.patch is not None
        )
        return {"bad.py": [finding]}

    def test_apply_all_applies_every_confirmed_patch(self) -> None:
        with mock.patch("builtins.input", return_value="a"):
            applied, skipped = _apply_all(
                self.engine,
                self.reporter,
                self.root,
                self._by_file(),
                self.root / BACKUP_DIRNAME / "run",
            )
        self.assertEqual((applied, skipped), (1, 0))
        self.assertIn("if value is None:", self.path.read_text(encoding="utf-8"))

    def test_apply_all_stops_on_quit_without_touching_the_file(self) -> None:
        before = self.path.read_bytes()
        with mock.patch("builtins.input", return_value="q"):
            applied, skipped = _apply_all(
                self.engine,
                self.reporter,
                self.root,
                self._by_file(),
                self.root / BACKUP_DIRNAME / "run",
            )
        self.assertEqual((applied, skipped), (0, 0))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / BACKUP_DIRNAME).exists())

    def test_patch_order_is_bottom_up(self) -> None:
        findings = self.engine.analyze_source(
            "bad.py",
            "from typing import Optional\n"
            "\n"
            "def use(a: Optional[str], b: Optional[str]) -> None:\n"
            "    print(a.upper())\n"
            "    print(b.upper())\n",
        )
        ordered = _patch_order(findings)
        lines = [item.patch.start_line for item in ordered if item.patch]
        self.assertEqual(lines, sorted(lines, reverse=True))


class EncodingTests(unittest.TestCase):
    """``--fix`` must write back the codec the file actually used."""

    THAI = (
        "# -*- coding: cp874 -*-\n"
        "from typing import Optional\n"
        "TITLE = 'ทดสอบ'\n"
        "\n"
        "def use(value: Optional[str]) -> None:\n"
        "    print(value.upper())\n"
    )

    def test_utf8_bom_is_preserved(self) -> None:
        raw = b"\xef\xbb\xbf" + VULNERABLE.encode("utf-8")
        text = decode_source(raw)
        self.assertFalse(text.startswith("\ufeff"))
        encoded = _encode_like(text, raw)
        assert encoded is not None
        self.assertTrue(encoded.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(decode_source(encoded), text)

    def test_plain_utf8_source_gains_no_bom(self) -> None:
        raw = VULNERABLE.encode("utf-8")
        encoded = _encode_like(decode_source(raw), raw)
        assert encoded is not None
        self.assertFalse(encoded.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(encoded, raw)

    def test_apply_single_does_not_add_a_bom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "plain.py"
            raw = VULNERABLE.encode("utf-8")
            path.write_bytes(raw)
            engine = Engine(use_cache=False)
            try:
                finding = next(
                    item
                    for item in engine.analyze_source("plain.py", decode_source(raw))
                    if item.patch is not None
                )
                ok, message = _apply_single(
                    engine, path, "plain.py", finding, root / BACKUP_DIRNAME / "run"
                )
            finally:
                engine.close()
            self.assertTrue(ok, message)
            written = path.read_bytes()
            self.assertNotEqual(written, raw)
            self.assertFalse(written.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"if value is None:", written)

    def test_cp874_source_is_re_encoded_as_cp874(self) -> None:
        raw = self.THAI.encode("cp874")
        self.assertEqual(resolve_encoding(raw), "cp874")
        text = decode_source(raw)
        patched = text + "# trailing comment\n"
        encoded = _encode_like(patched, raw)
        assert encoded is not None
        self.assertEqual(encoded, patched.encode("cp874"))
        self.assertEqual(decode_source(encoded), patched)

    def test_unrepresentable_patch_is_refused(self) -> None:
        raw = self.THAI.encode("cp874")
        # U+2713 has no cp874 code point: writing UTF-8 anyway would leave the
        # coding cookie lying about the file's contents.
        encoded = _encode_like(decode_source(raw) + "# ✓\n", raw)
        self.assertIsNone(encoded)

    def test_apply_single_round_trips_a_cp874_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "thai.py"
            raw = self.THAI.encode("cp874")
            path.write_bytes(raw)
            engine = Engine(use_cache=False)
            try:
                finding = next(
                    item
                    for item in engine.analyze_source("thai.py", decode_source(raw))
                    if item.patch is not None
                )
                backup_root = root / BACKUP_DIRNAME / "run"
                ok, message = _apply_single(
                    engine, path, "thai.py", finding, backup_root
                )
            finally:
                engine.close()
            self.assertTrue(ok, message)
            written = path.read_bytes()
            self.assertNotEqual(written, raw)
            self.assertIn("if value is None:", written.decode("cp874"))
            self.assertIn("ทดสอบ", written.decode("cp874"))
            self.assertEqual((backup_root / "thai.py").read_bytes(), raw)
            self.assertEqual(list(root.glob("*.ait-tmp")), [])


class SelfScanTests(unittest.TestCase):
    def test_package_scan_is_clean(self) -> None:
        result = run_cli(str(REPO_ROOT / "src"), "--no-cache")
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        self.assertIn("DEFECT 0", result.stdout)


class FixtureCorpusTests(unittest.TestCase):
    """The corpus is deliberately defective: a clean exit means the rules went silent."""

    def test_corpus_exits_one_and_reports_defects(self) -> None:
        result = run_cli(str(FIXTURES), "--no-cache")
        self.assertEqual(result.returncode, 1, result.stdout[-4000:])
        self.assertIn("DEFECT", result.stdout)


if __name__ == "__main__":
    unittest.main()
