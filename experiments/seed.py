"""Canonical synthetic corpus and identities shared through a database snapshot."""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from experiments.fixture_manifest import FixtureManifest, read_fixture
from experiments.scenario_types import DirectGrant, Principal, RoleGrant, Scenario
from rag_access_guard_api.adapters.embeddings import MODEL_ID
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION
from rag_access_guard_api.persistence import Session, User
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.documents import register_text_document
from rag_access_guard_api.services.grants import grant_role, grant_user
from rag_access_guard_api.services.indexing import prepare_index
from rag_access_guard_api.services.passwords import hash_password
from rag_access_guard_api.services.roles import create_role, set_membership
from rag_access_guard_api.services.security import PolicyUnitOfWork, database_clock
from rag_access_guard_api.services.tokens import issue_token, token_digest


@dataclass(frozen=True, slots=True)
class SyntheticEmbedder:
    """Deterministic structural-test vectors, explicitly not semantic-quality evidence."""

    model_id: str = MODEL_ID
    revision: str = MODEL_REVISION
    dimension: int = 384

    async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Give the same validated vector to every canonical test passage."""
        return tuple((1.0, *(0.0 for _ in range(383))) for _ in texts)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Use the same query vector in both arms."""
        del text
        return (1.0, *(0.0 for _ in range(383)))


@dataclass(frozen=True, slots=True)
class Actor:
    """Private synthetic session credentials, excluded from evidence representations."""

    user_id: UUID
    session_id: UUID
    token: str = field(repr=False)
    csrf: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SeededCorpus:
    """Stable identities cloned with the canonical database, not regenerated per arm."""

    actors: dict[Principal, Actor]
    documents: dict[str, UUID]
    grants: dict[tuple[str, str, str], UUID]
    roles: dict[str, UUID]


async def seed_actors(engine: AsyncEngine) -> dict[Principal, Actor]:
    """Seed synthetic accounts and normal digest-backed server sessions."""
    actors: dict[Principal, Actor] = {}
    password_hash = await asyncio.to_thread(hash_password, issue_token())
    names: tuple[Principal, ...] = ("student", "teacher", "staff", "admin")
    async with engine.begin() as connection:
        now = await database_clock(connection)
        for name in names:
            actor = Actor(uuid4(), uuid4(), issue_token(), issue_token())
            _ = await connection.execute(
                insert(User).values(
                    id=actor.user_id,
                    login=name,
                    display_name=f"Synthetic {name}",
                    password_hash=password_hash,
                    is_admin=name == "admin",
                )
            )
            _ = await connection.execute(
                insert(Session).values(
                    id=actor.session_id,
                    user_id=actor.user_id,
                    token_digest=token_digest(actor.token),
                    csrf_token_digest=token_digest(actor.csrf),
                    absolute_expires_at=now + timedelta(hours=8),
                )
            )
            actors[name] = actor
    return actors


async def seed_corpus(
    engine: AsyncEngine, case: Scenario, manifest: FixtureManifest, root: Path
) -> SeededCorpus:
    """Ingest real fixture bytes and grants through existing production services."""
    actors = await seed_actors(engine)
    documents: dict[str, UUID] = {}
    grants: dict[tuple[str, str, str], UUID] = {}
    roles: dict[str, UUID] = {}
    policy = PolicyUnitOfWork(engine)
    for fixture in manifest.fixtures:
        data = await asyncio.to_thread(read_fixture, root, fixture)
        prepared = await prepare_index(
            UploadPayload(filename=Path(fixture.path).name, media_type="text/plain", data=data),
            SyntheticEmbedder(),
        )
        async with policy.mutation(actors["admin"].token) as uow:
            document = await register_text_document(uow, f"Synthetic {fixture.key}", prepared)
            documents[fixture.key] = document.id
    for grant in case.initial_grants:
        async with policy.mutation(actors["admin"].token) as uow:
            match grant:
                case DirectGrant():
                    edge = await grant_user(
                        uow, documents[grant.document], actors[grant.user].user_id
                    )
                    grants[("direct", grant.document, grant.user)] = edge.id
                case RoleGrant():
                    if grant.role not in roles:
                        role = await create_role(uow, code=grant.role, display_name=grant.role)
                        roles[grant.role] = role.id
                    for member in grant.members:
                        await set_membership(
                            uow, roles[grant.role], actors[member].user_id, present=True
                        )
                    edge = await grant_role(uow, documents[grant.document], roles[grant.role])
                    grants[("role", grant.document, grant.role)] = edge.id
    return SeededCorpus(actors, documents, grants, roles)
