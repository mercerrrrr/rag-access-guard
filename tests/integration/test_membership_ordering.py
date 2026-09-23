from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from uuid import UUID

import pytest
from anyio.to_thread import run_sync
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Integer, func, select, text

from rag_access_guard_api.persistence import User
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.services import roles
from rag_access_guard_api.services.security import MutationUoW


def test_membership_revoke_before_read_gate_denies(
    admin_client: TestClient,
    auth_database: Engine,
    role_grant: GrantView,
    registered_document: DocumentSummary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = Event(), Event()
    backend: list[int] = []
    original = roles.set_membership
    with auth_database.connect() as connection:
        member = connection.execute(select(User.id)).scalar_one()

    async def paused(uow: MutationUoW, role_id: UUID, user_id: UUID, *, present: bool) -> None:
        backend.append(
            (await uow.connection.execute(select(func.pg_backend_pid(type_=Integer)))).scalar_one()
        )
        await original(uow, role_id, user_id, present=present)
        entered.set()
        assert await run_sync(release.wait, 10)

    monkeypatch.setattr(roles, "set_membership", paused)
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    path = (
        f"/api/documents/{registered_document.id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(
            admin_client.delete,
            f"/api/admin/roles/{role_grant.role_id}/members/{member}",
            headers=headers,
        )
        try:
            assert entered.wait(10)
            reader = executor.submit(admin_client.get, path)
            deadline = monotonic() + 10
            with auth_database.connect() as observer:
                while True:
                    if observer.execute(
                        text("""SELECT EXISTS (
                        SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
                        AND :blocker=ANY(pg_blocking_pids(pid)) AND wait_event_type='Lock'
                        AND query LIKE '%policy_state%' AND query LIKE '%FOR %')"""),
                        {"blocker": backend[0]},
                    ).scalar_one():
                        break
                    assert monotonic() < deadline, "Expected policy lock wait was not observed"
                    observer.rollback()
        finally:
            release.set()
        assert writer.result(10).status_code == 204
        response = reader.result(10)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert "PROTECTED_SYNTHETIC" not in response.text
