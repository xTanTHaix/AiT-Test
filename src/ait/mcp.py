"""AI agent bridge: JSON-RPC 2.0 over stdio (MCP transport).

The bridge is a thin protocol shell around the same :class:`Engine` the CLI uses.
It never prints anything to stdout except JSON-RPC frames, keeps one engine and
one cache alive for the whole session, and answers requests whose ``id`` is
``null`` because JSON-RPC treats those as requests, not notifications.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Mapping, Optional, TextIO, Tuple

from .engine import Engine, EngineError, UsageError, decode_source
from .models import (
    ENGINE_NAME,
    ENGINE_VERSION,
    EXIT_CLEAN,
    EXIT_INTERNAL,
    EXIT_INTERRUPT,
    SEVERITY_ORDER,
)
from .reporter import configure_stream
from .slicer import build_slice

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


class ToolFailure(Exception):
    """Raised for tool-level failures that belong inside a JSON-RPC result."""


class _UnknownMethod(Exception):
    """Raised by the dispatcher when a method name is not implemented."""


def tool_schemas() -> List[Dict[str, Any]]:
    """Return the advertised MCP tool catalog.

    Descriptions follow the three-pillar contract an LLM caller needs: what the
    tool does, when to call it, and what it is explicitly not for.
    """
    return [
        {
            "name": "ait_review",
            "description": (
                "Run the 13-rule AST catalog over a Python file or directory and return "
                "machine-readable findings: rule_id, severity (DEFECT/WARNING/ADVICE), "
                "location, symbol, confidence, causation trace, and where available a "
                "repro witness and a unified diff. "
                "USE WHEN: the user asks to review, audit, lint, or hunt bugs, race "
                "conditions, null dereferences, or swallowed errors in .py code, or "
                "before proposing a patch. "
                "DO NOT USE: to inspect one known symbol (use ait_slice_context) or to "
                "test a patch you already wrote (use ait_verify_fix). "
                "Note: use_cache=true (the default) writes <target>/.ait_cache; findings "
                "are bounded by max_findings and severity_min."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "description": "File or directory to analyse, e.g. src/ait/mcp.py or src/ait.",
                    },
                    "severity_min": {
                        "type": "string",
                        "enum": ["ADVICE", "WARNING", "DEFECT"],
                        "default": "ADVICE",
                        "description": "Drop findings below this severity, e.g. DEFECT to return only defects.",
                    },
                    "max_findings": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 50,
                        "description": "Cap the returned findings (sorted by path,line); the tally still counts every finding.",
                    },
                    "exitfirst": {
                        "type": "boolean",
                        "default": False,
                        "description": "Stop at the first file carrying a DEFECT.",
                    },
                    "use_cache": {
                        "type": "boolean",
                        "default": True,
                        "description": "Reuse the BLAKE2b cache; set false for one-shot scans that must not write into the tree.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "ait_slice_context",
            "description": (
                "Return a token-efficient structural slice of exactly one symbol: its "
                "source text, signature, docstring, variable table, outgoing calls, "
                "callers and optionally the bodies of its callees. "
                "USE WHEN: you know the symbol name and need its implementation without "
                "reading the whole file. "
                "DO NOT USE: for whole-file or repository review (use ait_review), or "
                "when the symbol name is unknown — locate it first."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "pattern": "\\.py$",
                        "description": "Existing Python file that contains the symbol, e.g. src/ait/slicer.py.",
                    },
                    "symbol_name": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Function or class name; qualify it as Class.method when ambiguous, e.g. Engine.scan.",
                    },
                    "depth": {
                        "type": "integer",
                        "default": 1,
                        "minimum": 0,
                        "maximum": 3,
                        "description": "Call-graph hops to expand (0 = signature only, 3 = maximum). Out-of-range values are rejected, not clamped.",
                    },
                },
                "required": ["file_path", "symbol_name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "ait_verify_fix",
            "description": (
                "Re-run the 13-rule catalog over a candidate source string and diff the "
                "result against the file as it currently exists on disk: returns "
                "resolved, introduced, remaining_defects, regression and clean. "
                "USE WHEN: you have a proposed patch for a file that is still unmodified "
                "on disk, to prove the target finding is gone and nothing new appeared. "
                "DO NOT USE: after writing the patch — the baseline would then equal the "
                "candidate and the diff is meaningless — or as a semantic proof; this is "
                "AST re-verification of 13 rules, not correctness."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "pattern": "\\.py$",
                        "description": "Unmodified baseline file on disk, e.g. src/ait/engine.py.",
                    },
                    "candidate_code": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200000,
                        "description": "Full replacement source text (not a diff), e.g. 'def f(x):\\n    return x\\n'.",
                    },
                },
                "required": ["file_path", "candidate_code"],
                "additionalProperties": False,
            },
        },
    ]


class Server:
    """Session-scoped JSON-RPC dispatcher."""

    def __init__(
        self,
        engine: Optional[Engine] = None,
        input_stream: Optional[Any] = None,
        output_stream: Optional[TextIO] = None,
        error_stream: Optional[TextIO] = None,
    ) -> None:
        self.engine = engine or Engine()
        self._input = input_stream
        self._output = output_stream or sys.stdout
        self._error = error_stream or sys.stderr
        self._stopping = False
        configure_stream(self._output)
        configure_stream(self._error)

    # ---------------------------------------------------------------- transport
    def _log(self, message: str) -> None:
        """Write diagnostics to stderr; stdout is reserved for protocol frames."""
        self._error.write(f"[ait-mcp] {message}\n")
        self._error.flush()

    def _write(self, payload: Mapping[str, Any]) -> None:
        """Emit exactly one newline-delimited JSON-RPC frame."""
        self._output.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._output.flush()

    def _read_line(self) -> Optional[str]:
        """Read one line from the text or binary stdin stream."""
        stream = self._input if self._input is not None else getattr(sys.stdin, "buffer", sys.stdin)
        line = stream.readline()
        if line is None or line == "" or line == b"":
            return None
        if isinstance(line, bytes):
            return line.decode("utf-8-sig", errors="replace")
        return str(line)

    def serve(self) -> int:
        """Process frames until stdin closes."""
        while True:
            line = self._read_line()
            if line is None:
                return EXIT_CLEAN
            text = line.strip()
            if not text:
                continue
            try:
                request = json.loads(text)
            except json.JSONDecodeError as exc:
                self._write(_error_frame(-32700, f"Parse error: {exc.msg}", None))
                continue
            response = self.handle_request(request)
            if response is not None:
                self._write(response)
            if self._stopping:
                return EXIT_CLEAN

    def close(self) -> None:
        """Release engine resources."""
        self.engine.close()

    # ---------------------------------------------------------------- dispatch
    def handle_request(self, request: Any) -> Optional[Dict[str, Any]]:
        """Dispatch one decoded JSON-RPC message, returning a response or ``None``.

        A message *without* an ``id`` member is a notification: it is executed
        but never answered. An explicit ``"id": null`` is a request with a null
        id, and JSON-RPC requires it to be answered with ``"id": null``.
        """
        if not isinstance(request, dict):
            return _error_frame(-32600, "Invalid Request: payload must be an object", None)
        version = request.get("jsonrpc")
        if version is not None and version != "2.0":
            return _error_frame(-32600, "Invalid Request: jsonrpc must be '2.0'", None)
        method = request.get("method")
        request_id = request.get("id", None)
        has_id = "id" in request
        if not isinstance(method, str) or not method:
            if not has_id:
                return None
            return _error_frame(-32600, "Invalid Request: missing method", request_id)
        if method.startswith("notifications/"):
            if method == "notifications/exit":
                self._stopping = True
            elif method == "notifications/initialized":
                self._log("client initialised")
            if has_id:
                # A notification method carrying an id is a malformed request;
                # answering it is the only way the caller stops waiting.
                return _error_frame(
                    -32600,
                    f"Invalid Request: {method} is a notification and must not carry an id",
                    request_id,
                )
            return None
        try:
            result = self._invoke(method, request.get("params"))
        except Exception as exc:  # noqa: BLE001 - mapped to a JSON-RPC frame below
            response = self._failure_frame(exc, request_id)
        else:
            response = _result_frame(request_id, result)
        return response if has_id else None

    def _invoke(self, method: str, params: Any) -> Dict[str, Any]:
        """Run one method and return its result object."""
        request_params = params if isinstance(params, Mapping) else {}
        if method == "initialize":
            return self._initialize(request_params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": tool_schemas()}
        if method == "tools/call":
            return self._call_tool(request_params)
        if method == "shutdown":
            self._stopping = True
            return {}
        raise _UnknownMethod(method)

    def _failure_frame(self, exc: Exception, request_id: Any) -> Dict[str, Any]:
        """Translate an exception into the correct JSON-RPC frame."""
        if isinstance(exc, _UnknownMethod):
            return _error_frame(-32601, f"Method not found: {exc}", request_id)
        if isinstance(exc, ToolFailure):
            return _result_frame(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        if isinstance(exc, UsageError):
            return _error_frame(-32602, f"Invalid params: {exc}", request_id)
        if isinstance(exc, EngineError):
            return _error_frame(-32603, f"Internal error: {exc}", request_id)
        if isinstance(exc, (OSError, ValueError)):
            return _error_frame(
                -32603, f"Internal error: {type(exc).__name__}: {exc}", request_id
            )
        # Anything else (RecursionError, MemoryError, a rule bug) must still be
        # answered: an uncaught exception would kill the process mid-stream and
        # leave every later request unanswered.
        return _error_frame(
            -32603,
            f"Internal error: {type(exc).__name__}: {exc}",
            request_id,
        )

    def _initialize(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        """Negotiate the protocol version and advertise capabilities."""
        requested = params.get("protocolVersion") if isinstance(params, Mapping) else None
        negotiated = (
            str(requested) if requested in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION
        )
        return {
            "protocolVersion": negotiated,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {"name": ENGINE_NAME, "version": ENGINE_VERSION},
            "instructions": (
                "Static diagnostics for Python. Call ait_review to scan, "
                "ait_slice_context to read one symbol cheaply, and ait_verify_fix "
                "to confirm a patch removed the finding without adding a new one."
            ),
        }

    # ---------------------------------------------------------------- tools
    def _call_tool(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        """Route a ``tools/call`` request to the matching implementation."""
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            raise UsageError("tools/call requires a tool name")
        if not isinstance(arguments, dict):
            raise UsageError("tools/call arguments must be an object")
        if name == "ait_review":
            return self._tool_review(arguments)
        if name == "ait_slice_context":
            return self._tool_slice(arguments)
        if name == "ait_verify_fix":
            return self._tool_verify(arguments)
        raise UsageError(f"Unknown tool: {name}")

    def _tool_review(self, arguments: Mapping[str, Any]) -> Dict[str, Any]:
        """Implement ``ait_review`` with strict argument validation."""
        _reject_unknown(
            arguments,
            ("path", "severity_min", "max_findings", "exitfirst", "use_cache"),
        )
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            raise UsageError("ait_review requires a non-empty 'path' string")
        exitfirst = _as_bool(arguments, "exitfirst", False)
        use_cache = _as_bool(arguments, "use_cache", True)
        severity_min = arguments.get("severity_min", "ADVICE")
        if not isinstance(severity_min, str) or severity_min not in SEVERITY_ORDER:
            raise UsageError(
                "'severity_min' must be one of ADVICE, WARNING or DEFECT"
            )
        max_findings = _as_int(arguments, "max_findings", 50, minimum=1, maximum=500)
        result = self.engine.scan(path, exitfirst=exitfirst, use_cache=use_cache)
        payload = result.to_dict()
        findings = payload["findings"]
        # Severity rank 0 is the most severe, so "at least this severe" means
        # rank <= floor. Comparing the other way round drops every DEFECT and
        # returns only ADVICE, which is the exact opposite of the request.
        floor = SEVERITY_ORDER[severity_min]
        visible = [
            item for item in findings if SEVERITY_ORDER.get(item["severity"], -1) <= floor
        ]
        truncated = len(visible) > max_findings
        payload["findings"] = visible[:max_findings]
        payload["returned_findings"] = len(payload["findings"])
        payload["truncated"] = truncated
        return _tool_payload(payload, is_error=False)

    def _tool_slice(self, arguments: Mapping[str, Any]) -> Dict[str, Any]:
        """Implement ``ait_slice_context`` with strict argument validation."""
        _reject_unknown(arguments, ("file_path", "symbol_name", "depth"))
        file_path = arguments.get("file_path")
        symbol_name = arguments.get("symbol_name")
        if not isinstance(file_path, str) or not file_path.strip():
            raise UsageError("ait_slice_context requires a non-empty 'file_path'")
        if not isinstance(symbol_name, str) or not symbol_name.strip():
            raise UsageError("ait_slice_context requires a non-empty 'symbol_name'")
        depth = _as_int(arguments, "depth", 1, minimum=0, maximum=3)
        payload = build_slice(file_path, symbol_name, depth=depth)
        return _tool_payload(payload, is_error=False)

    def _tool_verify(self, arguments: Mapping[str, Any]) -> Dict[str, Any]:
        """Implement ``ait_verify_fix`` with strict argument validation."""
        _reject_unknown(arguments, ("file_path", "candidate_code"))
        file_path = arguments.get("file_path")
        candidate = arguments.get("candidate_code")
        if not isinstance(file_path, str) or not file_path.strip():
            raise UsageError("ait_verify_fix requires a non-empty 'file_path'")
        if not isinstance(candidate, str) or not candidate.strip():
            raise UsageError("ait_verify_fix requires 'candidate_code'")
        from pathlib import Path

        path = Path(file_path).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise UsageError(f"File is not accessible: {file_path}") from exc
        if not resolved.is_file():
            raise UsageError(f"Expected a file: {file_path}")
        try:
            original = decode_source(resolved.read_bytes())
        except OSError as exc:
            raise ToolFailure(f"Cannot read {file_path}: {exc}") from exc
        try:
            payload = self.engine.verify_fix(resolved.name, original, candidate)
        except UsageError as exc:
            raise ToolFailure(f"Candidate code does not parse: {exc}") from exc
        if original == candidate:
            # The caller already wrote the patch: the baseline is the candidate,
            # so "resolved" can only be empty. Say so instead of implying a pass.
            payload["baseline_matches_candidate"] = True
            payload["note"] = (
                "candidate_code is identical to the file on disk; verify before writing"
            )
        return _tool_payload(payload, is_error=False)


def _tool_payload(payload: Mapping[str, Any], is_error: bool) -> Dict[str, Any]:
    """Wrap a payload in the MCP tool result shape.

    ``structuredContent`` carries the object once; the text mirror is compact
    JSON (no indentation) so a 20-finding scan does not roughly double in size.
    ``isError`` is reserved for tool failure — findings are a successful result.
    """
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": payload,
        "isError": is_error,
    }


def _reject_unknown(arguments: Mapping[str, Any], allowed: Tuple[str, ...]) -> None:
    """Reject arguments the schema does not declare instead of ignoring them."""
    unknown = sorted(set(arguments) - set(allowed))
    if unknown:
        raise UsageError(f"unknown argument(s): {', '.join(unknown)}")


def _as_bool(arguments: Mapping[str, Any], key: str, default: bool) -> bool:
    """Read a boolean argument without accepting truthy strings."""
    value = arguments.get(key, default)
    if not isinstance(value, bool):
        raise UsageError(f"'{key}' must be a boolean")
    return value


def _as_int(
    arguments: Mapping[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """Read a bounded integer argument, rejecting out-of-range values."""
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise UsageError(f"'{key}' must be an integer")
    if value < minimum or value > maximum:
        raise UsageError(f"'{key}' must be between {minimum} and {maximum}")
    return value


def _result_frame(request_id: Any, result: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a JSON-RPC success frame."""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_frame(code: int, message: str, request_id: Any) -> Dict[str, Any]:
    """Build a JSON-RPC error frame."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def main(argv: Optional[List[str]] = None) -> int:
    """Run the stdio bridge until EOF, or answer ``--help``/``--version``."""
    parser = argparse.ArgumentParser(
        prog="ait-mcp",
        description=(
            "AiT-test MCP bridge: JSON-RPC 2.0 over stdin/stdout. "
            "With no arguments it serves until stdin closes."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{ENGINE_NAME}-mcp {ENGINE_VERSION}",
    )
    parser.parse_args(argv)
    configure_stream(sys.stdout)
    configure_stream(sys.stderr)
    server = Server()
    try:
        return server.serve()
    except KeyboardInterrupt:
        return EXIT_INTERRUPT
    except (OSError, ValueError) as exc:
        server._log(f"fatal: {type(exc).__name__}: {exc}")
        return EXIT_INTERNAL
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
