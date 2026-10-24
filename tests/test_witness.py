"""Witness synthesis: coverage, syntax validity and execution safety flags."""

from __future__ import annotations

import ast
import re
import unittest

from tests._support import analyze_fixture, fixture_text

from ait.flow.witness import WitnessRequest, build_witness, supported_rules
from ait.rules import all_rules

EXPECTED_RULES = {
    "AIT-Z01",
    "AIT-Z02",
    "AIT-Z03",
    "AIT-R01",
    "AIT-R02",
    "AIT-A01",
    "AIT-A02",
    "AIT-A03",
    "AIT-M01",
    "AIT-M02",
    "AIT-N01",
    "AIT-H01",
    "AIT-H02",
}


class RegistryTests(unittest.TestCase):
    def test_catalog_matches_the_blueprint(self) -> None:
        self.assertEqual({spec.rule_id for spec in all_rules()}, EXPECTED_RULES)
        self.assertEqual(len(all_rules()), 13)

    def test_every_rule_ships_a_witness(self) -> None:
        self.assertEqual(set(supported_rules()), EXPECTED_RULES)


class WitnessTemplateTests(unittest.TestCase):
    def _build(self, rule_id: str, symbol: str = "value") -> str:
        witness = build_witness(
            WitnessRequest(
                rule_id=rule_id,
                path="demo.py",
                lineno=12,
                symbol=symbol,
                function="demo(value)",
                detail={"call": "time.sleep(0.2)"},
            )
        )
        self.assertIsNotNone(witness, rule_id)
        return witness.code  # type: ignore[union-attr]

    def test_every_template_parses(self) -> None:
        for rule_id in sorted(EXPECTED_RULES):
            with self.subTest(rule=rule_id):
                code = self._build(rule_id)
                ast.parse(code)

    def test_no_placeholder_survives_substitution(self) -> None:
        placeholder = re.compile(r"__[A-Z][A-Z0-9_]*__")
        for rule_id in sorted(EXPECTED_RULES):
            code = self._build(rule_id, symbol="self.total")
            with self.subTest(rule=rule_id):
                self.assertEqual(placeholder.findall(code), [])

    def test_unknown_rule_has_no_witness(self) -> None:
        self.assertIsNone(
            build_witness(
                WitnessRequest(rule_id="AIT-X99", path="a.py", lineno=1)
            )
        )

    def test_hazardous_witnesses_are_flagged(self) -> None:
        for rule_id in ("AIT-Z01", "AIT-Z03"):
            witness = build_witness(
                WitnessRequest(rule_id=rule_id, path="a.py", lineno=3)
            )
            self.assertIsNotNone(witness)
            self.assertFalse(witness.safe_to_execute, rule_id)  # type: ignore[union-attr]

    def test_safe_witnesses_are_flagged_runnable(self) -> None:
        for rule_id in ("AIT-Z02", "AIT-A01", "AIT-H01"):
            witness = build_witness(
                WitnessRequest(rule_id=rule_id, path="a.py", lineno=3)
            )
            self.assertIsNotNone(witness)
            self.assertTrue(witness.safe_to_execute)  # type: ignore[union-attr]
            self.assertTrue(witness.runnable)  # type: ignore[union-attr]

    def test_witness_cites_the_origin_coordinate(self) -> None:
        witness = build_witness(
            WitnessRequest(rule_id="AIT-H01", path="src/demo.py", lineno=42)
        )
        self.assertIsNotNone(witness)
        self.assertIn("src/demo.py:42", witness.code)  # type: ignore[union-attr]


class FixtureWitnessTests(unittest.TestCase):
    def test_every_fixture_finding_carries_a_valid_witness(self) -> None:
        for name in (
            "bad_process.py",
            "bad_race.py",
            "bad_async.py",
            "bad_mutation.py",
            "bad_null.py",
            "bad_hygiene.py",
        ):
            for finding in analyze_fixture(name):
                with self.subTest(fixture=name, rule=finding.rule_id):
                    self.assertIsNotNone(finding.witness, fixture_text(name)[:0])
                    witness = finding.witness
                    assert witness is not None
                    ast.parse(witness.code)
                    self.assertTrue(witness.summary.strip())


if __name__ == "__main__":
    unittest.main()
