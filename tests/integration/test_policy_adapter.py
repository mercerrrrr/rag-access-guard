from collections.abc import Callable
from typing import TYPE_CHECKING, TypedDict
from uuid import uuid4

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard import SourceRef
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine
from rag_access_guard_api.persistence import DocumentChunk
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.security import PolicyUnitOfWork


class LoopOptions(TypedDict):
    loop_factory: Callable[[], "AbstractEventLoop"]


@pytest.fixture
def anyio_backend() -> tuple[str, LoopOptions]:
    return "asyncio", {"loop_factory": create_event_loop}


@pytest.mark.anyio
async def test_canonical_hashes_exactly_cover_allowed_refs(
    admin_client: TestClient, auth_database: Engine, self_grant: GrantView
) -> None:
    assert self_grant.user_id is not None
    with auth_database.connect() as connection:
        row = (
            connection.execute(
                select(
                    DocumentChunk.document_id,
                    DocumentChunk.document_version_id,
                    DocumentChunk.id,
                    DocumentChunk.content_sha256,
                )
            )
            .tuples()
            .one()
        )
    ref = SourceRef(document_id=row[0], document_version_id=row[1], chunk_id=row[2])
    engine = create_database_engine(Settings())
    try:
        async with PolicyUnitOfWork(engine).protected_read(
            admin_client.cookies["__Host-rag_session"]
        ) as uow:
            reader = PostgresPolicyReader(uow)
            snapshot = await reader.snapshot(uow.principal.principal_id, (ref, ref))
            assert snapshot.allowed_refs == (ref,)
            assert snapshot.denied_refs == ()
            assert snapshot.canonical_chunk_hashes == ((ref, row[3]),)
            assert snapshot.provenance_valid
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_source_tuple_mismatch_is_invalid_provenance(
    admin_client: TestClient, auth_database: Engine, self_grant: GrantView
) -> None:
    assert self_grant.user_id is not None
    with auth_database.connect() as connection:
        version, chunk = (
            connection.execute(select(DocumentChunk.document_version_id, DocumentChunk.id))
            .tuples()
            .one()
        )
    ref = SourceRef(document_id=uuid4(), document_version_id=version, chunk_id=chunk)
    engine = create_database_engine(Settings())
    try:
        async with PolicyUnitOfWork(engine).protected_read(
            admin_client.cookies["__Host-rag_session"]
        ) as uow:
            snapshot = await PostgresPolicyReader(uow).snapshot(uow.principal.principal_id, (ref,))
            assert not snapshot.provenance_valid
            assert snapshot.allowed_refs == ()
    finally:
        await engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case", ["different-principal", "closed-uow", "unknown-thread", "revoked", "inactive", "old"]
)
async def test_policy_reader_rejects_stale_or_unowned_access(
    admin_client: TestClient, auth_database: Engine, self_grant: GrantView, case: str
) -> None:
    assert self_grant.user_id is not None
    with auth_database.begin() as connection:
        document, version, chunk = (
            connection.execute(
                select(
                    DocumentChunk.document_id, DocumentChunk.document_version_id, DocumentChunk.id
                )
            )
            .tuples()
            .one()
        )
        if case == "revoked":
            _ = connection.execute(text("DELETE FROM document_grants"))
        if case == "inactive":
            _ = connection.execute(text("UPDATE documents SET is_active=false"))
        if case == "old":
            _ = connection.execute(text("UPDATE documents SET active_version_id=NULL"))
    ref = SourceRef(document_id=document, document_version_id=version, chunk_id=chunk)
    engine = create_database_engine(Settings())
    try:
        async with PolicyUnitOfWork(engine).protected_read(
            admin_client.cookies["__Host-rag_session"]
        ) as uow:
            reader = PostgresPolicyReader(uow)
            principal = uuid4() if case == "different-principal" else uow.principal.principal_id
            if case != "closed-uow":
                snapshot = await reader.snapshot(
                    principal, (ref,), thread_id=uuid4() if case == "unknown-thread" else None
                )
                assert snapshot.allowed_refs == ()
                if case in {"revoked", "inactive", "old"}:
                    assert snapshot.provenance_valid
                    assert snapshot.denied_refs == (ref,)
        if case == "closed-uow":
            snapshot = await reader.snapshot(principal, (ref,))
            assert not snapshot.principal_active
            assert snapshot.allowed_refs == ()
    finally:
        await engine.dispose()
