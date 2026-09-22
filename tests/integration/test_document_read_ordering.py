from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from uuid import UUID

import pytest
from anyio.to_thread import run_sync
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Integer, func, select, text

from rag_access_guard_api.schemas.access import DocumentText, GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.services import access, grants
from rag_access_guard_api.services.access import DocumentVersionRef
from rag_access_guard_api.services.security import MutationUoW, ReadUoW


def wait_for_policy_wait(engine: Engine, blocker: int) -> None:
    deadline = monotonic() + 10
    with engine.connect() as observer:
        while True:
            if observer.execute(
                text("""SELECT EXISTS (
                SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
                AND :blocker = ANY(pg_blocking_pids(pid)) AND wait_event_type='Lock'
                AND query LIKE '%policy_state%' AND query LIKE '%FOR %')"""),
                {"blocker": blocker},
            ).scalar_one():
                return
            assert monotonic() < deadline, "Expected policy lock wait was not observed"
            observer.rollback()


def test_read_waiting_for_revoke_uses_post_lock_grants(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    registered_document: DocumentSummary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real revoke service has changed grants while holding the policy lock.
    entered, release = Event(), Event()
    backend: list[int] = []
    original = grants.revoke_grant

    async def paused(uow: MutationUoW, document_id: UUID, grant_id: UUID) -> None:
        backend.append(
            (await uow.connection.execute(select(func.pg_backend_pid(type_=Integer)))).scalar_one()
        )
        await original(uow, document_id, grant_id)
        entered.set()
        assert await run_sync(release.wait, 10)

    monkeypatch.setattr("rag_access_guard_api.routes.access.revoke_grant", paused)
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    path = (
        f"/api/documents/{self_grant.document_id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(
            admin_client.delete,
            f"/api/admin/documents/{self_grant.document_id}/grants/{self_grant.id}",
            headers=headers,
        )
        try:
            assert entered.wait(10)
            reader = executor.submit(admin_client.get, path)
            # When: PostgreSQL proves the read is waiting behind the real revoke.
            wait_for_policy_wait(auth_database, backend[0])
        finally:
            release.set()
        assert writer.result(10).status_code == 204
        response = reader.result(10)
    # Then: the resumed read uses grants after commit, never its pre-wait snapshot.
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert "PROTECTED_SYNTHETIC" not in response.text


def test_read_finishes_before_waiting_revoke(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    registered_document: DocumentSummary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: canonical text was authorized under a real shared policy lock.
    entered, release = Event(), Event()
    backend: list[int] = []
    original = access.read_document_text

    async def paused(uow: ReadUoW, ref: DocumentVersionRef) -> DocumentText:
        backend.append(
            (await uow.connection.execute(select(func.pg_backend_pid(type_=Integer)))).scalar_one()
        )
        result = await original(uow, ref)
        entered.set()
        assert await run_sync(release.wait, 10)
        return result

    monkeypatch.setattr("rag_access_guard_api.routes.access.read_document_text", paused)
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    path = (
        f"/api/documents/{self_grant.document_id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        reader = executor.submit(admin_client.get, path)
        try:
            assert entered.wait(10)
            writer = executor.submit(
                admin_client.delete,
                f"/api/admin/documents/{self_grant.document_id}/grants/{self_grant.id}",
                headers=headers,
            )
            # When: revoke waits until the already authorized read releases its lock.
            wait_for_policy_wait(auth_database, backend[0])
        finally:
            release.set()
        response = reader.result(10)
        assert writer.result(10).status_code == 204
    # Then: the first read succeeds, but the same URL is denied on its next read.
    assert response.status_code == 200
    assert DocumentText.model_validate_json(response.content).text == "PROTECTED_SYNTHETIC"
    assert admin_client.get(path).status_code == 404
