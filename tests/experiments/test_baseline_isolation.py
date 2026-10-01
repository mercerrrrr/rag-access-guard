import ast
import subprocess
import sys
from pathlib import Path


def test_baseline_not_reachable_from_app() -> None:
    roots = (Path("apps/api/src"), Path("packages/rag_access_guard/src"))
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(not name.name.startswith("experiments") for name in node.names), path
                elif isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("experiments"), path
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from rag_access_guard_api.main import app
def controls(value):
    if isinstance(value, dict):
        return {key: controls(item) for key, item in value.items()
                if key not in {'description', 'summary'}}
    if isinstance(value, list):
        return [controls(item) for item in value]
    return value
assert not any(key.startswith('experiments') for key in sys.modules)
assert 'baseline' not in str(controls(app.openapi())).lower()
""",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    for path in Path("apps/web/src").rglob("*"):
        if path.is_file() and path.suffix in {".ts", ".vue"}:
            assert "baseline" not in path.read_text(encoding="utf-8").lower(), path
