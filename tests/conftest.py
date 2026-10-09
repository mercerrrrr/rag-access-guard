import os
import secrets
from collections.abc import Generator
from typing import Final

import anyio
import pytest

from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime, bind_runtime

_TEST_LIMIT_SECRET: Final = secrets.token_hex(32)
pytest_plugins = (
    "tests.support.chat_generation",
    "tests.support.security_races",
    "tests.support.stored_chat",
    "tests.support.downloads",
)


def pytest_configure() -> None:
    os.environ["RAG_ACCESS_GUARD_AUTH_ORIGIN"] = "https://rag.test"
    os.environ["RAG_ACCESS_GUARD_AUTH_LIMIT_SECRET"] = _TEST_LIMIT_SECRET
    os.environ["RAG_ACCESS_GUARD_LOOPBACK_DEVELOPMENT"] = "false"


pytest_configure()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def standalone_inference_scope() -> Generator[None]:
    runtime = InferenceRuntime()
    with bind_runtime(runtime):
        yield
    anyio.run(runtime.aclose)
