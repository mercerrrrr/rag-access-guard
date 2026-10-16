"""Thin dataset alias for the shared demo-quality suite entry point."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.suite_dispatch import (
    parse_invocation,
    run_suite,
)


def main() -> int:
    """Map only the external dataset spelling; all validation and execution are shared."""
    arguments = [
        "--scenarios"
        if arg == "--dataset"
        else "--scenarios=" + arg.removeprefix("--dataset=")
        if arg.startswith("--dataset=")
        else arg
        for arg in sys.argv[1:]
    ]
    return run_suite(parse_invocation(["--suite", "demo-quality", *arguments]))


if __name__ == "__main__":
    raise SystemExit(main())
