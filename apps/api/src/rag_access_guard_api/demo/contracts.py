"""Shared private configuration for setup and the future runtime launcher."""

from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, SecretStr

from rag_access_guard_api.demo.plan import SeedPlan
from rag_access_guard_api.schemas.demo_corpus import CorpusId, CorpusValue


class DemoRuntime(CorpusValue):
    """Paths and dedicated local resources consumed by the runtime launcher."""

    repository_root: Path
    state_directory: Path
    log_directory: Path
    ollama_supervisor_config: Path
    api_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    web_port: Annotated[int, Field(ge=1, le=65535)] = 4173
    postgres_port: Annotated[int, Field(ge=1, le=65535)] = 55472
    compose_project: CorpusId = "rag-access-guard-demo"


class DemoConfig(CorpusValue):
    """The single strict file contract; credentialed URLs never enter repr or JSON."""

    dataset_id: CorpusId
    database_url: SecretStr = Field(repr=False, exclude=True)
    corpus_manifest: Path
    ownership_manifest: Path
    credentials_path: Path
    access_definition: Path
    model_profile: Literal["qwen3-thinking-legacy-v1", "qwen3-instruct-demo-v1"]
    retrieval_config: Path
    runtime: DemoRuntime
    report_path: Path | None = None
    suggestions_path: Path | None = None


class DemoMarker(CorpusValue):
    """Immutable identity shared by the private manifest and database marker."""

    instance_id: UUID
    dataset_id: CorpusId
    database_name: str
    database_role: str


class SeedCounts(CorpusValue):
    """Desired object totals plus changes actually committed by this invocation."""

    users: int
    roles: int
    memberships: int
    grants: int
    documents: int
    new_objects: int = 0
    new_versions: int = 0


class SeedDocumentBinding(CorpusValue):
    """Server-generated active identities bound to the two independent hashes."""

    key: str
    document_id: UUID
    version_id: UUID
    upload_sha256: str
    origin_sha256: str


class SeedReport(CorpusValue):
    """Safe machine-readable result; private output locations stay out of serialized logs."""

    schema_version: Literal[1] = 1
    mode: Literal["dry_run", "applied"]
    instance_id: UUID
    dataset_id: CorpusId
    manifest_sha256: str
    counts: SeedCounts
    plan: SeedPlan
    documents: tuple[SeedDocumentBinding, ...] = ()
    policy_revision_before: int | None = None
    policy_revision_after: int | None = None
    failures: tuple[str, ...] = ()
    report_path: Path | None = Field(default=None, repr=False, exclude=True)
    suggestions_path: Path | None = Field(default=None, repr=False, exclude=True)
