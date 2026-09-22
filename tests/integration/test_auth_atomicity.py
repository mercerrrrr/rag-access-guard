from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text, update

from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine
from rag_access_guard_api.persistence import AuditEvent, PolicyState, Session, User
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.security import PolicyUnitOfWork


def test_logout_is_atomic_with_revision_and_audit(
    authenticated_client: TestClient, auth_database: Engine
) -> None:
    token = authenticated_client.cookies["__Host-rag_session"]
    csrf = authenticated_client.cookies["__Host-rag_csrf"]
    response = authenticated_client.post(
        "/api/auth/logout", headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf}
    )
    assert response.status_code == 204
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == 1
        assert connection.execute(select(Session.revoked_at)).scalar_one() is not None
        assert (
            connection.execute(
                select(AuditEvent.policy_revision).where(AuditEvent.event_type == "session_revoked")
            ).scalar_one()
            == 1
        )
    authenticated_client.cookies.set("__Host-rag_session", token)
    assert (
        authenticated_client.post(
            "/api/auth/logout", headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf}
        ).status_code
        == 401
    )
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == 1


def test_audit_failure_rolls_back_logout(
    authenticated_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(
            text(
                """ALTER TABLE audit_events ADD CONSTRAINT synthetic_audit_failure
                CHECK (event_type <> 'session_revoked')"""
            )
        )
    response = authenticated_client.post(
        "/api/auth/logout",
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": authenticated_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == 0
        assert connection.execute(select(Session.revoked_at)).scalar_one() is None
        assert (
            connection.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "session_revoked")
            ).scalar_one()
            == 0
        )
    assert authenticated_client.get("/api/auth/me").status_code == 200


def test_multiple_records_share_one_mutation_revision(
    authenticated_client: TestClient, auth_database: Engine
) -> None:
    async def change() -> tuple[int, int]:
        engine = create_database_engine(Settings())
        try:
            async with PolicyUnitOfWork(engine).mutation(
                authenticated_client.cookies["__Host-rag_session"]
            ) as uow:
                _ = await uow.connection.execute(update(User).values(display_name="Changed Reader"))
                event = AuditRecord(
                    event_type="user_changed",
                    stage="policy",
                    outcome="success",
                    principal_id=uow.principal.principal_id,
                )
                first = await uow.record_change(event)
                second = await uow.record_change(event)
                return first, second
        finally:
            await engine.dispose()

    portal = authenticated_client.portal
    assert portal is not None
    assert portal.call(change) == (1, 1)
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == 1
        assert (
            connection.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "user_changed", AuditEvent.policy_revision == 1)
            ).scalar_one()
            == 2
        )
