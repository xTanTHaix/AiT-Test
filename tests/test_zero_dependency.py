"""Guardrail: the engine must run on a bare standard-library interpreter."""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

from tests._support import REPO_ROOT, SRC_DIR

ENGINE_ROOT = SRC_DIR / "ait"
FORBIDDEN = {"pytest", "_pytest", "setuptools", "pkg_resources"}


def _imported_modules(path: Path) -> set[str]:
    """Return every absolute module name imported by one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module)
    return modules


def _python_files() -> list[Path]:
    return sorted(ENGINE_ROOT.rglob("*.py"))


class StdlibOnlyTests(unittest.TestCase):
    def test_every_engine_import_is_stdlib(self) -> None:
        allowed = set(sys.stdlib_module_names)
        offenders: dict[str, str] = {}
        for path in _python_files():
            for module in _imported_modules(path):
                root = module.split(".", 1)[0]
                if root not in allowed:
                    offenders[path.name] = module
        self.assertEqual(offenders, {}, f"third-party imports: {offenders}")

    def test_root_entrypoints_import_only_stdlib(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"ait"}
        for name in ("cli.py", "mcp.py"):
            with self.subTest(entrypoint=name):
                for module in _imported_modules(REPO_ROOT / name):
                    self.assertIn(module.split(".", 1)[0], allowed, module)

    def test_no_test_module_depends_on_pytest(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"ait", "tests"}
        for path in sorted((REPO_ROOT / "tests").glob("*.py")):
            with self.subTest(test_module=path.name):
                for module in _imported_modules(path):
                    root = module.split(".", 1)[0]
                    self.assertNotIn(root, FORBIDDEN, f"{path.name} imports {module}")
                    self.assertIn(root, allowed, f"{path.name} imports {module}")

    def test_package_and_scripts_import_under_isolated_site(self) -> None:
        """``-S`` disables site-packages, proving no third-party runtime import."""
        script = (
            "import sys;"
            f"sys.path.insert(0, {str(SRC_DIR)!r});"
            "import ait, ait.cli, ait.mcp, ait.engine, ait.slicer;"
            "print(ait.__version__)"
        )
        result = subprocess.run(
            [sys.executable, "-S", "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1.0.0")

    def test_cli_runs_under_isolated_site(self) -> None:
        result = subprocess.run(
            [sys.executable, "-S", str(REPO_ROOT / "cli.py"), str(SRC_DIR), "--no-cache", "--no-color", "-q"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 failed", result.stdout)


class PackagingTests(unittest.TestCase):
    def test_pyproject_declares_no_runtime_dependencies(self) -> None:
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("dependencies = []", text)
        self.assertIn('name = "ait-test"', text)
        self.assertIn('version = "1.0.0"', text)

    def test_no_compiled_or_vendored_payload_is_shipped(self) -> None:
        suffixes = {path.suffix for path in ENGINE_ROOT.rglob("*") if path.is_file()}
        self.assertNotIn(".pyd", suffixes)
        self.assertNotIn(".so", suffixes)
        self.assertNotIn(".dll", suffixes)


if __name__ == "__main__":
    unittest.main()
