import ast
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "packages/rag_access_guard/src/rag_access_guard"


def _imports(tree: ast.AST) -> tuple[set[str], dict[str, str], set[str]]:
    aliases: dict[str, str] = {}
    roots: set[str] = set()
    invalid: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
                aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > 1:
                    invalid.add("external_relative_import")
                continue
            roots.add((node.module or "").split(".", 1)[0])
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return roots, aliases, invalid


def _unsafe_calls(tree: ast.AST, aliases: dict[str, str]) -> set[str]:
    invalid: set[str] = set()

    def qualified(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{qualified(node.value)}.{node.attr}"
        return ""

    banned = {
        "__import__",
        "exec",
        "eval",
        "import_module",
        "spec_from_file_location",
        "module_from_spec",
        "run_module",
        "run_path",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = qualified(node.func)
            if name.rsplit(".", 1)[-1] in banned or name.startswith("sys.path."):
                invalid.add("dynamic_import_or_path")
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and qualified(node) == "sys.path"
        ):
            invalid.add("dynamic_import_or_path")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store)
            and qualified(node.value) == "sys.path"
        ):
            invalid.add("dynamic_import_or_path")
    return invalid


def violations(source: str) -> set[str]:
    tree = ast.parse(source)
    roots, aliases, invalid = _imports(tree)
    return (
        invalid
        | _unsafe_calls(tree, aliases)
        | (roots - sys.stdlib_module_names - {"rag_access_guard"})
        | (roots & {"http", "urllib", "socket"})
    )


def test_core_and_demo_import_only_stdlib_and_guard() -> None:
    for path in (*CORE.rglob("*.py"), ROOT / "scripts/demo_guard_host.py"):
        assert not violations(path.read_text(encoding="utf-8")), path


@pytest.mark.parametrize(
    "source",
    [
        "import fastapi",
        "from rag_access_guard_api import app",
        "import sqlalchemy",
        "import ollama",
        "import requests",
        "import experiments",
        "import urllib.request",
        "import importlib as x; x.import_module('fastapi')",
        "from importlib import import_module as load; load('fastapi')",
        "__import__('fastapi')",
        "exec('import fastapi')",
        "eval('x')",
        "import sys as s; s.path.append('src')",
        "import sys; sys.path = []",
        "import sys; sys.path[:] = []",
        "from sys import path as p; p.append('src')",
    ],
)
def test_scanner_rejects_host_dependencies_and_dynamic_bypasses(source: str) -> None:
    assert violations(source)


def test_core_declares_no_runtime_dependencies() -> None:
    metadata = tomllib.loads((ROOT / "packages/rag_access_guard/pyproject.toml").read_text())
    assert metadata["project"]["name"] == "rag-access-guard"
    assert metadata["project"]["dependencies"] == []
