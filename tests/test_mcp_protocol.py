"""MCP bridge: JSON-RPC framing, tool contracts and stdio robustness."""

from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests._support import REPO_ROOT

from ait.engine import Engine
from ait.mcp import PROTOCOL_VERSION, Server, tool_schemas
from ait.slicer import _docstring

MCP = REPO_ROOT / "mcp.py"

VULNERABLE = (
    "from typing import Optional\n"
    "\n"
    "def use(value: Optional[str]) -> None:\n"
    "    print(value.upper())\n"
)

FIXED = (
    "from typing import Optional\n"
    "\n"
    "def use(value: Optional[str]) -> None:\n"
    "    if value is None:\n"
    "        return\n"
    "    print(value.upper())\n"
)


class ServerHarness(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.engine = Engine(use_cache=False)
        self.server = Server(
            engine=self.engine,
            output_stream=self.output,
            error_stream=self.errors,
        )

    def tearDown(self) -> None:
        self.server.close()
        self._temporary.cleanup()

    def call(self, payload):
        return self.server.handle_request(payload)

    def tool(self, name: str, **arguments):
        response = self.call(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        assert response is not None
        return response

    def write(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_bytes(text.encode("utf-8"))
        return path


class FramingTests(ServerHarness):
    def test_initialize_echoes_the_supported_protocol(self) -> None:
        response = self.call(
            {
                "jsonrpc": "2.0",
                "id": "init-1",
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        assert response is not None
        self.assertEqual(response["jsonrpc"], "2.0")
        self.assertEqual(response["id"], "init-1")
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(response["result"]["serverInfo"]["version"], "1.0.0")

    def test_unknown_protocol_falls_back_to_the_current_version(self) -> None:
        response = self.call(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "1999-01-01"},
            }
        )
        assert response is not None
        self.assertEqual(response["result"]["protocolVersion"], PROTOCOL_VERSION)

    def test_ping_returns_an_empty_result(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        assert response is not None
        self.assertEqual(response, {"jsonrpc": "2.0", "id": 3, "result": {}})

    def test_null_id_is_answered_with_null_id(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": None, "method": "ping"})
        assert response is not None
        self.assertIsNone(response["id"])
        self.assertEqual(response["result"], {})

    def test_missing_id_is_a_notification_and_gets_no_response(self) -> None:
        response = self.call({"jsonrpc": "2.0", "method": "ping"})
        self.assertIsNone(response)

    def test_initialized_notification_is_logged_not_answered(self) -> None:
        response = self.call({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(response)
        self.assertIn("client initialised", self.errors.getvalue())

    def test_stdout_carries_only_protocol_frames(self) -> None:
        self.server._write({"jsonrpc": "2.0", "id": 1, "result": {}})
        frame = self.output.getvalue()
        self.assertTrue(frame.endswith("\n"))
        self.assertEqual(json.loads(frame), {"jsonrpc": "2.0", "id": 1, "result": {}})

    def test_unknown_method_returns_32601(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 4, "method": "does/not/exist"})
        assert response is not None
        self.assertEqual(response["error"]["code"], -32601)

    def test_non_object_payload_returns_32600(self) -> None:
        response = self.call([1, 2, 3])
        assert response is not None
        self.assertEqual(response["error"]["code"], -32600)

    def test_missing_method_returns_32600(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 5})
        assert response is not None
        self.assertEqual(response["error"]["code"], -32600)

    def test_tools_list_advertises_three_tools(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
        assert response is not None
        tools = response["result"]["tools"]
        self.assertEqual(
            [item["name"] for item in tools],
            ["ait_review", "ait_slice_context", "ait_verify_fix"],
        )
        self.assertEqual(tools, tool_schemas())

    def test_every_schema_is_closed_and_documented(self) -> None:
        for schema in tool_schemas():
            with self.subTest(tool=schema["name"]):
                self.assertFalse(schema["inputSchema"]["additionalProperties"])
                self.assertTrue(schema["description"].strip())
                for name in schema["inputSchema"]["required"]:
                    self.assertIn(name, schema["inputSchema"]["properties"])


class ReviewToolTests(ServerHarness):
    def test_reports_findings_without_marking_the_result_as_error(self) -> None:
        # Finding a defect is a successful tool call: ``isError`` is reserved for
        # a tool that could not do its job, otherwise every client treats a useful
        # review as a failure and discards the payload.
        self.write("bad.py", VULNERABLE)
        response = self.tool("ait_review", path=str(self.root))
        result = response["result"]
        self.assertFalse(result["isError"])
        payload = result["structuredContent"]
        self.assertEqual(payload["exit_code"], 1)
        self.assertEqual(payload["tally"]["DEFECT"], 1)
        self.assertEqual(payload["findings"][0]["rule_id"], "AIT-N01")
        # The textual content mirrors the structured payload, compactly.
        self.assertEqual(json.loads(result["content"][0]["text"]), payload)
        self.assertNotIn("\n", result["content"][0]["text"])

    def test_max_findings_caps_the_listing_but_not_the_tally(self) -> None:
        for name in ("a.py", "b.py"):
            self.write(name, VULNERABLE)
        response = self.tool("ait_review", path=str(self.root), max_findings=1)
        payload = response["result"]["structuredContent"]
        self.assertEqual(payload["returned_findings"], 1)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["tally"]["DEFECT"], 2)

    def test_severity_min_keeps_only_findings_at_least_that_severe(self) -> None:
        nested = (
            "def deep(flag):\n"
            "    if flag:\n"
            "        if flag:\n"
            "            if flag:\n"
            "                if flag:\n"
            "                    if flag:\n"
            "                        if flag:\n"
            "                            return 1\n"
            "    return 0\n"
        )
        self.write("bad.py", VULNERABLE)
        self.write("nested.py", nested)
        everything = self.tool("ait_review", path=str(self.root))["result"][
            "structuredContent"
        ]
        self.assertEqual(everything["tally"]["ADVICE"], 1)
        self.assertEqual(everything["tally"]["DEFECT"], 1)
        only_defects = self.tool(
            "ait_review", path=str(self.root), severity_min="DEFECT"
        )["result"]["structuredContent"]
        self.assertEqual(
            [item["severity"] for item in only_defects["findings"]], ["DEFECT"]
        )
        self.assertEqual(only_defects["tally"]["ADVICE"], 1)

    def test_unknown_argument_is_invalid_params(self) -> None:
        self.write("clean.py", "value = 1\n")
        response = self.tool("ait_review", path=str(self.root), paths=str(self.root))
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("unknown argument", response["error"]["message"])

    def test_non_boolean_exitfirst_is_invalid_params(self) -> None:
        self.write("clean.py", "value = 1\n")
        response = self.tool("ait_review", path=str(self.root), exitfirst="yes")
        self.assertEqual(response["error"]["code"], -32602)

    def test_clean_tree_is_not_an_error(self) -> None:
        self.write("clean.py", "value = 1\n")
        response = self.tool("ait_review", path=str(self.root))
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["findings"], [])

    def test_missing_path_is_invalid_params(self) -> None:
        response = self.tool("ait_review", path=str(self.root / "nope"))
        self.assertEqual(response["error"]["code"], -32602)

    def test_empty_path_is_invalid_params(self) -> None:
        response = self.tool("ait_review", path="   ")
        self.assertEqual(response["error"]["code"], -32602)

    def test_unknown_tool_is_invalid_params(self) -> None:
        response = self.tool("ait_no_such_tool", path=str(self.root))
        self.assertEqual(response["error"]["code"], -32602)

    def test_exitfirst_is_honoured(self) -> None:
        for name in ("a.py", "b.py"):
            self.write(name, VULNERABLE)
        response = self.tool("ait_review", path=str(self.root), exitfirst=True)
        self.assertEqual(response["result"]["structuredContent"]["files_processed"], 1)


class SliceToolTests(ServerHarness):
    SOURCE = (
        "def inner(value: int) -> int:\n"
        "    return value + 1\n"
        "\n"
        "def outer(items: list) -> int:\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        total += inner(item)\n"
        "    return total\n"
    )

    def test_returns_source_calls_and_callers(self) -> None:
        path = self.write("module.py", self.SOURCE)
        response = self.tool(
            "ait_slice_context", file_path=str(path), symbol_name="outer"
        )
        payload = response["result"]["structuredContent"]
        self.assertEqual(payload["qualname"], "outer")
        self.assertIn("total += inner(item)", payload["code"])
        self.assertEqual([call["callee"] for call in payload["calls"]], ["inner"])
        self.assertEqual(payload["kind"], "function")

    def test_depth_zero_does_not_expand_callees(self) -> None:
        path = self.write("module.py", self.SOURCE)
        response = self.tool(
            "ait_slice_context", file_path=str(path), symbol_name="outer", depth=0
        )
        self.assertEqual(response["result"]["structuredContent"]["expanded"], [])

    def test_depth_one_expands_the_callee_body(self) -> None:
        path = self.write("module.py", self.SOURCE)
        response = self.tool(
            "ait_slice_context", file_path=str(path), symbol_name="outer", depth=1
        )
        expanded = response["result"]["structuredContent"]["expanded"]
        self.assertEqual([item["qualname"] for item in expanded], ["inner"])
        self.assertIn("return value + 1", expanded[0]["code"])

    def test_depth_outside_the_documented_range_is_rejected(self) -> None:
        # Clamping silently changes what the caller asked for; the schema says
        # 0..3, so an out-of-range value is a caller bug we must report.
        path = self.write("module.py", self.SOURCE)
        response = self.tool(
            "ait_slice_context", file_path=str(path), symbol_name="outer", depth=99
        )
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("between 0 and 3", response["error"]["message"])

    def test_non_integer_depth_is_invalid_params(self) -> None:
        path = self.write("module.py", self.SOURCE)
        response = self.tool(
            "ait_slice_context", file_path=str(path), symbol_name="outer", depth="deep"
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_missing_symbol_is_invalid_params(self) -> None:
        path = self.write("module.py", self.SOURCE)
        response = self.tool(
            "ait_slice_context", file_path=str(path), symbol_name="ghost"
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_ambiguous_symbol_is_invalid_params(self) -> None:
        path = self.write(
            "two.py",
            "class A:\n    def run(self):\n        return 1\n"
            "\n"
            "class B:\n    def run(self):\n        return 2\n",
        )
        response = self.tool(
            "ait_slice_context", file_path=str(path), symbol_name="run"
        )
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("ambiguous", response["error"]["message"])

    def test_missing_file_is_invalid_params(self) -> None:
        response = self.tool(
            "ait_slice_context",
            file_path=str(self.root / "absent.py"),
            symbol_name="x",
        )
        self.assertEqual(response["error"]["code"], -32602)


class VerifyToolTests(ServerHarness):
    def test_clean_candidate_is_accepted(self) -> None:
        path = self.write("bad.py", VULNERABLE)
        response = self.tool(
            "ait_verify_fix", file_path=str(path), candidate_code=FIXED
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        payload = result["structuredContent"]
        self.assertTrue(payload["clean"])
        self.assertFalse(payload["regression"])
        self.assertEqual([item["rule_id"] for item in payload["resolved"]], ["AIT-N01"])

    def test_regressing_candidate_is_flagged(self) -> None:
        path = self.write("bad.py", VULNERABLE)
        candidate = (
            "from typing import Optional\n"
            "\n"
            "def use(value: Optional[str], bucket: list = []) -> None:\n"
            "    if value is None:\n"
            "        return\n"
            "    bucket.append(value)\n"
        )
        response = self.tool(
            "ait_verify_fix", file_path=str(path), candidate_code=candidate
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertTrue(result["structuredContent"]["regression"])
        self.assertTrue(result["structuredContent"]["introduced"])

    def test_unparsable_candidate_is_a_tool_level_error(self) -> None:
        path = self.write("bad.py", VULNERABLE)
        response = self.tool(
            "ait_verify_fix", file_path=str(path), candidate_code="def broken(:\n"
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("does not parse", result["content"][0]["text"])

    def test_missing_target_file_is_invalid_params(self) -> None:
        response = self.tool(
            "ait_verify_fix",
            file_path=str(self.root / "absent.py"),
            candidate_code=FIXED,
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_empty_candidate_is_invalid_params(self) -> None:
        path = self.write("bad.py", VULNERABLE)
        response = self.tool("ait_verify_fix", file_path=str(path), candidate_code="")
        self.assertEqual(response["error"]["code"], -32602)


class CrashGuardTests(ServerHarness):
    def test_unexpected_engine_failure_is_answered_and_the_bridge_stays_up(self) -> None:
        self.write("bad.py", VULNERABLE)
        self.engine.scan = lambda *args, **kwargs: (_ for _ in ()).throw(
            RecursionError("source nested too deeply")
        )
        response = self.tool("ait_review", path=str(self.root))
        self.assertEqual(response["error"]["code"], -32603)
        self.assertIn("RecursionError", response["error"]["message"])
        ping = self.call({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert ping is not None
        self.assertEqual(ping["result"], {})

    def test_slice_of_a_module_containing_lambdas_does_not_crash(self) -> None:
        path = self.write(
            "lambdas.py",
            "def outer(items):\n"
            "    step = lambda value: value + 1\n"
            "    return [step(item) for item in items]\n",
        )
        response = self.tool(
            "ait_slice_context", file_path=str(path), symbol_name="outer", depth=3
        )
        self.assertIn("result", response)
        self.assertEqual(response["result"]["structuredContent"]["qualname"], "outer")


class DocstringTests(unittest.TestCase):
    def test_lambda_has_no_docstring(self) -> None:
        # ``ast.get_docstring`` raises TypeError on a Lambda, which used to kill
        # the whole slice request when a callee frame was a lambda.
        function = ast.parse("f = lambda x: x\n").body[0]
        assert isinstance(function, ast.Assign)
        self.assertEqual(_docstring(function.value), "")

    def test_module_and_function_docstrings_are_returned(self) -> None:
        tree = ast.parse('"""module"""\n\ndef f():\n    """doc"""\n')
        self.assertEqual(_docstring(tree), "module")
        self.assertEqual(_docstring(tree.body[1]), "doc")


class LifecycleTests(ServerHarness):
    def test_shutdown_stops_the_serve_loop(self) -> None:
        response = self.call({"jsonrpc": "2.0", "id": 9, "method": "shutdown"})
        assert response is not None
        self.assertEqual(response["result"], {})
        self.assertTrue(self.server._stopping)

    def test_notification_exit_stops_the_serve_loop(self) -> None:
        self.assertIsNone(self.call({"jsonrpc": "2.0", "method": "notifications/exit"}))
        self.assertTrue(self.server._stopping)

    def test_notification_method_with_an_id_is_a_malformed_request(self) -> None:
        response = self.call(
            {"jsonrpc": "2.0", "id": 10, "method": "notifications/initialized"}
        )
        assert response is not None
        self.assertEqual(response["error"]["code"], -32600)

    def test_capabilities_do_not_advertise_logging(self) -> None:
        response = self.call(
            {"jsonrpc": "2.0", "id": 11, "method": "initialize", "params": {}}
        )
        assert response is not None
        capabilities = response["result"]["capabilities"]
        self.assertIn("tools", capabilities)
        self.assertNotIn("logging", capabilities)


class StdioBridgeTests(unittest.TestCase):
    def _run(self, lines) -> subprocess.CompletedProcess:
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        payload = "".join(line + "\n" for line in lines)
        return subprocess.run(
            [sys.executable, str(MCP)],
            cwd=str(REPO_ROOT),
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=180,
        )

    def test_batch_of_requests_returns_one_frame_each(self) -> None:
        requests = [
            json.dumps({"jsonrpc": "2.0", "id": index, "method": "ping"})
            for index in range(200)
        ]
        result = self._run(requests)
        self.assertEqual(result.returncode, 0, result.stderr)
        frames = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(frames), 200)
        self.assertEqual([frame["id"] for frame in frames], list(range(200)))

    def test_malformed_json_returns_a_parse_error_frame(self) -> None:
        result = self._run(
            [
                "{not json",
                json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}),
            ]
        )
        frames = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0]["error"]["code"], -32700)
        self.assertIsNone(frames[0]["id"])
        self.assertEqual(frames[1]["id"], 7)

    def test_notifications_never_emit_a_frame(self) -> None:
        result = self._run(
            [
                json.dumps(
                    {"jsonrpc": "2.0", "method": "notifications/initialized"}
                )
                for _ in range(3)
            ]
        )
        self.assertEqual(result.stdout.strip(), "")
        self.assertEqual(result.returncode, 0)

    def test_large_stderr_volume_does_not_deadlock_the_bridge(self) -> None:
        # Every notification writes to stderr; enough of them exceed the OS pipe
        # buffer, so this deadlocks unless both streams are drained concurrently.
        lines = []
        for index in range(1500):
            lines.append(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            )
            lines.append(json.dumps({"jsonrpc": "2.0", "id": index, "method": "ping"}))
        result = self._run(lines)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        frames = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(frames), 1500)
        self.assertIn("client initialised", result.stderr)

    def test_blank_lines_are_ignored(self) -> None:
        result = self._run(
            ["", "   ", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), ""]
        )
        frames = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(frames), 1)

    def test_help_exits_zero_without_entering_the_serve_loop(self) -> None:
        result = subprocess.run(
            [sys.executable, str(MCP), "--help"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: ait-mcp", result.stdout)

    def test_version_exits_zero_and_prints_the_engine_version(self) -> None:
        result = subprocess.run(
            [sys.executable, str(MCP), "--version"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1.0.0", result.stdout)

    def test_unknown_option_exits_two(self) -> None:
        result = subprocess.run(
            [sys.executable, str(MCP), "--definitely-not-a-flag"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
