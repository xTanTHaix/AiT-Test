"""AIT-Z01 / Z02 / Z03: process safety detection and patch quality."""

from __future__ import annotations

import ast
import unittest

from tests._support import analyze, analyze_fixture, rule_ids

from ait.models import apply_patch


class PipeDeadlockTests(unittest.TestCase):
    SOURCE = (
        "import subprocess\n"
        "\n"
        "def run(command):\n"
        "    child = subprocess.Popen(command, stdout=subprocess.PIPE)\n"
        "    child.wait()\n"
        "    return child.returncode\n"
    )

    def test_detects_wait_without_drain(self) -> None:
        findings = [item for item in analyze(self.SOURCE) if item.rule_id == "AIT-Z01"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].symbol, "child")
        self.assertEqual(findings[0].location.line, 5)

    def test_communicate_is_not_reported(self) -> None:
        findings = analyze(
            "import subprocess\n"
            "\n"
            "def run(command):\n"
            "    child = subprocess.Popen(command, stdout=subprocess.PIPE)\n"
            "    return child.communicate()[0]\n"
        )
        self.assertNotIn("AIT-Z01", rule_ids(findings))

    def test_context_manager_is_not_reported(self) -> None:
        findings = analyze(
            "import subprocess\n"
            "\n"
            "def run(command):\n"
            "    with subprocess.Popen(command, stdout=subprocess.PIPE) as child:\n"
            "        return child.wait()\n"
        )
        self.assertNotIn("AIT-Z01", rule_ids(findings))

    def test_pipe_without_wait_is_not_a_deadlock(self) -> None:
        findings = analyze(
            "import subprocess\n"
            "\n"
            "def run(command):\n"
            "    child = subprocess.Popen(command, stdout=subprocess.PIPE)\n"
            "    return child.stdout.read()\n"
        )
        self.assertNotIn("AIT-Z01", rule_ids(findings))

    def test_patch_replaces_wait_with_communicate(self) -> None:
        findings = [item for item in analyze(self.SOURCE) if item.rule_id == "AIT-Z01"]
        patch = findings[0].patch
        self.assertIsNotNone(patch)
        patched = apply_patch(self.SOURCE, patch)  # type: ignore[arg-type]
        ast.parse(patched)
        self.assertIn("communicate()", patched)
        self.assertNotIn("child.wait()", patched)


class ZombieChildTests(unittest.TestCase):
    def test_detects_child_without_reaper(self) -> None:
        findings = [
            item
            for item in analyze(
                "import subprocess\n"
                "\n"
                "def launch(command):\n"
                "    child = subprocess.Popen(command)\n"
                "    return child.pid\n"
            )
            if item.rule_id == "AIT-Z02"
        ]
        self.assertEqual(len(findings), 1)

    def test_wait_satisfies_the_reaper_requirement(self) -> None:
        findings = analyze(
            "import subprocess\n"
            "\n"
            "def launch(command):\n"
            "    child = subprocess.Popen(command)\n"
            "    child.wait()\n"
        )
        self.assertNotIn("AIT-Z02", rule_ids(findings))

    def test_handing_ownership_back_is_accepted(self) -> None:
        findings = analyze(
            "import subprocess\n"
            "\n"
            "def launch(command):\n"
            "    child = subprocess.Popen(command)\n"
            "    return child\n"
        )
        self.assertNotIn("AIT-Z02", rule_ids(findings))

    def test_daemon_process_is_not_reported(self) -> None:
        findings = analyze(
            "import multiprocessing\n"
            "\n"
            "def launch(target):\n"
            "    child = multiprocessing.Process(target=target, daemon=True)\n"
            "    child.start()\n"
        )
        self.assertNotIn("AIT-Z02", rule_ids(findings))


class ImportTimeSpawnTests(unittest.TestCase):
    def test_detects_unguarded_module_scope_spawn(self) -> None:
        findings = analyze(
            "import multiprocessing\n"
            "\n"
            "def work():\n"
            "    return None\n"
            "\n"
            "process = multiprocessing.Process(target=work)\n"
            "process.start()\n"
        )
        self.assertEqual(rule_ids(findings) & {"AIT-Z03"}, {"AIT-Z03"})

    def test_main_guard_is_accepted(self) -> None:
        findings = analyze(
            "import multiprocessing\n"
            "\n"
            "def work():\n"
            "    return None\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    process = multiprocessing.Process(target=work)\n"
            "    process.start()\n"
            "    process.join()\n"
        )
        self.assertNotIn("AIT-Z03", rule_ids(findings))

    def test_function_scope_spawn_is_not_an_import_time_spawn(self) -> None:
        findings = analyze(
            "import multiprocessing\n"
            "\n"
            "def work():\n"
            "    return None\n"
            "\n"
            "def launch():\n"
            "    process = multiprocessing.Process(target=work)\n"
            "    process.start()\n"
            "    process.join()\n"
        )
        self.assertNotIn("AIT-Z03", rule_ids(findings))


class ProcessFixtureTests(unittest.TestCase):
    def test_fixture_reports_all_three_process_rules(self) -> None:
        self.assertEqual(
            rule_ids(analyze_fixture("bad_process.py")),
            {"AIT-Z01", "AIT-Z02", "AIT-Z03"},
        )

    def test_clean_fixture_is_silent(self) -> None:
        self.assertEqual(analyze_fixture("clean_process.py"), [])


if __name__ == "__main__":
    unittest.main()
