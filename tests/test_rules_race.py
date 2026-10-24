"""AIT-R01 / R02: concurrency hazards and evidence-driven severity."""

from __future__ import annotations

import ast
import unittest

from tests._support import analyze, analyze_fixture, rule_ids

from ait.models import Severity, apply_patch

SPAWNED_COUNTER = (
    "import threading\n"
    "\n"
    "class Counter:\n"
    "    def __init__(self):\n"
    "        self._lock = threading.Lock()\n"
    "        self.total = 0\n"
    "\n"
    "    def bump(self):\n"
    "        self.total += 1\n"
    "\n"
    "def run():\n"
    "    counter = Counter()\n"
    "    worker = threading.Thread(target=counter.bump)\n"
    "    worker.start()\n"
    "    worker.join()\n"
)

DOUBLE_CHECKED = (
    "import threading\n"
    "\n"
    "class Registry:\n"
    "    def __init__(self):\n"
    "        self._lock = threading.Lock()\n"
    "        self._value = None\n"
    "\n"
    "    def get(self):\n"
    "        if self._value is None:\n"
    "            with self._lock:\n"
    "                if self._value is None:\n"
    "                    self._value = 'built'\n"
    "        return self._value\n"
)


class UnlockedMutationTests(unittest.TestCase):
    def test_detects_mutation_reachable_from_a_spawn_site(self) -> None:
        findings = [item for item in analyze(SPAWNED_COUNTER) if item.rule_id == "AIT-R01"]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.DEFECT)
        self.assertEqual(findings[0].symbol, "self.total")
        self.assertEqual(findings[0].location.line, 9)

    def test_mutation_inside_the_lock_is_silent(self) -> None:
        findings = analyze(SPAWNED_COUNTER.replace(
            "        self.total += 1\n",
            "        with self._lock:\n            self.total += 1\n",
        ))
        self.assertNotIn("AIT-R01", rule_ids(findings))

    def test_without_any_concurrency_hint_nothing_is_reported(self) -> None:
        findings = analyze(
            "class Counter:\n"
            "    def __init__(self):\n"
            "        self._lock = object()\n"
            "        self.total = 0\n"
            "\n"
            "    def bump(self):\n"
            "        self.total += 1\n"
        )
        self.assertEqual(findings, [])

    def test_async_def_counts_as_a_concurrency_hint(self) -> None:
        findings = analyze(
            "class Counter:\n"
            "    def __init__(self):\n"
            "        self._lock = object()\n"
            "        self.total = 0\n"
            "\n"
            "    def bump(self):\n"
            "        self.total += 1\n"
            "\n"
            "async def main() -> None:\n"
            "    Counter().bump()\n"
        )
        raced = [item for item in findings if item.rule_id == "AIT-R01"]
        self.assertEqual(len(raced), 1)
        self.assertIs(raced[0].severity, Severity.WARNING)
        self.assertLess(raced[0].confidence, 0.9)

    def test_daemon_thread_spawn_is_still_a_spawn_site(self) -> None:
        findings = analyze(
            "import threading\n"
            "\n"
            "class Box:\n"
            "    def __init__(self):\n"
            "        self._lock = threading.Lock()\n"
            "        self.count = 0\n"
            "\n"
            "    def bump(self):\n"
            "        self.count += 1\n"
            "\n"
            "def run():\n"
            "    box = Box()\n"
            "    threading.Thread(target=box.bump, daemon=True).start()\n"
        )
        self.assertIn("AIT-R01", rule_ids(findings))

    def test_global_counter_mutation_is_reported(self) -> None:
        findings = analyze(
            "import threading\n"
            "\n"
            "TOTAL = 0\n"
            "\n"
            "def bump():\n"
            "    global TOTAL\n"
            "    TOTAL += 1\n"
            "\n"
            "def run():\n"
            "    threading.Thread(target=bump).start()\n"
        )
        raced = [item for item in findings if item.rule_id == "AIT-R01"]
        self.assertEqual(len(raced), 1)
        self.assertEqual(raced[0].symbol, "TOTAL")

    def test_plain_assignment_read_modify_write_is_a_defect(self) -> None:
        findings = analyze(
            SPAWNED_COUNTER.replace(
                "        self.total += 1\n",
                "        self.total = self.total + 1\n",
            )
        )
        raced = [item for item in findings if item.rule_id == "AIT-R01"]
        self.assertEqual(len(raced), 1)
        self.assertIs(raced[0].severity, Severity.DEFECT)
        self.assertEqual(raced[0].symbol, "self.total")

    def test_global_read_modify_write_assignment_is_reported(self) -> None:
        findings = analyze(
            "import threading\n"
            "\n"
            "TOTAL = 0\n"
            "\n"
            "def bump():\n"
            "    global TOTAL\n"
            "    TOTAL = TOTAL + 1\n"
            "\n"
            "def run():\n"
            "    threading.Thread(target=bump).start()\n"
        )
        self.assertIn("AIT-R01", rule_ids(findings))

    def test_plain_assignment_is_not_a_race(self) -> None:
        findings = analyze(
            SPAWNED_COUNTER.replace(
                "        self.total += 1\n",
                "        self.total = compute()\n",
            )
        )
        self.assertNotIn("AIT-R01", rule_ids(findings))

    def test_one_finding_per_symbol_not_per_statement(self) -> None:
        findings = analyze(
            "import threading\n"
            "\n"
            "class Box:\n"
            "    def __init__(self):\n"
            "        self._lock = threading.Lock()\n"
            "        self.count = 0\n"
            "\n"
            "    def bump(self):\n"
            "        self.count += 1\n"
            "        self.count += 1\n"
            "\n"
            "def run():\n"
            "    box = Box()\n"
            "    threading.Thread(target=box.bump).start()\n"
        )
        raced = [item for item in findings if item.rule_id == "AIT-R01"]
        self.assertEqual(len(raced), 1)

    def test_patch_wraps_the_statement_in_the_existing_lock(self) -> None:
        findings = [item for item in analyze(SPAWNED_COUNTER) if item.rule_id == "AIT-R01"]
        patch = findings[0].patch
        self.assertIsNotNone(patch)
        patched = apply_patch(SPAWNED_COUNTER, patch)  # type: ignore[arg-type]
        ast.parse(patched)
        self.assertIn("with self._lock:", patched)
        lines = patched.splitlines()
        index = lines.index("        with self._lock:")
        self.assertEqual(lines[index + 1], "            self.total += 1")

    def test_causation_ends_at_the_unprotected_statement(self) -> None:
        findings = [item for item in analyze(SPAWNED_COUNTER) if item.rule_id == "AIT-R01"]
        trace = findings[0].causation
        self.assertGreaterEqual(len(trace), 2)
        self.assertEqual([step.index for step in trace], list(range(1, len(trace) + 1)))
        self.assertIn("lock", trace[-1].note.lower())


class DoubleCheckLockTests(unittest.TestCase):
    def test_detects_the_double_checked_pattern(self) -> None:
        findings = [item for item in analyze(DOUBLE_CHECKED) if item.rule_id == "AIT-R02"]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.WARNING)
        self.assertEqual(findings[0].location.line, 11)

    def test_single_check_inside_the_lock_is_silent(self) -> None:
        findings = analyze(
            "import threading\n"
            "\n"
            "class Registry:\n"
            "    def __init__(self):\n"
            "        self._lock = threading.Lock()\n"
            "        self._value = None\n"
            "\n"
            "    def get(self):\n"
            "        with self._lock:\n"
            "            if self._value is None:\n"
            "                self._value = 'built'\n"
            "        return self._value\n"
        )
        self.assertNotIn("AIT-R02", rule_ids(findings))

    def test_repeated_check_without_a_lock_is_not_the_pattern(self) -> None:
        findings = analyze(
            "class Registry:\n"
            "    def __init__(self):\n"
            "        self._value = None\n"
            "\n"
            "    def get(self):\n"
            "        if self._value is None:\n"
            "            if self._value is None:\n"
            "                self._value = 'built'\n"
            "        return self._value\n"
        )
        self.assertNotIn("AIT-R02", rule_ids(findings))

    def test_missing_causation_or_witness_never_happens(self) -> None:
        findings = [item for item in analyze(DOUBLE_CHECKED) if item.rule_id == "AIT-R02"]
        self.assertTrue(findings[0].causation)
        self.assertIsNotNone(findings[0].witness)


class RaceFixtureTests(unittest.TestCase):
    def test_fixture_reports_both_race_rules(self) -> None:
        self.assertEqual(
            rule_ids(analyze_fixture("bad_race.py")), {"AIT-R01", "AIT-R02"}
        )

    def test_clean_fixture_is_silent(self) -> None:
        self.assertEqual(analyze_fixture("clean_race.py"), [])


if __name__ == "__main__":
    unittest.main()
