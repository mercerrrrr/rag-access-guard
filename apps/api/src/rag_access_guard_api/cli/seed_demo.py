"""Thin private-config CLI for the owned, resumable demo setup service."""

import argparse
import json
import sys
from pathlib import Path
from subprocess import SubprocessError
from typing import NoReturn, override

import anyio
from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.files import load_config
from rag_access_guard_api.demo.seed import SeedOptions, seed_demo
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.demo_corpus import CorpusValidationError
from rag_access_guard_api.services.errors import (
    AlreadyAuthenticatedError,
    ForbiddenError,
    GrantConflictError,
    RateLimitedError,
    RoleConflictError,
    UnauthenticatedError,
)
from rag_access_guard_api.services.text_documents import DocumentError


class Arguments(argparse.Namespace):
    """Explicit private input and bounded mutation switches; no credential arguments."""

    config: Path = Path()
    apply: bool = False
    dry_run: bool = False
    initialize_owned_database: bool = False
    accept_content_update: list[str] | None = None


class SafeParser(argparse.ArgumentParser):
    """Invalid argument values are never echoed into process output."""

    @override
    def error(self, message: str) -> NoReturn:
        raise DemoOwnershipError(code="invalid_arguments")


def main() -> int:
    """Stdout carries only safe report JSON or a safe failure code, never private paths."""
    parser = SafeParser(description=__doc__)
    _ = parser.add_argument("--config", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    _ = mode.add_argument("--dry-run", action="store_true")
    _ = mode.add_argument("--apply", action="store_true")
    _ = parser.add_argument("--initialize-owned-database", action="store_true")
    _ = parser.add_argument("--accept-content-update", action="append", metavar="KEY")
    try:
        arguments = parser.parse_args(namespace=Arguments())
        options = SeedOptions(
            apply=arguments.apply,
            initialize_owned_database=arguments.initialize_owned_database,
            accepted_content_updates=frozenset(arguments.accept_content_update or ()),
        )
        config = load_config(arguments.config)
        report = anyio.run(
            seed_demo, config, options, backend_options={"loop_factory": create_event_loop}
        )
    except (DemoOwnershipError, CorpusValidationError) as error:
        _ = sys.stdout.write(json.dumps({"error": error.code}) + "\n")
        return 1
    except (
        SQLAlchemyError,
        CommandError,
        OSError,
        SubprocessError,
        ValueError,
        AlreadyAuthenticatedError,
        ForbiddenError,
        GrantConflictError,
        RateLimitedError,
        RoleConflictError,
        UnauthenticatedError,
        DocumentError,
        EmbeddingError,
    ):
        _ = sys.stdout.write('{"error":"seed_failed"}\n')
        return 1
    _ = sys.stdout.write(report.model_dump_json() + "\n")
    return 0
