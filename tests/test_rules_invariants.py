"""AIT-M01 / M02 / N01 / H01 / H02: mutation, nullability and hygiene."""

from __future__ import annotations

import ast
import unittest

from tests._support import analyze, analyze_fixture, rule_ids

from ait.models import Severity, apply_patch


class ParameterMutationTests(unittest.TestCase):
    SOURCE = "def add(tags: list, tag: str) -> None:\n    tags.append(tag)\n"

    def test_detects_an_in_place_mutation(self) -> None:
        findings = [item for item in analyze(self.SOURCE) if item.rule_id == "AIT-M01"]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.DEFECT)
        self.assertEqual(findings[0].symbol, "tags")
        self.assertEqual(findings[0].location.line, 2)

    def test_returning_a_copy_is_silent(self) -> None:
        findings = analyze(
            "def add(tags: list, tag: str) -> list:\n"
            "    return [*tags, tag]\n"
        )
        self.assertNotIn("AIT-M01", rule_ids(findings))

    def test_rebinding_the_parameter_is_silent(self) -> None:
        findings = analyze(
            "def add(tags: list, tag: str) -> list:\n"
            "    tags = tags + [tag]\n"
            "    return tags\n"
        )
        self.assertNotIn("AIT-M01", rule_ids(findings))

    def test_sorted_returns_a_new_list(self) -> None:
        findings = analyze(
            "def ordered(tags: list) -> list:\n"
            "    return sorted(tags)\n"
        )
        self.assertNotIn("AIT-M01", rule_ids(findings))

    def test_patch_copies_the_parameter_before_mutating(self) -> None:
        findings = [item for item in analyze(self.SOURCE) if item.rule_id == "AIT-M01"]
        patch = findings[0].patch
        self.assertIsNotNone(patch)
        patched = apply_patch(self.SOURCE, patch)  # type: ignore[arg-type]
        ast.parse(patched)
        self.assertIn("tags = list(tags)", patched)
        self.assertLess(patched.index("tags = list(tags)"), patched.index("tags.append"))


class MutableDefaultTests(unittest.TestCase):
    def test_detects_a_list_default(self) -> None:
        findings = [item for item in analyze(
            "def remember(item: str, bucket: list = []) -> int:\n"
            "    bucket = bucket + [item]\n"
            "    return len(bucket)\n"
        ) if item.rule_id == "AIT-M02"]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.DEFECT)
        self.assertEqual(findings[0].location.line, 1)

    def test_detects_a_dict_default(self) -> None:
        findings = analyze(
            "def remember(key: str, store: dict = {}) -> None:\n"
            "    seen = dict(store)\n"
            "    seen[key] = 1\n"
        )
        self.assertIn("AIT-M02", rule_ids(findings))

    def test_none_default_with_a_local_initialiser_is_silent(self) -> None:
        findings = analyze(
            "def remember(item: str, bucket: list | None = None) -> list:\n"
            "    if bucket is None:\n"
            "        bucket = []\n"
            "    bucket.append(item)\n"
            "    return bucket\n"
        )
        self.assertEqual(findings, [])

    def test_immutable_defaults_are_silent(self) -> None:
        findings = analyze(
            "def remember(item: str, label: str = 'x', count: int = 0) -> str:\n"
            "    return item + label * count\n"
        )
        self.assertEqual(findings, [])

    def test_default_factory_helper_is_silent(self) -> None:
        findings = analyze(
            "def remember(item: str, bucket: list | None = None) -> list:\n"
            "    bucket = list(bucket or ())\n"
            "    bucket.append(item)\n"
            "    return bucket\n"
        )
        self.assertNotIn("AIT-M02", rule_ids(findings))


class NullabilityTests(unittest.TestCase):
    def test_optional_parameter_dereference_is_a_defect(self) -> None:
        findings = [item for item in analyze(
            "from typing import Optional\n"
            "\n"
            "def use(value: Optional[str]) -> None:\n"
            "    print(value.upper())\n"
        ) if item.rule_id == "AIT-N01"]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.DEFECT)
        self.assertGreaterEqual(findings[0].confidence, 0.9)

    def test_arithmetic_on_an_optional_value_is_a_defect(self) -> None:
        findings = [
            item
            for item in analyze(
                "from typing import Optional\n"
                "\n"
                "def bump(value: Optional[int]) -> int:\n"
                "    return value + 1\n"
            )
            if item.rule_id == "AIT-N01"
        ]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.DEFECT)

    def test_ordering_comparison_on_an_optional_value_is_a_defect(self) -> None:
        findings = [
            item
            for item in analyze(
                "from typing import Optional\n"
                "\n"
                "def below(value: Optional[int]) -> bool:\n"
                "    return value < 3\n"
            )
            if item.rule_id == "AIT-N01"
        ]
        self.assertEqual(len(findings), 1)

    def test_identity_comparison_is_not_a_dereference(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "def missing(value: Optional[int]) -> bool:\n"
            "    return value is None\n"
        )
        self.assertEqual(findings, [])

    def test_dict_get_result_is_treated_as_optional(self) -> None:
        findings = analyze(
            "def use(mapping: dict) -> None:\n"
            "    value = mapping.get('key')\n"
            "    print(value.upper())\n"
        )
        self.assertIn("AIT-N01", rule_ids(findings))

    def test_optional_attribute_dereference_is_a_defect(self) -> None:
        findings = analyze(
            "from typing import Optional\n"
            "\n"
            "class Service:\n"
            "    def __init__(self):\n"
            "        self.client: Optional[object] = None\n"
            "\n"
            "    def send(self, payload):\n"
            "        self.client.send(payload)\n"
        )
        self.assertIn("AIT-N01", rule_ids(findings))

    def test_non_optional_parameter_is_silent(self) -> None:
        findings = analyze(
            "def use(value: str) -> None:\n"
            "    print(value.upper())\n"
        )
        self.assertEqual(findings, [])

    def test_unknown_symbol_is_not_guessed(self) -> None:
        findings = analyze(
            "def use() -> None:\n"
            "    print(module.value.upper())\n"
        )
        self.assertEqual(findings, [])

    def test_guard_forms_are_respected(self) -> None:
        guarded = (
            "from typing import Optional\n"
            "\n"
            "def a(value: Optional[str]) -> None:\n"
            "    if value is None:\n"
            "        return\n"
            "    print(value.upper())\n"
            "\n"
            "def b(value: Optional[str]) -> None:\n"
            "    if value is not None:\n"
            "        print(value.upper())\n"
            "\n"
            "def c(value: Optional[str]) -> str:\n"
            "    return value.upper() if value else ''\n"
            "\n"
            "def d(value: Optional[str]) -> None:\n"
            "    assert value is not None\n"
            "    print(value.upper())\n"
        )
        self.assertEqual(analyze(guarded), [])

    def test_fix_patch_parses_and_resolves_the_finding(self) -> None:
        source = (
            "from typing import Optional\n"
            "\n"
            "def use(value: Optional[str]) -> None:\n"
            "    print(value.upper())\n"
        )
        findings = [item for item in analyze(source) if item.rule_id == "AIT-N01"]
        patch = findings[0].patch
        self.assertIsNotNone(patch)
        patched = apply_patch(source, patch)  # type: ignore[arg-type]
        ast.parse(patched)
        self.assertEqual(analyze(patched), [])


class HygieneTests(unittest.TestCase):
    def test_bare_except_is_a_warning(self) -> None:
        findings = [item for item in analyze(
            "def parse(value: str) -> int:\n"
            "    try:\n"
            "        return int(value)\n"
            "    except:\n"
            "        return 0\n"
        ) if item.rule_id == "AIT-H01"]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.WARNING)

    def test_narrow_except_is_silent(self) -> None:
        findings = analyze(
            "def parse(value: str) -> int:\n"
            "    try:\n"
            "        return int(value)\n"
            "    except ValueError:\n"
            "        return 0\n"
        )
        self.assertNotIn("AIT-H01", rule_ids(findings))

    def test_bare_except_with_reraise_is_silent(self) -> None:
        findings = analyze(
            "def parse(value: str) -> int:\n"
            "    try:\n"
            "        return int(value)\n"
            "    except:\n"
            "        raise\n"
        )
        self.assertNotIn("AIT-H01", rule_ids(findings))

    def test_bare_except_reraise_as_new_exception_is_not_propagation(self) -> None:
        findings = analyze(
            "def parse(value: str) -> int:\n"
            "    try:\n"
            "        return int(value)\n"
            "    except:\n"
            "        raise RuntimeError('bad input')\n"
        )
        self.assertIn("AIT-H01", rule_ids(findings))

    def test_nesting_depth_is_advisory_only(self) -> None:
        findings = [item for item in analyze(
            "def classify(rows, flag) -> str:\n"
            "    for row in rows:\n"
            "        for cell in row:\n"
            "            if flag:\n"
            "                if cell:\n"
            "                    if isinstance(cell, int):\n"
            "                        if cell % 2 == 0:\n"
            "                            return 'even'\n"
            "    return 'other'\n"
        ) if item.rule_id == "AIT-H02"]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.ADVICE)

    def test_five_nested_bodies_are_counted_from_the_outermost(self) -> None:
        findings = [
            item
            for item in analyze(
                "def f(flag) -> None:\n"
                "    if flag:\n"
                "        if flag:\n"
                "            if flag:\n"
                "                if flag:\n"
                "                    if flag:\n"
                "                        return\n"
            )
            if item.rule_id == "AIT-H02"
        ]
        self.assertEqual(len(findings), 1)
        self.assertIn("5 levels deep", findings[0].message)

    def test_four_nested_bodies_stay_within_the_limit(self) -> None:
        findings = analyze(
            "def f(flag) -> None:\n"
            "    if flag:\n"
            "        if flag:\n"
            "            if flag:\n"
            "                if flag:\n"
            "                    return\n"
        )
        self.assertEqual(findings, [])

    def test_shallow_nesting_is_silent(self) -> None:
        findings = analyze(
            "def classify(rows, flag) -> str:\n"
            "    for row in rows:\n"
            "        if flag:\n"
            "            for cell in row:\n"
            "                if cell:\n"
            "                    return 'found'\n"
            "    return 'other'\n"
        )
        self.assertNotIn("AIT-H02", rule_ids(findings))


class InvariantFixtureTests(unittest.TestCase):
    def test_mutation_fixture(self) -> None:
        self.assertEqual(
            rule_ids(analyze_fixture("bad_mutation.py")), {"AIT-M01", "AIT-M02"}
        )

    def test_null_fixture(self) -> None:
        self.assertEqual(rule_ids(analyze_fixture("bad_null.py")), {"AIT-N01"})

    def test_hygiene_fixture(self) -> None:
        self.assertEqual(
            rule_ids(analyze_fixture("bad_hygiene.py")), {"AIT-H01", "AIT-H02"}
        )

    def test_clean_counterparts_are_silent(self) -> None:
        for name in ("clean_mutation.py", "clean_null.py", "clean_hygiene.py"):
            with self.subTest(fixture=name):
                self.assertEqual(analyze_fixture(name), [])


if __name__ == "__main__":
    unittest.main()
