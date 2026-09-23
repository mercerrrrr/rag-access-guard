import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.documents import DocumentSummary


@pytest.mark.parametrize("route", ["create", "version"])
@pytest.mark.parametrize(
    "filename",
    [
        r"C:\private\test.txt",
        r"\\server\share\test.txt",
        r"\\\\server\\share\\test.txt",
        r'safe.txt"; filename="C:\private\test.txt',
    ],
)
def test_original_multipart_filename_cannot_be_a_windows_path(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
    route: str,
    filename: str,
) -> None:
    prefix = (
        b'--synthetic\r\nContent-Disposition: form-data; name="title"\r\n\r\nSynthetic\r\n'
        if route == "create"
        else b""
    )
    body = (
        prefix
        + (
            f'--synthetic\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: text/plain\r\n\r\ntext\r\n--synthetic--\r\n"
        ).encode()
    )
    path = "/api/admin/documents"
    if route == "version":
        path += f"/{registered_document.id}/versions"
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        path,
        content=body,
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
            "Content-Type": "multipart/form-data; boundary=synthetic",
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid document"}
    assert response.headers["cache-control"] == "private, no-store"
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
