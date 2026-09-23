from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from anyio.to_thread import run_sync
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from starlette.datastructures import UploadFile

from rag_access_guard_api.persistence import PolicyState


@pytest.mark.parametrize(
    "case",
    [
        ("UPDATE sessions SET revoked_at=clock_timestamp()", 401),
        ("UPDATE users SET is_admin=false", 403),
    ],
)
def test_upload_rechecks_security_after_multipart_read(
    admin_client: TestClient,
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, int],
) -> None:
    change, expected = case
    entered, release = Event(), Event()
    original = UploadFile.read

    async def paused(upload: UploadFile, size: int = -1) -> bytes:
        entered.set()
        assert await run_sync(release.wait, 10)
        return await original(upload, size)

    monkeypatch.setattr(UploadFile, "read", paused)
    with auth_database.connect() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            admin_client.post,
            "/api/admin/documents",
            data={"title": "Synthetic"},
            files={"file": ("example.txt", b"PROTECTED_SYNTHETIC", "text/plain")},
            headers={
                "Origin": "https://rag.test",
                "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
            },
        )
        try:
            assert entered.wait(10)
            with auth_database.begin() as writer:
                _ = writer.execute(text("SELECT revision FROM policy_state FOR UPDATE"))
                _ = writer.execute(text(change))
                _ = writer.execute(text("UPDATE policy_state SET revision=revision+1"))
        finally:
            release.set()
        response = future.result(10)
    assert response.status_code == expected
    assert "PROTECTED_SYNTHETIC" not in response.text
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 0
        assert connection.execute(select(PolicyState.revision)).scalar_one() == before + 1
