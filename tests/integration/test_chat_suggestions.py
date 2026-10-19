from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from rag_access_guard import PolicySnapshot, SourceRef
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.demo.reports import SeedSuggestion, SeedSuggestions
from rag_access_guard_api.persistence import DocumentChunk
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.demo import SuggestionList
from tests.integration.test_chat_history_boundaries import add_document
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def source_refs(case: ChatCase, document_id: UUID) -> tuple[SourceRef, ...]:
    with case.database.connect() as connection:
        rows = (
            connection.execute(
                select(
                    DocumentChunk.document_id, DocumentChunk.document_version_id, DocumentChunk.id
                )
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.ordinal)
            )
            .tuples()
            .all()
        )
    return tuple(SourceRef(document_id=d, document_version_id=v, chunk_id=c) for d, v, c in rows)


def configure(
    path: Path, suggestions: tuple[SeedSuggestion, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = SeedSuggestions(
        instance_id=str(uuid4()), dataset_id="neutral-corpus", suggestions=suggestions
    )
    _ = path.write_text(bundle.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("RAG_ACCESS_GUARD_SUGGESTIONS_PATH", str(path))


def get_items(case: ChatCase) -> list[dict[str, str]]:
    response = case.client.get("/api/chat/suggestions")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    data = SuggestionList.model_validate_json(response.content)
    return [{"id": item.id, "question": item.question} for item in data.items]


def test_suggestion_rechecks_every_source_dependency(
    chat_case: ChatCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    second = add_document(chat_case)
    refs = (*source_refs(chat_case, second.id), *source_refs(chat_case, chat_case.document.id))
    configure(
        tmp_path / "suggestions.json",
        (
            SeedSuggestion(
                id="paired",
                question="A question requiring both documents?",
                required_source_refs=refs,
            ),
        ),
        monkeypatch,
    )
    assert get_items(chat_case) == [
        {"id": "paired", "question": "A question requiring both documents?"}
    ]
    chat_case.revoke()
    assert get_items(chat_case) == []


@pytest.mark.parametrize("change", ["version", "inactive", "invalid_ref", "policy_failure"])
def test_stale_or_denied_suggestion_never_returns_private_question(
    chat_case: ChatCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    refs = source_refs(chat_case, chat_case.document.id)
    if change == "invalid_ref":
        refs = (
            SourceRef(
                document_id=refs[0].document_id,
                document_version_id=refs[0].document_version_id,
                chunk_id=uuid4(),
            ),
        )
    configure(
        tmp_path / "suggestions.json",
        (SeedSuggestion(id="private", question="PRIVATE_QUESTION", required_source_refs=refs),),
        monkeypatch,
    )
    if change == "version":
        assert (
            chat_case.client.post(
                f"/api/admin/documents/{chat_case.document.id}/versions",
                files={"file": ("new.txt", b"NEW_CONTENT", "text/plain")},
                headers=ChatHttp.csrf(chat_case.client),
            ).status_code
            == 201
        )
    elif change == "inactive":
        assert (
            chat_case.client.patch(
                f"/api/admin/documents/{chat_case.document.id}",
                json={"is_active": False},
                headers=ChatHttp.csrf(chat_case.client),
            ).status_code
            == 200
        )
    elif change == "policy_failure":

        async def failed(
            _self: PostgresPolicyReader,
            _principal: UUID,
            _refs: tuple[SourceRef, ...],
            *,
            thread_id: UUID | None = None,
        ) -> PolicySnapshot:
            del thread_id
            message = "PRIVATE_POLICY_FAILURE"
            raise ConnectionError(message)

        monkeypatch.setattr(PostgresPolicyReader, "snapshot", failed)
    assert get_items(chat_case) == []


def test_filter_precedes_limit_and_keeps_private_manifest_order(
    chat_case: ChatCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refs = source_refs(chat_case, chat_case.document.id)
    configure(
        tmp_path / "suggestions.json",
        (
            SeedSuggestion(id="empty", question="PRIVATE_EMPTY", required_source_refs=()),
            *(
                SeedSuggestion(
                    id=f"item-{index}", question=f"Question {index}?", required_source_refs=refs
                )
                for index in range(6)
            ),
        ),
        monkeypatch,
    )
    assert get_items(chat_case) == [
        {"id": f"item-{index}", "question": f"Question {index}?"} for index in range(4)
    ]


def test_suggestions_do_not_inherit_another_principals_grant(
    chat_case: ChatCase, chat_http: ChatHttp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(
        tmp_path / "suggestions.json",
        (
            SeedSuggestion(
                id="private",
                question="PRIVATE_QUESTION",
                required_source_refs=source_refs(chat_case, chat_case.document.id),
            ),
        ),
        monkeypatch,
    )
    assert len(get_items(chat_case)) == 1
    response = chat_http.other.get("/api/chat/suggestions")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_missing_configuration_is_empty_without_loading_models(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAG_ACCESS_GUARD_SUGGESTIONS_PATH", raising=False)
    assert get_items(chat_case) == []


def test_configured_manifest_error_is_sanitized_and_not_cached(
    chat_case: ChatCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_SUGGESTIONS_PATH", str(tmp_path / "PRIVATE_MISSING.json"))
    response = chat_case.client.get("/api/chat/suggestions")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
    assert "PRIVATE_MISSING" not in response.text


def test_independent_direct_grant_survives_role_membership_removal(
    chat_case: ChatCase, role_grant: GrantView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(
        tmp_path / "suggestions.json",
        (
            SeedSuggestion(
                id="permitted",
                question="Question?",
                required_source_refs=source_refs(chat_case, chat_case.document.id),
            ),
        ),
        monkeypatch,
    )
    assert len(get_items(chat_case)) == 1
    assert (
        chat_case.client.delete(
            f"/api/admin/roles/{role_grant.role_id}/members/{chat_case.grant.user_id}",
            headers=ChatHttp.csrf(chat_case.client),
        ).status_code
        == 204
    )
    assert len(get_items(chat_case)) == 1
    chat_case.revoke()
    assert get_items(chat_case) == []


def test_manifest_failure_never_falls_back_to_previous_items(
    chat_case: ChatCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "suggestions.json"
    configure(
        path,
        (
            SeedSuggestion(
                id="permitted",
                question="Question?",
                required_source_refs=source_refs(chat_case, chat_case.document.id),
            ),
        ),
        monkeypatch,
    )
    assert len(get_items(chat_case)) == 1
    _ = path.write_text("invalid private manifest", encoding="utf-8")
    response = chat_case.client.get("/api/chat/suggestions")
    assert response.status_code == 503
    assert "permitted" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
