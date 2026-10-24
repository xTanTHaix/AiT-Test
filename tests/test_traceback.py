"""Causation trace, guard awareness and parse diagnostics."""

from __future__ import annotations

import unittest

from tests._support import analyze, analyze_fixture

from ait.engine import UsageError
from ait.flow.data_flow import optional_sources, trace_symbol
from ait.flow.scope_tree import build_scope_tree, source_of


def _scopes(source: str):
    import ast

    return build_scope_tree("sample.py", source, ast.parse(source))


class CausationTraceTests(unittest.TestCase):
    def test_parameter_origin_is_the_first_step(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "def send(payload: Optional[str]) -> None:\n"
            "    print(payload.upper())\n"
        )
        self.assertEqual(len(findings), 1)
        trace = findings[0].causation
        self.assertGreaterEqual(len(trace), 2)
        self.assertIn("parameter", trace[0].note)
        self.assertIn("dereferenced", trace[-1].note)
        self.assertEqual([step.index for step in trace], list(range(1, len(trace) + 1)))

    def test_alias_chain_reaches_the_origin(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "def use(source: Optional[str]) -> None:\n"
            "    alias = source\n"
            "    print(alias.upper())\n"
        )
        self.assertEqual(len(findings), 1)
        notes = " ".join(step.note for step in findings[0].causation)
        self.assertIn("alias", notes)
        self.assertIn("parameter", notes)

    def test_producer_call_is_described(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "def lookup(key: str) -> Optional[str]:\n"
            "    return None\n"
            "\n"
            "def use() -> None:\n"
            "    value = lookup('a')\n"
            "    print(value.upper())\n"
        )
        self.assertEqual(len(findings), 1)
        notes = " ".join(step.note for step in findings[0].causation)
        self.assertIn("Optional[str]", notes)

    def test_trace_is_bounded_and_never_loops(self) -> None:
        scopes = _scopes(
            "from typing import Optional\n"
            "\n"
            "def f(a: Optional[str]) -> None:\n"
            "    b = a\n"
            "    c = b\n"
            "    d = c\n"
            "    e = d\n"
            "    print(e.upper())\n"
        )
        frame = scopes.functions()[0]
        steps = trace_symbol(scopes, "sample.py", frame, "e", 7)
        self.assertLessEqual(len(steps), 6)

    def test_optional_sources_requires_positive_evidence(self) -> None:
        scopes = _scopes(
            "def f(value: str) -> None:\n"
            "    alias = value\n"
            "    print(alias.upper())\n"
        )
        frame = scopes.functions()[0]
        optional, steps = optional_sources(scopes, "sample.py", frame, "alias", 3)
        self.assertFalse(optional)
        self.assertEqual(steps, [])

    def test_container_annotation_is_not_treated_as_optional(self) -> None:
        findings = analyze(
            "from typing import Optional, Tuple\n"
            "\n"
            "def pick() -> None:\n"
            "    pair: Tuple[int, Optional[int]] = (1, None)\n"
            "    print(pair[0])\n"
        )
        self.assertEqual([item.rule_id for item in findings], [])

    def test_source_of_survives_synthetic_nodes(self) -> None:
        self.assertEqual(source_of(None), "")


class GuardTests(unittest.TestCase):
    def test_guard_after_dereference_does_not_protect_it(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "def use(value: Optional[str]) -> None:\n"
            "    print(value.upper())\n"
            "    if value is None:\n"
            "        return\n"
        )
        self.assertEqual([item.rule_id for item in findings], ["AIT-N01"])

    def test_positive_branch_guard_protects_body(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "def use(value: Optional[str]) -> None:\n"
            "    if value is not None:\n"
            "        print(value.upper())\n"
        )
        self.assertEqual(findings, [])

    def test_while_guard_protects_loop_body(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "class Node:\n"
            "    next: Optional['Node'] = None\n"
            "\n"
            "def walk(node: Optional[Node]) -> None:\n"
            "    cursor = node\n"
            "    while cursor is not None:\n"
            "        print(cursor.next)\n"
            "        cursor = cursor.next\n"
        )
        self.assertEqual(findings, [])

    def test_isinstance_guard_protects_short_circuit_use(self) -> None:
        findings = analyze(
            "def f(value: object) -> None:\n"
            "    if isinstance(value, str) and value.isupper():\n"
            "        print(value)\n"
        )
        self.assertEqual(findings, [])

    def test_negated_isinstance_exit_guard_protects_remainder(self) -> None:
        findings = analyze(
            "def f(value: object) -> None:\n"
            "    if not isinstance(value, str):\n"
            "        raise TypeError('expected str')\n"
            "    print(value.upper())\n"
        )
        self.assertEqual(findings, [])

    def test_truthiness_guard_inside_ternary_protects_body(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "def f(value: Optional[str]) -> str:\n"
            "    return value.upper() if value else ''\n"
        )
        self.assertEqual(findings, [])

    def test_suppression_directive_is_honoured(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "def f(value: Optional[str]) -> None:\n"
            "    print(value.upper())  # ait: disable=AIT-N01\n"
        )
        self.assertEqual(findings, [])

    def test_suppression_all_covers_every_rule(self) -> None:
        findings = analyze("try:\n    pass\nexcept:  # ait: disable=ALL\n    pass\n")
        self.assertEqual(findings, [])

    def test_bare_except_is_reported_without_directive(self) -> None:
        findings = analyze("try:\n    pass\nexcept:  # ait: disable=AIT-N01\n    pass\n")
        self.assertEqual([item.rule_id for item in findings], ["AIT-H01"])


class ParseDiagnosticTests(unittest.TestCase):
    def test_usage_error_names_the_file_and_line(self) -> None:
        with self.assertRaises(UsageError) as caught:
            analyze("\n\ndef broken(:\n    pass\n", "broken.py")
        message = str(caught.exception)
        self.assertIn("broken.py", message)
        self.assertIn("line 3", message)

    def test_fixture_causation_locations_are_real_lines(self) -> None:
        for name in ("bad_null.py", "bad_async.py"):
            for finding in analyze_fixture(name):
                with self.subTest(fixture=name, rule=finding.rule_id):
                    self.assertGreaterEqual(finding.location.line, 1)
                    for step in finding.causation:
                        self.assertGreaterEqual(step.location.line, 1)
                        self.assertTrue(step.note.strip())


if __name__ == "__main__":
    unittest.main()
