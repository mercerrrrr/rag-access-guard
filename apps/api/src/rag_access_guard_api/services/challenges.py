"""Bounded pre-auth issuance and one-use transactional CSRF consumption."""

from datetime import timedelta
from typing import Final
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_access_guard_api.persistence import AuthChallenge
from rag_access_guard_api.services.errors import ForbiddenError, RateLimitedError
from rag_access_guard_api.services.security import database_clock
from rag_access_guard_api.services.tokens import issue_token, matches_token, token_digest

CHALLENGE_CAP: Final = 1000


async def create_challenge(engine: AsyncEngine) -> tuple[str, str]:
    """Serialize the global challenge cap while expiring a bounded batch."""
    preauth, csrf = issue_token(), issue_token()
    async with engine.begin() as connection:
        _ = await connection.execute(text("SELECT pg_advisory_xact_lock(120012, 1)"))
        now = await database_clock(connection)
        expired = select(AuthChallenge.id).where(AuthChallenge.expires_at <= now).limit(100)
        _ = await connection.execute(delete(AuthChallenge).where(AuthChallenge.id.in_(expired)))
        count = (
            await connection.execute(
                select(func.count())
                .select_from(AuthChallenge)
                .where(AuthChallenge.expires_at > now)
            )
        ).scalar_one()
        if count >= CHALLENGE_CAP:
            raise RateLimitedError(600)
        _ = await connection.execute(
            insert(AuthChallenge).values(
                id=uuid4(),
                token_digest=token_digest(preauth),
                csrf_token_digest=token_digest(csrf),
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            )
        )
    return preauth, csrf


async def consume_challenge(engine: AsyncEngine, preauth: str, csrf: str) -> None:
    """Commit consumption before password verification, including failed login."""
    digest = token_digest(preauth)
    if digest is None or token_digest(csrf) is None:
        raise ForbiddenError
    async with engine.begin() as connection:
        challenge = (
            (
                await connection.execute(
                    select(
                        AuthChallenge.id,
                        AuthChallenge.token_digest,
                        AuthChallenge.csrf_token_digest,
                        AuthChallenge.expires_at,
                        AuthChallenge.consumed_at,
                    )
                    .where(AuthChallenge.token_digest == digest)
                    .with_for_update()
                )
            )
            .tuples()
            .one_or_none()
        )
        now = await database_clock(connection)
        if (
            challenge is None
            or challenge[4] is not None
            or now >= challenge[3]
            or not matches_token(preauth, challenge[1])
            or not matches_token(csrf, challenge[2])
        ):
            raise ForbiddenError
        _ = await connection.execute(
            update(AuthChallenge).where(AuthChallenge.id == challenge[0]).values(consumed_at=now)
        )
