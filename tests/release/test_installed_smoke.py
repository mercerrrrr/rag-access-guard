import ast
import asyncio
import sys
from pathlib import Path

from tests.release.installed_guard_smoke import smoke


def test_installed_probe_checks_three_operations_and_denials() -> None:
    assert asyncio.run(smoke()) == {
        "prepare_allowed": True,
        "release_allowed": True,
        "read_allowed": True,
        "read_revoked": True,
        "release_revoked": True,
        "prepare_revoked": True,
        "unknown_denied": True,
        "other_principal_denied": True,
    }


def test_installed_probe_has_no_host_imports() -> None:
    source = Path(__file__).with_name("installed_guard_smoke.py")
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            modules = [node.module]
        for module in modules:
            root = module.split(".", maxsplit=1)[0]
            assert root == "rag_access_guard" or root in sys.stdlib_module_names
