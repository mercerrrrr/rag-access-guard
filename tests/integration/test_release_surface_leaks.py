from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import PoolProxiedConnection

from rag_access_guard_api.main import create_app
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services import chat_completion
from rag_access_guard_api.services.chat_state import NeutralReason, StoredTurn
from rag_access_guard_api.services.chat_turns import ReleasedAnswer, complete_turn
from rag_access_guard_api.services.security import ReadUoW
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase
from tests.support.release_surface import ReleaseSurface


@pytest.mark.parametrize("fail_commit", [False, True])
def test_protected_asgi_bytes_follow_actual_database_commit(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    *,
    fail_commit: bool,
) -> None:
    surface = ReleaseSurface(create_app(), chat_case.database)
    releasing: set[int] = set()
    original_commit = PGDialect_psycopg.do_commit

    async def mark(uow: ReadUoW, turn: StoredTurn, result: ReleasedAnswer | NeutralReason) -> int:
        revision = await complete_turn(uow, turn, result)
        uow.connection.info["surface_release"] = True
        return revision

    def before_commit(connection: Connection) -> None:
        if connection.info.pop("surface_release", False):
            assert not surface.bodies
            releasing.add(id(connection.connection))

    def commit(dialect: PGDialect_psycopg, connection: PoolProxiedConnection) -> None:
        is_release = id(connection) in releasing
        if is_release and fail_commit:
            message = "PRIVATE_COMMIT_DETAIL"
            raise SQLAlchemyError(message)
        original_commit(dialect, connection)
        if is_release:
            surface.committed.set()

    monkeypatch.setattr(chat_completion, "complete_turn", mark)
    monkeypatch.setattr(PGDialect_psycopg, "do_commit", commit)
    event.listen(Engine, "commit", before_commit)
    try:
        with TestClient(
            surface,
            base_url="https://rag.test",
            backend_options={"loop_factory": create_event_loop},
        ) as client:
            client.cookies.update(chat_case.client.cookies)
            response = client.post(
                f"/api/chat/threads/{chat_case.thread}/messages",
                json=MessageRequest(
                    request_id=uuid4(),
                    expected_thread_revision=0,
                    user_input="PRIVATE_USER_QUESTION",
                ).model_dump(mode="json"),
                headers=ChatHttp.csrf(client),
            )
    finally:
        event.remove(Engine, "commit", before_commit)
    assert releasing
    assert response.status_code == (503 if fail_commit else 200)
    assert response.headers["cache-control"] == "private, no-store"
    assert surface.protected_observations == ([] if fail_commit else [(True, 1)])
    assert "PRIVATE_COMMIT_DETAIL" not in response.text
    assert "PRIVATE_COMMIT_DETAIL" not in caplog.text
    assert "SYNTHETIC_ANSWER" not in caplog.text
    assert "PRIVATE_USER_QUESTION" not in caplog.text
    with chat_case.database.connect() as connection:
        for statement in (
            "SELECT count(*) FROM chat_turns WHERE answer IS NOT NULL",
            "SELECT count(*) FROM turn_sources",
            "SELECT count(*) FROM audit_events WHERE stage='release'",
        ):
            assert connection.execute(text(statement)).scalar_one() == (0 if fail_commit else 1)
        audits = TypeAdapter(tuple[str, ...]).validate_python(
            connection.execute(text("SELECT row_to_json(audit_events)::text FROM audit_events"))
            .scalars()
            .all()
        )
        assert all(
            "SYNTHETIC_ANSWER" not in row
            and "PROTECTED_SYNTHETIC" not in row
            and "PRIVATE_USER_QUESTION" not in row
            for row in audits
        )
