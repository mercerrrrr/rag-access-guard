import os
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from rag_access_guard_api.adapters import embeddings, llm
from rag_access_guard_api.main import create_app
from rag_access_guard_api.server import EVENT_LOOP_IMPORT
from tests.e2e.seed import disposable_database, seed_users
from tests.integration.embedding_fixtures import DeterministicEmbedder
from tests.integration.search_fixtures import configure_search

ROOT = Path(__file__).resolve().parents[2]


class ContextAnswer:
    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        del user_input
        if "SYNTHETIC_REVOKE_57" in system_supplied_context:
            return "SYNTHETIC_REVOKE_57"
        return "SYNTHETIC_OTHER_57"


def mount_web(application: FastAPI) -> None:
    distribution = ROOT / "apps" / "web" / "dist"
    if not (distribution / "index.html").is_file():
        message = "Build the production frontend before E2E"
        raise RuntimeError(message)
    application.mount("/assets", StaticFiles(directory=distribution / "assets"))

    @application.get("/{path:path}", include_in_schema=False)
    async def web(path: str) -> FileResponse:
        if path not in {
            "",
            "login",
            "chat",
            "documents",
            "access",
            "audit",
        } and not path.startswith("chat/"):
            raise HTTPException(404, "Not found")
        return FileResponse(distribution / "index.html", headers={"Cache-Control": "no-store"})


def main() -> None:
    admin_url = os.environ["RAG_E2E_DATABASE_ADMIN_URL"]
    password = os.environ["RAG_E2E_PASSWORD"]
    with (
        disposable_database(admin_url) as database_url,
        TemporaryDirectory(prefix="rag-e2e-") as temp,
        pytest.MonkeyPatch.context() as environment,
    ):
        environment.setenv("RAG_ACCESS_GUARD_DATABASE_URL", database_url)
        environment.setenv("RAG_ACCESS_GUARD_AUTH_LIMIT_SECRET", secrets.token_hex(32))
        environment.setenv("RAG_ACCESS_GUARD_AUTH_ORIGIN", "http://127.0.0.1:54174")
        environment.setenv("RAG_ACCESS_GUARD_LOOPBACK_DEVELOPMENT", "true")
        environment.setenv("RAG_ACCESS_GUARD_LLM_ADAPTER", "fake")
        configure_search(Path(temp) / "retrieval.json", environment)
        command.upgrade(Config(str(ROOT / "apps" / "api" / "alembic.ini")), "head")
        _ = seed_users(database_url, password)
        with (
            patch.object(embeddings, "get_embedding_adapter", DeterministicEmbedder),
            patch.object(llm, "get_llm_adapter", ContextAnswer),
        ):
            application = create_app()
            mount_web(application)
            uvicorn.run(
                application,
                host="127.0.0.1",
                port=54174,
                loop=EVENT_LOOP_IMPORT,
                proxy_headers=False,
                access_log=False,
            )


if __name__ == "__main__":
    main()
