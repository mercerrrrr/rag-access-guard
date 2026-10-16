"""Shared command-line entry point, preserving original security defaults."""

from typing import assert_never

from experiments.run_config import RunOptions
from experiments.suite_dispatch import (
    DemoQualityInvocation,
    SecurityInvocation,
    parse_invocation,
    run_suite,
)


def parse_options() -> RunOptions:
    """Retain the original security parser interface for existing consumers."""
    invocation = parse_invocation()
    match invocation:
        case SecurityInvocation(options=options):
            return options
        case DemoQualityInvocation():
            message = "Security options require the security suite"
            raise ValueError(message)
        case _:
            assert_never(invocation)


def main() -> int:
    """Dispatch before creating outputs or accessing a runtime."""
    return run_suite(parse_invocation())


if __name__ == "__main__":
    raise SystemExit(main())
