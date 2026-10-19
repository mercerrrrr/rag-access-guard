from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from rag_access_guard import SourceRef
from rag_access_guard_api.demo.private_write import exclusive_private_write, private_directory
from rag_access_guard_api.demo.reports import SeedSuggestion, SeedSuggestions
from rag_access_guard_api.main import create_app
from rag_access_guard_api.persistence import DocumentChunk, DocumentVersion
from rag_access_guard_api.schemas.auth import CsrfResponse, LoginResponse
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.server import create_event_loop

GUIDANCE_TITLE = "Synthetic guidance document"
GUIDANCE_QUESTION = "Как подать учебную заявку?"
ORIGIN = "http://127.0.0.1:54174"


def seed_guidance(database_url: str, actors: dict[str, UUID], password: str, path: Path) -> None:
    with TestClient(
        create_app(), base_url=ORIGIN, backend_options={"loop_factory": create_event_loop}
    ) as client:
        challenge = CsrfResponse.model_validate_json(client.get("/api/auth/csrf").content)
        login = client.post(
            "/api/auth/login",
            json={"login": "admin", "password": password},
            headers={"Origin": ORIGIN, "X-CSRF-Token": challenge.csrf_token},
        )
        assert login.status_code == 200
        token = LoginResponse.model_validate_json(login.content).csrf_token
        headers = {"Origin": ORIGIN, "X-CSRF-Token": token}
        uploaded = client.post(
            "/api/admin/documents",
            headers=headers,
            data={"title": GUIDANCE_TITLE},
            files={
                "file": (
                    "guidance.txt",
                    "Учебный пример. Заявку передают координатору.".encode(),
                    "text/plain",
                )
            },
        )
        assert uploaded.status_code == 201
        document = DocumentSummary.model_validate_json(uploaded.content)
        given = client.post(
            f"/api/admin/documents/{document.id}/grants",
            headers=headers,
            json={"user_id": str(actors["teacher"])},
        )
        assert given.status_code == 201
    engine = create_engine(database_url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            chunks = (
                connection.execute(
                    select(DocumentChunk.id, DocumentChunk.document_version_id)
                    .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
                    .where(DocumentVersion.document_id == document.id)
                )
                .tuples()
                .all()
            )
    finally:
        engine.dispose()
    assert chunks
    bundle = SeedSuggestions(
        instance_id="e2e-guidance",
        dataset_id="synthetic-guidance-v1",
        suggestions=(
            SeedSuggestion(
                id="e2e-guidance",
                question=GUIDANCE_QUESTION,
                required_source_refs=tuple(
                    SourceRef(document_id=document.id, document_version_id=version, chunk_id=chunk)
                    for chunk, version in chunks
                ),
            ),
        ),
    )
    _ = private_directory(path.parent)
    exclusive_private_write(path, bundle.model_dump_json().encode())
