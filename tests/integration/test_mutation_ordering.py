from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from anyio.to_thread import run_sync
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Integer, func, select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.security import MutationUoW
from tests.integration.policy_probe import wait_for_policy_wait


def test_two_concurrent_mutations_increment_twice_without_lost_update(
    admin_client: TestClient, auth_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    backend: list[int] = []
    original = MutationUoW.record_change

    async def paused(uow: MutationUoW, event: AuditRecord) -> int:
        result = await original(uow, event)
        if not entered.is_set():
            backend.append(
                (
                    await uow.connection.execute(select(func.pg_backend_pid(type_=Integer)))
                ).scalar_one()
            )
            entered.set()
            assert await run_sync(release.wait, 10)
        return result

    monkeypatch.setattr(MutationUoW, "record_change", paused)
    with auth_database.connect() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            admin_client.post,
            "/api/admin/roles",
            json={"code": "first", "display_name": "First"},
            headers=headers,
        )
        try:
            assert entered.wait(10)
            second = executor.submit(
                admin_client.post,
                "/api/admin/roles",
                json={"code": "second", "display_name": "Second"},
                headers=headers,
            )
            wait_for_policy_wait(auth_database, backend[0])
        finally:
            release.set()
        assert first.result(10).status_code == 201
        assert second.result(10).status_code == 201
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == before + 2
        assert connection.execute(text("SELECT count(*) FROM roles")).scalar_one() == 2
        revisions = (
            connection.execute(
                text("""SELECT policy_revision FROM audit_events
                WHERE event_type='role_changed' ORDER BY policy_revision""")
            )
            .scalars()
            .all()
        )
        assert revisions == [before + 1, before + 2]
