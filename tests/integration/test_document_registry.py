import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text


def test_registry_create_does_not_create_document_grant(
    admin_client: TestClient,
    auth_database: Engine,
) -> None:
    # Given: administrative privileges do not grant access to content.
    csrf = admin_client.cookies["__Host-rag_csrf"]
    # When: a text document is registered through the authenticated protocol.
    response = admin_client.post(
        "/api/admin/documents",
        data={"title": "Synthetic"},
        files={"file": ("example.txt", b"PROTECTED_SYNTHETIC", "text/plain")},
        headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
    )
    # Then: only metadata is returned, without a grant side effect.
    assert response.status_code == 201
    assert "PROTECTED_SYNTHETIC" not in response.text
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM document_grants")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM documents")).scalar_one() == 1


@pytest.mark.parametrize(
    ("body", "filename", "media_type", "status"),
    [
        (b"\xff", "example.txt", "text/plain", 422),
        (b"a\x00b", "example.txt", "text/plain", 422),
        (b"", "example.txt", "text/plain", 422),
        (b" \r\n\t", "example.txt", "text/plain", 422),
        (b"a" * 1048577, "example.txt", "text/plain", 413),
        (b"content", "example.md", "text/plain", 415),
        (b"content", "example.txt", "application/pdf", 415),
    ],
    ids=["invalid-utf8", "nul", "empty", "blank", "oversize", "extension", "mime"],
)
def test_invalid_upload_is_rejected(
    admin_client: TestClient,
    body: bytes,
    filename: str,
    media_type: str,
    status: int,
) -> None:
    response = admin_client.post(
        "/api/admin/documents",
        data={"title": "Synthetic"},
        files={"file": (filename, body, media_type)},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == status
    assert response.headers["cache-control"] == "private, no-store"


def test_utf8_bom_and_newlines_have_canonical_text(
    admin_client: TestClient,
    auth_database: Engine,
) -> None:
    raw = b"\xef\xbb\xbfalpha\r\nbeta\rgamma\n"
    response = admin_client.post(
        "/api/admin/documents",
        data={"title": "  Synthetic  "},
        files={"file": ("../../example.TXT", raw, "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 201
    with auth_database.connect() as connection:
        assert connection.execute(
            text("SELECT original_bytes, extracted_text FROM document_versions")
        ).one() == (raw, "alpha\nbeta\ngamma\n")
        assert connection.execute(text("SELECT title FROM documents")).scalar_one() == "Synthetic"


def test_exact_file_limit_is_accepted(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/admin/documents",
        data={"title": "Synthetic"},
        files={"file": ("example.txt", b"a" * 1_048_576, "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "title", ["", " \t", "a" * 513, "a\x00b"], ids=["empty", "blank", "long", "nul"]
)
def test_invalid_title_is_rejected_without_writes(
    admin_client: TestClient, auth_database: Engine, title: str
) -> None:
    response = admin_client.post(
        "/api/admin/documents",
        data={"title": title},
        files={"file": ("example.txt", b"text", "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0
