"""AIT-A01 / A02 / A03: async traps."""

from __future__ import annotations

import unittest

from tests._support import analyze, analyze_fixture, rule_ids

from ait.models import Severity


class BlockingCallTests(unittest.TestCase):
    def test_detects_time_sleep_inside_a_coroutine(self) -> None:
        findings = [
            item
            for item in analyze(
                "import time\n"
                "\n"
                "async def handler():\n"
                "    time.sleep(0.1)\n"
                "    return 1\n"
            )
            if item.rule_id == "AIT-A01"
        ]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.DEFECT)
        self.assertEqual(findings[0].symbol, "time.sleep")
        self.assertEqual(findings[0].location.line, 4)

    def test_detects_blocking_network_call(self) -> None:
        findings = analyze(
            "import requests\n"
            "\n"
            "async def fetch():\n"
            "    return requests.get('https://example.com')\n"
        )
        self.assertIn("AIT-A01", rule_ids(findings))

    def test_file_open_is_downgraded_to_warning(self) -> None:
        findings = [
            item
            for item in analyze(
                "async def read():\n"
                "    with open('data.txt') as handle:\n"
                "        return handle.read()\n"
            )
            if item.rule_id == "AIT-A01"
        ]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.WARNING)

    def test_thread_offload_is_silent(self) -> None:
        findings = analyze(
            "import asyncio\n"
            "import time\n"
            "\n"
            "async def handler():\n"
            "    await asyncio.to_thread(time.sleep, 0.1)\n"
            "    return 1\n"
        )
        self.assertNotIn("AIT-A01", rule_ids(findings))

    def test_executor_offload_is_silent(self) -> None:
        findings = analyze(
            "import asyncio\n"
            "import time\n"
            "\n"
            "async def handler(loop):\n"
            "    await loop.run_in_executor(None, time.sleep, 0.1)\n"
        )
        self.assertNotIn("AIT-A01", rule_ids(findings))

    def test_sync_function_calling_sleep_is_not_a_coroutine(self) -> None:
        findings = analyze(
            "import time\n"
            "\n"
            "def handler():\n"
            "    time.sleep(0.1)\n"
        )
        self.assertNotIn("AIT-A01", rule_ids(findings))


class OrphanTaskTests(unittest.TestCase):
    SOURCE = (
        "import asyncio\n"
        "\n"
        "async def worker():\n"
        "    await asyncio.sleep(0.01)\n"
        "\n"
        "async def demo():\n"
        "    asyncio.create_task(worker())\n"
        "    await asyncio.sleep(0.05)\n"
    )

    def test_detects_a_discarded_task_handle(self) -> None:
        findings = [item for item in analyze(self.SOURCE) if item.rule_id == "AIT-A02"]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.WARNING)
        self.assertEqual(findings[0].location.line, 7)

    def test_keeping_the_handle_is_silent(self) -> None:
        findings = analyze(
            "import asyncio\n"
            "\n"
            "async def worker():\n"
            "    await asyncio.sleep(0.01)\n"
            "\n"
            "async def demo():\n"
            "    task = asyncio.create_task(worker())\n"
            "    await task\n"
        )
        self.assertNotIn("AIT-A02", rule_ids(findings))

    def test_adding_a_done_callback_is_silent(self) -> None:
        findings = analyze(
            "import asyncio\n"
            "\n"
            "async def worker():\n"
            "    await asyncio.sleep(0.01)\n"
            "\n"
            "async def demo():\n"
            "    asyncio.create_task(worker()).add_done_callback(lambda task: None)\n"
        )
        self.assertNotIn("AIT-A02", rule_ids(findings))

    def test_awaiting_the_call_directly_is_silent(self) -> None:
        findings = analyze(
            "import asyncio\n"
            "\n"
            "async def worker():\n"
            "    return 1\n"
            "\n"
            "async def demo():\n"
            "    await asyncio.create_task(worker())\n"
        )
        self.assertNotIn("AIT-A02", rule_ids(findings))


class MissingAwaitTests(unittest.TestCase):
    SOURCE = (
        "import asyncio\n"
        "\n"
        "async def fetch():\n"
        "    await asyncio.sleep(0.01)\n"
        "    return 1\n"
        "\n"
        "def demo():\n"
        "    fetch()\n"
    )

    def test_detects_an_unawaited_coroutine(self) -> None:
        findings = [item for item in analyze(self.SOURCE) if item.rule_id == "AIT-A03"]
        self.assertEqual(len(findings), 1)
        self.assertIs(findings[0].severity, Severity.DEFECT)
        self.assertEqual(findings[0].location.line, 8)

    def test_await_makes_it_silent(self) -> None:
        findings = analyze(
            "import asyncio\n"
            "\n"
            "async def fetch():\n"
            "    return 1\n"
            "\n"
            "async def demo():\n"
            "    await fetch()\n"
        )
        self.assertNotIn("AIT-A03", rule_ids(findings))

    def test_asyncio_run_is_an_allowed_consumer(self) -> None:
        findings = analyze(
            "import asyncio\n"
            "\n"
            "async def fetch():\n"
            "    return 1\n"
            "\n"
            "asyncio.run(fetch())\n"
        )
        self.assertNotIn("AIT-A03", rule_ids(findings))

    def test_returning_the_coroutine_still_reports_the_site(self) -> None:
        findings = [item for item in analyze(
            "import asyncio\n"
            "\n"
            "async def fetch():\n"
            "    return 1\n"
            "\n"
            "def demo():\n"
            "    pending = fetch()\n"
            "    return pending\n"
        ) if item.rule_id == "AIT-A03"]
        self.assertEqual(len(findings), 1)

    def test_calling_a_sync_helper_is_not_reported(self) -> None:
        findings = analyze(
            "def helper():\n"
            "    return 1\n"
            "\n"
            "def demo():\n"
            "    helper()\n"
        )
        self.assertNotIn("AIT-A03", rule_ids(findings))


class AsyncFixtureTests(unittest.TestCase):
    def test_fixture_reports_all_three_async_rules(self) -> None:
        self.assertEqual(
            rule_ids(analyze_fixture("bad_async.py")),
            {"AIT-A01", "AIT-A02", "AIT-A03"},
        )

    def test_clean_fixture_is_silent(self) -> None:
        self.assertEqual(analyze_fixture("clean_async.py"), [])


if __name__ == "__main__":
    unittest.main()
