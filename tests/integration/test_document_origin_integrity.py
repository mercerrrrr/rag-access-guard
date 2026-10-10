from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Literal, assert_never, override

import pytest
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from rag_access_guard_api.routes import chat as routes
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services import chat, ingestion
from rag_access_guard_api.services.chat_state import Reservation
from rag_access_guard_api.services.origin import canonical_origin_hash
from rag_access_guard_api.services.sources import build_source_url
from tests.integration.test_document_origin import request, synthetic_origin, upload_origin
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


@pytest.mark.parametrize("consistent_hash", [False, True])
def test_origin_changed_during_model_inference_is_not_released(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, *, consistent_hash: bool
) -> None:
    # Given an authorized captured request with no policy revision change.
    _ = upload_origin(chat_case, monkeypatch, synthetic_origin())
    changed = synthetic_origin().model_copy(update={"transformation_revision": "tampered-v2"})
    chat_case.model.hold = True
    # When corruption bypasses the trigger only inside the owned disposable database.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(chat_case.send, request())
        try:
            assert chat_case.model.entered.wait(10)
            with chat_case.database.begin() as connection:
                _ = connection.execute(text("SET LOCAL session_replication_role=replica"))
                _ = connection.execute(
                    text(
                        """UPDATE document_origins SET origin=CAST(:origin AS jsonb),
                        origin_sha256=:hash"""
                    ),
                    {
                        "origin": changed.model_dump_json(),
                        "hash": canonical_origin_hash(changed) if consistent_hash else "1" * 64,
                    },
                )
        finally:
            chat_case.model.resume.set()
        response = pending.result(10)
    # Then even a internally consistent replacement cannot match the prepared digest.
    assert response.turn.state == "neutral"
    assert response.turn.answer is None
    assert response.turn.sources == ()
    assert chat_case.model.call_count == 1
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM chat_turns WHERE answer IS NOT NULL")
            ).scalar_one()
            == 0
        )
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == 0


def test_version_and_origin_roll_back_atomically(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a parse artifact failing after version and origin insertion.
    prepare = ingestion.prepare_upload

    def invalid(upload: UploadPayload) -> ingestion.PreparedUpload:
        prepared = prepare(upload)
        return replace(
            prepared,
            origin=synthetic_origin(),
            chunks=(replace(prepared.chunks[0], char_end=999999),),
        )

    monkeypatch.setattr(ingestion, "prepare_upload", invalid)
    with chat_case.database.connect() as connection:
        versions = TypeAdapter(int).validate_python(
            connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one()
        )
    # When
    response = chat_case.client.post(
        f"/api/admin/documents/{chat_case.document.id}/versions",
        files={"file": ("bad.txt", b"Synthetic", "text/plain")},
        headers=ChatHttp.csrf(chat_case.client),
    )
    # Then
    assert response.status_code == 422
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one()
            == versions
        )
        assert connection.execute(text("SELECT count(*) FROM document_origins")).scalar_one() == 0


def test_origin_cannot_be_attached_to_a_preexisting_version(chat_case: ChatCase) -> None:
    # Given a legacy immutable version committed by the ordinary upload path.
    origin = synthetic_origin()
    # When / Then
    with (
        chat_case.database.begin() as connection,
        pytest.raises(IntegrityError, match="new version"),
    ):
        _ = connection.execute(
            text("""INSERT INTO document_origins(version_id,origin,origin_sha256)
            VALUES (:id,CAST(:origin AS jsonb),:hash)"""),
            {
                "id": chat_case.document.active_version_id,
                "origin": origin.model_dump_json(),
                "hash": canonical_origin_hash(origin),
            },
        )


@pytest.mark.parametrize("corruption", ["hash", "content", "invalid"])
def test_corrupt_origin_closes_source_original_history_and_generation(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    # Given a completed authorized synthetic answer.
    ref = upload_origin(chat_case, monkeypatch, synthetic_origin())
    answer = chat_case.send(request())
    assert answer.turn.state == "available"
    with chat_case.database.begin() as connection:
        _ = connection.execute(text("SET LOCAL session_replication_role=replica"))
        expression = {
            "hash": "UPDATE document_origins SET origin_sha256=repeat('1',64)",
            "content": """UPDATE document_origins SET origin=
            jsonb_set(origin,'{transformation_revision}','\"tampered\"'::jsonb)""",
            "invalid": """UPDATE document_origins SET origin=
            jsonb_set(origin,'{source_sha256}','\"invalid\"'::jsonb)""",
        }[corruption]
        _ = connection.execute(text(expression))
    # When / Then
    for url in (build_source_url(ref), build_source_url(ref).replace("/content?", "/original?")):
        response = chat_case.client.get(url)
        assert response.status_code == 404
        assert response.headers["cache-control"] == "private, no-store"
    assert chat_case.read().turns[0].state == "unavailable"
    response = chat_case.send(request(1))
    assert response.turn.state == "neutral"
    assert chat_case.model.call_count == 1


@pytest.mark.parametrize("fault", ["digest", "context", "budget"])
def test_release_rejects_origin_attempt_tampering(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
    fault: Literal["digest", "context", "budget"],
) -> None:
    # Given
    _ = upload_origin(chat_case, monkeypatch, synthetic_origin())

    class TamperingService(chat.ChatService):
        @override
        async def _complete(
            self,
            session_token: str,
            outcome: chat.Generated | chat.Neutral,
            *,
            expected_attempt: Reservation,
        ) -> chat.Completion:
            assert isinstance(outcome, chat.Generated)
            match fault:
                case "digest":
                    changed = replace(outcome.attempt, origin_binding="0" * 64)
                case "context":
                    changed = replace(
                        outcome.attempt, model_context=outcome.attempt.prepared.model_context
                    )
                case "budget":
                    changed = replace(outcome.attempt, context_budget=5000)
                case _:
                    assert_never(fault)
            return await super()._complete(
                session_token, replace(outcome, attempt=changed), expected_attempt=expected_attempt
            )

    monkeypatch.setattr(routes, "ChatService", TamperingService)
    # When
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=request().model_dump(mode="json"),
        headers=ChatHttp.csrf(chat_case.client),
    )
    # Then
    assert response.status_code == 403
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM chat_turns WHERE answer IS NOT NULL")
            ).scalar_one()
            == 0
        )
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == 0
