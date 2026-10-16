from pathlib import Path
from typing import override
from uuid import UUID

import anyio
import httpx2
import pytest
from experiments.demo_collection import DemoCollector
from experiments.demo_run_contracts import DemoRunPlan, DemoUnit
from experiments.demo_sessions import DemoActorSessions
from pydantic import SecretStr
from tests.unit.demo_evaluation_fixtures import dataset

from rag_access_guard_api.demo.contracts import DemoMarker
from rag_access_guard_api.demo.credentials import CredentialEntry, CredentialJournal
from rag_access_guard_api.demo.evaluation_contracts import DemoEvaluationCase, DemoSourceRef
from rag_access_guard_api.schemas.evaluation_identity import EvaluationIdentity


@pytest.mark.parametrize(
    "mode",
    [
        "test",
        "wrong_profile",
        "timeout",
        "neutral_failure",
        "failure_identity_drift",
        "success_identity_drift",
        "close_http",
        "close_timeout",
        "success",
    ],
)
def test_collector_refuses_wrong_runtime_and_preserves_terminal_truth(
    tmp_path: Path,
    mode: str,
) -> None:

    value = dataset()
    case = value.cases[20]
    marker = DemoMarker(
        instance_id=UUID(int=3000),
        dataset_id="synthetic-test",
        database_name="synthetic-test",
        database_role="synthetic-role",
    )
    journal = CredentialJournal(
        marker=marker,
        entries=(
            CredentialEntry(login=case.principal_login, password=SecretStr("synthetic-password")),
        ),
    )
    identity = EvaluationIdentity(
        profile_hash=value.profile_hash,
        model_digest="e" * 64,
        renderer_identity="renderer",
        retrieval_config_hash="f" * 64,
        corpus_sha256=value.corpus_sha256,
        policy_revision=1,
        demo_instance_id=marker.instance_id,
    )
    unit = DemoUnit(unit_id="0:" + case.id, case_id=case.id, gold=case, request_id=UUID(int=4000))
    plan = DemoRunPlan(
        output=tmp_path,
        git_sha="a" * 40,
        config_sha256="c" * 64,
        dataset_sha256=value.dataset_sha256,
        profile_hash=value.profile_hash,
        model_digest="e" * 64,
        renderer_identity="renderer",
        corpus_sha256=value.corpus_sha256,
        retrieval_config_hash="f" * 64,
        mode="holdout",
        schedule=(unit,),
    )
    paths: list[str] = []

    def response(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        paths.append(path)
        identity_reads = paths.count("/api/system/evaluation-identity")
        selected = (
            identity.model_copy(update={"profile_hash": "0" * 64})
            if mode == "wrong_profile"
            or (mode in {"failure_identity_drift", "success_identity_drift"} and identity_reads > 1)
            else identity
        )
        thread = {
            "id": str(UUID(int=5000)),
            "title": "New",
            "revision": 0,
            "created_at": "2026-01-01T00:00:00Z",
        }
        responses = {
            "/api/auth/csrf": httpx2.Response(200, json={"csrf_token": "challenge"}),
            "/api/auth/login": httpx2.Response(
                200,
                json={
                    "user": {
                        "id": str(UUID(int=1)),
                        "login": case.principal_login,
                        "display_name": "Synthetic",
                        "is_admin": False,
                    },
                    "csrf_token": "session-csrf",
                },
                headers={"set-cookie": "rag_session_local=session; Path=/"},
            ),
            "/api/system/status": httpx2.Response(
                200,
                json={
                    "mode": "test" if mode == "test" else "local",
                    "model": {"name": "synthetic", "state": "test" if mode == "test" else "ready"},
                    "search": {"state": "ready"},
                },
            ),
            "/api/system/evaluation-identity": httpx2.Response(
                200, content=selected.model_dump_json()
            ),
            "/api/search": httpx2.Response(
                200,
                json={
                    "items": [
                        {
                            "source_ref": {
                                "document_id": str(UUID(int=21)),
                                "document_version_id": str(UUID(int=21)),
                                "chunk_id": str(UUID(int=21)),
                            },
                            "text": "synthetic-private-search-text",
                            "token_count": 1,
                        }
                    ]
                },
            ),
            "/api/chat/threads": httpx2.Response(201, json=thread),
            f"/api/chat/threads/{UUID(int=5000)}": httpx2.Response(
                200, json={**thread, "turns": []}
            ),
        }
        if path == f"/api/chat/threads/{UUID(int=5000)}/messages":
            if mode == "timeout":
                message = "synthetic timeout"
                raise httpx2.ReadTimeout(message, request=request)
            failure = "\u041d\u0435 удалось получить ответ. Повторите запрос."
            available = mode in {"close_http", "close_timeout"}
            return httpx2.Response(
                200,
                json={
                    "thread_revision": 1,
                    "replayed": False,
                    "turn": {
                        "id": str(UUID(int=6000)),
                        "request_id": str(unit.request_id),
                        "user_input": case.question,
                        "state": "available" if available else "neutral",
                        "answer": "synthetic-sensitive-answer" if available else None,
                        "sources": [
                            {
                                "document_id": str(UUID(int=21)),
                                "document_version_id": str(UUID(int=21)),
                                "chunk_id": str(UUID(int=21)),
                                "title": "Synthetic",
                                "url": "/api/documents/synthetic/content",
                            }
                        ]
                        if available
                        else [],
                        "message": None
                        if available
                        else failure
                        if mode in {"neutral_failure", "failure_identity_drift"}
                        else "Нет доступных источников для ответа.",
                    },
                },
            )
        return responses.get(path, httpx2.Response(404))

    class FailureOnClose(httpx2.MockTransport):
        @override
        async def aclose(self) -> None:
            await super().aclose()
            message = "synthetic cleanup failure"
            failure = {
                "close_http": httpx2.ReadError(message),
                "close_timeout": TimeoutError(),
            }.get(mode)
            if failure is not None and any(path.endswith("/messages") for path in paths):
                raise failure

    def client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=FailureOnClose(response), base_url="http://127.0.0.1:8019"
        )

    async def admit(session: str, expected: DemoEvaluationCase) -> None:
        assert (session, expected) == ("session", case)

    async def collect() -> None:
        sessions = DemoActorSessions(journal)
        async with client() as preflight:
            _ = await sessions.authenticate(preflight, case.principal_login)
        result = await DemoCollector(plan, identity, sessions, client, admit).collect(
            unit, UUID(int=7000)
        )
        sent = any(path.endswith("/messages") for path in paths)
        assert sent == (
            mode
            in {
                "timeout",
                "neutral_failure",
                "failure_identity_drift",
                "success_identity_drift",
                "close_http",
                "close_timeout",
                "success",
            }
        )
        expected = {
            "test": ("error", "runtime_not_real_ready"),
            "wrong_profile": ("error", "runtime_identity_drift"),
            "timeout": ("timeout", "timeout"),
            "neutral_failure": ("error", "closed_generation_failure"),
            "failure_identity_drift": ("error", "runtime_identity_drift"),
            "success_identity_drift": ("error", "runtime_identity_drift"),
            "close_http": ("error", "transport_or_response_failed"),
            "close_timeout": ("timeout", "timeout"),
            "success": ("success", None),
        }
        assert (result.status, result.error_code) == expected[mode]
        assert (
            result.phase_timings,
            result.authentication_state,
            result.authentication_elapsed_ms,
        ) == (
            None,
            "reused",
            None,
        )
        assert sum(path.endswith("/messages") for path in paths) == int(sent)
        observed = sent and mode != "timeout"
        assert result.retrieved_refs == (
            (
                DemoSourceRef(
                    document_id=UUID(int=21), version_id=UUID(int=21), chunk_id=UUID(int=21)
                ),
            )
            if observed
            else None
        )
        assert (result.chat_response_elapsed_ms is not None) == observed
        assert result.retrieval_observation == (
            "separate_actor_search" if observed else "unavailable"
        )
        assert (result.answer, result.released_refs) == (
            ("Нет доступных источников для ответа.", ()) if mode == "success" else (None, None)
        )
        for marker in ("synthetic-private-search-text", "synthetic-sensitive-answer"):
            assert marker not in result.model_dump_json()

    anyio.run(collect)
