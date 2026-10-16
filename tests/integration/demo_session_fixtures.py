from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx2
from experiments.demo_inputs import DemoInputs
from experiments.demo_run_contracts import DemoRunPlan, make_unit
from experiments.run_config import synthetic_retrieval
from experiments.run_environment import GitState
from fastapi import FastAPI
from pydantic import SecretStr

from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker, DemoRuntime
from rag_access_guard_api.demo.credentials import (
    CredentialEntry,
    CredentialJournal,
    encoded_journal,
)
from rag_access_guard_api.demo.private_write import exclusive_private_write, private_directory
from tests.unit.demo_evaluation_fixtures import dataset


@dataclass(frozen=True, slots=True)
class SyntheticQueryCounter:
    def embedding_input_tokens(self, text: str, *, kind: Literal["query", "passage"]) -> int:
        del kind
        return len(text.split()) + 2


def session_inputs(root: Path, database_url: str) -> DemoInputs:
    marker = DemoMarker(
        instance_id=UUID(int=9000),
        dataset_id="synthetic-test",
        database_name="synthetic-test",
        database_role="synthetic-role",
    )
    config = DemoConfig(
        dataset_id=marker.dataset_id,
        database_url=SecretStr(database_url),
        corpus_manifest=root / "corpus.json",
        ownership_manifest=root / "owner.json",
        credentials_path=root / "private" / "credentials.json",
        access_definition=root / "access.json",
        model_profile="qwen3-instruct-demo-v1",
        retrieval_config=root / "retrieval.json",
        runtime=DemoRuntime(
            repository_root=Path(__file__).resolve().parents[2],
            state_directory=root / "state",
            log_directory=root / "logs",
            ollama_supervisor_config=root / "unused.json",
            api_port=8019,
            web_port=5179,
        ),
    )
    journal = CredentialJournal(
        marker=marker,
        entries=tuple(
            CredentialEntry(login=login, password=SecretStr("synthetic-password"))
            for login in ("demo.student", "demo.teacher", "demo.staff")
        ),
    )
    _ = private_directory(config.credentials_path.parent)
    exclusive_private_write(config.credentials_path, encoded_journal(journal))
    return DemoInputs(
        config=config,
        dataset=dataset().model_copy(update={"policy_revision": 0}),
        marker=marker,
        retrieval=synthetic_retrieval("a" * 64),
        hashes=(),
        code=GitState("a" * 40, dirty=False),
        repository=root,
    )


def session_plan(inputs: DemoInputs, mode: Literal["calibrate", "holdout"]) -> DemoRunPlan:
    split = "calibration" if mode == "calibrate" else "holdout"
    cases = tuple(
        case
        for case in inputs.dataset.cases
        if case.split == split and case.principal_login == "demo.student"
    )[:6]
    return DemoRunPlan(
        output=inputs.repository / "run",
        git_sha="a" * 40,
        config_sha256="c" * 64,
        dataset_sha256=inputs.dataset.dataset_sha256,
        profile_hash=inputs.dataset.profile_hash,
        model_digest="e" * 64,
        renderer_identity="renderer",
        corpus_sha256=inputs.dataset.corpus_sha256,
        retrieval_config_hash=inputs.retrieval.config_sha256,
        mode=mode,
        schedule=tuple(
            make_unit(case).model_copy(update={"request_id": None})
            if mode == "calibrate"
            else make_unit(case)
            for case in cases
        ),
    )


def session_clients(
    app: FastAPI, login_statuses: list[int]
) -> Callable[[DemoConfig], httpx2.AsyncClient]:
    async def observed(response: httpx2.Response) -> None:
        if response.request.url.path == "/api/auth/login":
            login_statuses.append(response.status_code)

    def client(_config: DemoConfig) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url="http://127.0.0.1:8019",
            headers={"Origin": "http://127.0.0.1:5179"},
            transport=httpx2.ASGITransport(app=app),
            event_hooks={"response": [observed]},
        )

    return client
