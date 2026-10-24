"""Contract tests for the immutable data model."""

from __future__ import annotations

import json
import unittest

from tests._support import REPO_ROOT  # noqa: F401  (anchors sys.path)

from ait.models import (
    CausationStep,
    Diagnostic,
    Evidence,
    Location,
    Patch,
    Severity,
    Witness,
    apply_patch,
    diagnostic_from_dict,
    diagnostic_to_dict,
    sort_diagnostics,
)


def _diagnostic(**overrides) -> Diagnostic:
    payload = dict(
        rule_id="AIT-N01",
        severity=Severity.DEFECT,
        confidence=0.9,
        message="value can be None",
        location=Location(path="a.py", line=7, col=4, end_line=7, end_col=12),
        function="use(value)",
        symbol="value",
        evidence=(
            Evidence(location=Location(path="a.py", line=7, col=4), note="deref"),
        ),
    )
    payload.update(overrides)
    return Diagnostic(**payload)


class LocationTests(unittest.TestCase):
    def test_rejects_empty_path(self) -> None:
        with self.assertRaises(ValueError):
            Location(path="", line=1)

    def test_rejects_zero_line(self) -> None:
        with self.assertRaises(ValueError):
            Location(path="a.py", line=0)

    def test_rejects_inverted_span(self) -> None:
        with self.assertRaises(ValueError):
            Location(path="a.py", line=5, end_line=4)

    def test_format_is_editor_clickable(self) -> None:
        self.assertEqual(
            Location(path="src/a.py", line=12, col=3).format(), "src/a.py:12:3"
        )


class DiagnosticTests(unittest.TestCase):
    def test_requires_evidence(self) -> None:
        with self.assertRaises(ValueError):
            _diagnostic(evidence=())

    def test_rejects_malformed_rule_id(self) -> None:
        with self.assertRaises(ValueError):
            _diagnostic(rule_id="N01")

    def test_rejects_out_of_range_confidence(self) -> None:
        with self.assertRaises(ValueError):
            _diagnostic(confidence=1.5)

    def test_fingerprint_is_stable_and_positional(self) -> None:
        first = _diagnostic()
        second = _diagnostic()
        self.assertEqual(first.fingerprint, second.fingerprint)
        moved = _diagnostic(location=Location(path="a.py", line=8, col=4))
        self.assertNotEqual(first.fingerprint, moved.fingerprint)

    def test_json_round_trip_preserves_every_payload(self) -> None:
        original = _diagnostic(
            causation=(
                CausationStep(index=1, location=Location(path="a.py", line=3), note="origin"),
            ),
            witness=Witness(summary="demo", code="x = None\n", safe_to_execute=False),
            patch=Patch(
                start_line=7,
                end_line=7,
                original="    value.upper()\n",
                replacement="    if value is None:\n        raise RuntimeError()\n",
                rationale="guard",
            ),
            snippet=((6, "value = None"), (7, "value.upper()")),
        )
        restored = diagnostic_from_dict(
            json.loads(json.dumps(diagnostic_to_dict(original)))
        )
        self.assertEqual(restored.to_dict(), original.to_dict())
        self.assertIsNotNone(restored.patch)
        self.assertFalse(restored.witness.safe_to_execute)
        self.assertEqual(restored.snippet, original.snippet)


class PatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = "def f(value):\n    value.upper()\n    return value\n"
        self.patch = Patch(
            start_line=2,
            end_line=2,
            original="    value.upper()\n",
            replacement="    print(value.upper())\n",
            rationale="wrap",
        )

    def test_rejects_identical_replacement(self) -> None:
        with self.assertRaises(ValueError):
            Patch(
                start_line=1,
                end_line=1,
                original="a = 1\n",
                replacement="a = 1\n",
                rationale="noop",
            )

    def test_apply_replaces_exactly_one_span(self) -> None:
        patched = apply_patch(self.source, self.patch)
        self.assertIn("print(value.upper())", patched)
        self.assertNotIn("\n    value.upper()\n", patched)

    def test_apply_refuses_drifted_source(self) -> None:
        drifted = self.source.replace("value.upper()", "value.lower()")
        with self.assertRaises(ValueError):
            apply_patch(drifted, self.patch)

    def test_apply_refuses_out_of_range_span(self) -> None:
        with self.assertRaises(ValueError):
            apply_patch("x = 1\n", Patch(1, 5, "x = 1\n", "x = 2\n", "range"))

    def test_unified_header_uses_real_file_coordinates(self) -> None:
        diff = self.patch.unified("src/example.py")
        self.assertIn("@@ -2,1 +2,1 @@", diff)
        self.assertIn("--- a/src/example.py", diff)
        self.assertIn("+++ b/src/example.py", diff)
        self.assertEqual(diff.count("@@"), 2)


class OrderingTests(unittest.TestCase):
    def test_sort_is_deterministic_across_rule_and_path(self) -> None:
        items = [
            _diagnostic(rule_id="AIT-H02", location=Location(path="b.py", line=1)),
            _diagnostic(rule_id="AIT-Z01", location=Location(path="a.py", line=9)),
            _diagnostic(rule_id="AIT-A01", location=Location(path="a.py", line=2)),
        ]
        ordered = sort_diagnostics(items)
        self.assertEqual(
            [(item.location.path, item.location.line) for item in ordered],
            [("a.py", 2), ("a.py", 9), ("b.py", 1)],
        )
        self.assertEqual(ordered, sort_diagnostics(list(reversed(ordered))))


if __name__ == "__main__":
    unittest.main()
