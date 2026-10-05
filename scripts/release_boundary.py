"""Allowed public paths for the prototype release."""

from pathlib import PurePosixPath

PUBLIC_ROOTS = frozenset(
    {"apps", "packages", "tests", "experiments", "scripts", "typings", ".github"}
)
PUBLIC_FILES = frozenset(
    {
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "compose.yaml",
        ".env.example",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        ".node-version",
        ".python-version",
    }
)
PRIVATE_SUFFIXES = frozenset(
    {
        ".jsonl",
        ".log",
        ".pdf",
        ".doc",
        ".docx",
        ".odt",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".gguf",
        ".safetensors",
        ".onnx",
        ".pt",
        ".pth",
        ".sqlite",
        ".sqlite3",
        ".db",
    }
)


def validate_public_tree(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Reject tracked private material rather than relying on ignore rules alone."""
    violations: list[str] = []
    for name in paths:
        if name in PUBLIC_FILES:
            continue
        path = PurePosixPath(name)
        if (
            not path.parts
            or path.parts[0] not in PUBLIC_ROOTS
            or path.suffix.lower() in PRIVATE_SUFFIXES | {".md"}
            or any(part.startswith(".env") for part in path.parts)
            or any(
                part in {"..", ".codex", ".omo", ".git", "node_modules", ".venv"}
                for part in path.parts
            )
        ):
            violations.append(name)
    return tuple(violations)
