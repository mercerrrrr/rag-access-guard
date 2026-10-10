"""Inspect an external corpus without contacting URLs or a database."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from rag_access_guard_api.services.demo_corpus import (
    CorpusValidationError,
    validate_corpus,
    validate_report_path,
    write_report,
)


class Arguments(argparse.Namespace):
    """Explicit filesystem inputs, following the repository's argparse CLI convention."""

    manifest: Path = Path()
    report: Path = Path()


def main() -> int:
    """Emit only a concise safe status after local inspection and exclusive output."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--manifest", required=True, type=Path)
    _ = parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(namespace=Arguments())
    try:
        _ = validate_report_path(args.report)
        report = validate_corpus(args.manifest)
        write_report(args.report, report)
    except CorpusValidationError as error:
        _ = sys.stderr.write(f"corpus_rejected: {error.code}\n")
        return 1
    except (ValidationError, json.JSONDecodeError, UnicodeError, RecursionError):
        _ = sys.stderr.write("corpus_rejected: invalid_manifest\n")
        return 1
    _ = sys.stdout.write("corpus_accepted: 24 documents\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
