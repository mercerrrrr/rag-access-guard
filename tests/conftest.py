import os
import secrets
from typing import Final

import pytest

_TEST_LIMIT_SECRET: Final = secrets.token_hex(32)


def pytest_configure() -> None:
    os.environ["RAG_ACCESS_GUARD_AUTH_ORIGIN"] = "https://rag.test"
    os.environ["RAG_ACCESS_GUARD_AUTH_LIMIT_SECRET"] = _TEST_LIMIT_SECRET
    os.environ["RAG_ACCESS_GUARD_LOOPBACK_DEVELOPMENT"] = "false"


pytest_configure()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
