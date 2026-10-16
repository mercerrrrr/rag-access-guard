from pathlib import Path
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
from rag_access_guard_api.demo.evaluation_contracts import DemoEvaluationCase
from rag_access_guard_api.schemas.evaluation_identity import EvaluationIdentity


@pytest.mark.parametrize("mode", ["test", "wrong_profile", "timeout", "neutral_failure", "success"])
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
        selected = (
            identity.model_copy(update={"profile_hash": "0" * 64})
            if mode == "wrong_profile"
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
            "/api/search": httpx2.Response(200, json={"items": []}),
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
            return httpx2.Response(
                200,
                json={
                    "thread_revision": 1,
                    "replayed": False,
                    "turn": {
                        "id": str(UUID(int=6000)),
                        "request_id": str(unit.request_id),
                        "user_input": case.question,
                        "state": "neutral",
                        "answer": None,
                        "sources": [],
                        "message": failure
                        if mode == "neutral_failure"
                        else "Нет доступных источников для ответа.",
                    },
                },
            )
        return responses.get(path, httpx2.Response(404))

    def client() -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.MockTransport(response), base_url="http://127.0.0.1:8019"
        )

    async def admit(session: str, expected: DemoEvaluationCase) -> None:
        del expected
        assert session == "session"

    async def collect() -> None:
        sessions = DemoActorSessions(journal)
        async with client() as preflight:
            _ = await sessions.authenticate(preflight, case.principal_login)
        result = await DemoCollector(plan, identity, sessions, client, admit).collect(
            unit, UUID(int=7000)
        )
        sent = any(path.endswith("/messages") for path in paths)
        assert sent == (mode in {"timeout", "neutral_failure", "success"})
        assert result.status == (
            "success" if mode == "success" else "timeout" if mode == "timeout" else "error"
        )
        assert result.phase_timings is None
        assert result.authentication_state == "reused"
        assert result.authentication_elapsed_ms is None
        if mode != "success":
            assert result.answer is result.retrieved_refs is result.released_refs is None
        else:
            assert result.retrieval_observation == "separate_actor_search"
            assert result.chat_response_elapsed_ms is not None

    anyio.run(collect)
