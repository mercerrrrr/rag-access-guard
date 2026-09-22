from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text, update

from rag_access_guard_api.persistence import AuthChallenge, AuthRateBucket, User
from rag_access_guard_api.schemas.auth import CsrfResponse


def test_concurrent_login_attempts_cannot_exceed_atomic_bucket(
    auth_client: TestClient, auth_database: Engine
) -> None:
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token

    def attempt(_: int) -> int:
        return auth_client.post(
            "/api/auth/login",
            json={"login": "reader", "password": "wrong-password"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(attempt, range(12)))
    assert statuses.count(429) == 7
    assert statuses.count(401) == 1
    assert statuses.count(403) == 4
    with auth_database.connect() as connection:
        assert (
            connection.execute(
                select(AuthRateBucket.attempts).where(AuthRateBucket.kind == "login_account_ip")
            ).scalar_one()
            == 5
        )


@pytest.mark.parametrize("login", ["reader", "missing"])
def test_rate_limits_do_not_disclose_login_existence(auth_client: TestClient, login: str) -> None:
    for attempt in range(6):
        csrf = CsrfResponse.model_validate_json(
            auth_client.get("/api/auth/csrf").content
        ).csrf_token
        response = auth_client.post(
            "/api/auth/login",
            json={"login": login, "password": "wrong-password"},
            headers={
                "Origin": "https://rag.test",
                "X-CSRF-Token": csrf,
                "X-Forwarded-For": f"203.0.113.{attempt}",
            },
        )
        assert response.status_code == (401 if attempt < 5 else 429)
        if attempt == 5:
            assert response.json() == {"detail": "Too many requests"}
            assert 1 <= int(response.headers["retry-after"]) <= 600


def test_untrusted_forwarded_for_cannot_bypass_limits(auth_client: TestClient) -> None:
    for index in range(21):
        response = auth_client.get(
            "/api/auth/csrf", headers={"X-Forwarded-For": f"203.0.113.{index}"}
        )
        assert response.status_code == (200 if index < 20 else 429)


def test_bootstrap_has_per_ip_and_global_challenge_caps(
    auth_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(
            text("""INSERT INTO auth_challenges
        (id, token_digest, csrf_token_digest, created_at, expires_at)
        SELECT gen_random_uuid(), sha256(n::text::bytea), sha256(('csrf-'||n)::bytea),
               now(), now()+interval '10 minutes' FROM generate_series(1,1000) n""")
        )
    response = auth_client.get("/api/auth/csrf")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"
    with auth_database.connect() as connection:
        assert (
            connection.execute(select(func.count()).select_from(AuthChallenge)).scalar_one() == 1000
        )


def test_expired_challenge_cleanup_is_bounded(
    auth_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(
            text("""INSERT INTO auth_challenges
        (id, token_digest, csrf_token_digest, created_at, expires_at)
        SELECT gen_random_uuid(), sha256(n::text::bytea), sha256(('csrf-'||n)::bytea),
               now()-interval '20 minutes', now()-interval '10 minutes'
        FROM generate_series(1,150) n""")
        )
    assert auth_client.get("/api/auth/csrf").status_code == 200
    with auth_database.connect() as connection:
        assert (
            connection.execute(select(func.count()).select_from(AuthChallenge)).scalar_one() == 51
        )


def test_unknown_and_inactive_login_match_bad_password(
    auth_client: TestClient, auth_database: Engine
) -> None:
    payloads = [
        ("reader", "wrong-password"),
        ("unknown", "Synthetic-Pass-123"),
        ("reader", "Synthetic-Pass-123"),
    ]
    for index, (login, password) in enumerate(payloads):
        if index == 2:
            with auth_database.begin() as connection:
                _ = connection.execute(update(User).values(is_active=False))
        csrf = CsrfResponse.model_validate_json(
            auth_client.get("/api/auth/csrf").content
        ).csrf_token
        response = auth_client.post(
            "/api/auth/login",
            json={"login": login, "password": password},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid credentials"}


def test_login_ip_bucket_bounds_distinct_accounts(auth_client: TestClient) -> None:
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    for index in range(21):
        response = auth_client.post(
            "/api/auth/login",
            json={"login": f"missing-{index}", "password": "wrong-password"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        )
        expected = 401 if index == 0 else (429 if index == 20 else 403)
        assert response.status_code == expected


def test_expired_bucket_cleanup_is_bounded(auth_client: TestClient, auth_database: Engine) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(
            text("""INSERT INTO auth_rate_buckets
        (kind, key_digest, window_start, attempts)
        SELECT 'synthetic', sha256(n::text::bytea), now()-interval '30 minutes', 1
        FROM generate_series(1,150) n""")
        )
    assert auth_client.get("/api/auth/csrf").status_code == 200
    with auth_database.connect() as connection:
        assert (
            connection.execute(select(func.count()).select_from(AuthRateBucket)).scalar_one() == 51
        )
