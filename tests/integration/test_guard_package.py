import ast
import sys
from importlib.metadata import distribution
from pathlib import Path

import rag_access_guard


def test_guard_has_no_runtime_dependencies() -> None:
    assert distribution("rag-access-guard").requires is None


def test_guard_imports_only_itself_and_standard_library() -> None:
    package = Path(rag_access_guard.__file__).parent
    sources = list(package.rglob("*.py"))
    assert sources
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module is not None
                modules = [node.module]
            else:
                continue
            for module in modules:
                root = module.split(".", maxsplit=1)[0]
                assert root == "rag_access_guard" or root in sys.stdlib_module_names, source


def test_guard_ships_typed_marker() -> None:
    assert (Path(rag_access_guard.__file__).parent / "py.typed").is_file()
