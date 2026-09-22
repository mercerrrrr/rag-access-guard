"""Database-backed, HMAC-keyed fixed-window authentication reservations."""

import hashlib
import hmac
import json
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_access_guard_api.persistence import AuthRateBucket
from rag_access_guard_api.services.errors import RateLimitedError
from rag_access_guard_api.services.security import database_clock


class AuthLimits:
    """Reserve all limits in one short transaction before expensive work."""

    def __init__(self, engine: AsyncEngine, secret: bytes) -> None:
        """Use an operator-provided secret shared by application workers."""
        self._engine: AsyncEngine = engine
        self._secret: bytes = secret

    async def reserve(self, peer: str, login: str | None = None) -> None:
        """Bound bootstrap per peer, or login per peer and normalized account."""
        buckets = (
            (("bootstrap_ip", (peer,), 20),)
            if login is None
            else (("login_ip", (peer,), 20), ("login_account_ip", (peer, login), 5))
        )
        async with self._engine.begin() as connection:
            now = await database_clock(connection)
            window = datetime.fromtimestamp(math.floor(now.timestamp() / 600) * 600, UTC)
            retry = max(1, math.ceil((window + timedelta(minutes=10) - now).total_seconds()))
            _ = await connection.execute(
                text(
                    """DELETE FROM auth_rate_buckets WHERE ctid IN
                    (SELECT ctid FROM auth_rate_buckets WHERE window_start < :cutoff LIMIT 100)"""
                ),
                {"cutoff": now - timedelta(minutes=20)},
            )
            for kind, parts, limit in buckets:
                digest = hmac.new(
                    self._secret, json.dumps((kind, *parts)).encode(), hashlib.sha256
                ).digest()
                statement = (
                    insert(AuthRateBucket)
                    .values(kind=kind, key_digest=digest, window_start=window, attempts=1)
                    .on_conflict_do_update(
                        index_elements=[
                            AuthRateBucket.kind,
                            AuthRateBucket.key_digest,
                            AuthRateBucket.window_start,
                        ],
                        set_={"attempts": AuthRateBucket.attempts + 1},
                        where=AuthRateBucket.attempts < limit,
                    )
                    .returning(AuthRateBucket.attempts)
                )
                if (await connection.execute(statement)).scalar_one_or_none() is None:
                    raise RateLimitedError(retry)
