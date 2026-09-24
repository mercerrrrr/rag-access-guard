"""One pgvector distance expression for calibration and retrieval."""

from typing import Final

from pgvector.sqlalchemy import Vector
from pydantic import TypeAdapter
from sqlalchemy import Float, Integer, String, bindparam, cast, column, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.sql.elements import ColumnElement

from rag_access_guard_api.config import DATABASE_TIMEOUT_SECONDS
from rag_access_guard_api.database import EXPECTED_PGVECTOR_VERSION, EXPECTED_POSTGRESQL_MAJOR
from rag_access_guard_api.schemas.search import SearchError

SIMILARITY_REVISION: Final = "pgvector-0.8.6-vector-cosine-v1"


def cosine_distance(
    left: ColumnElement[list[float]], right: ColumnElement[list[float]]
) -> ColumnElement[float]:
    """Keep vector storage precision and the distance operator identical."""
    return cast(left, Vector(384)).op("<=>", return_type=Float())(cast(right, Vector(384)))


async def calibration_scores(
    database_url: str,
    passages: tuple[tuple[float, ...], ...],
    queries: tuple[tuple[float, ...], ...],
) -> tuple[tuple[float, ...], ...]:
    """Evaluate supplied vectors only, without reading or writing application tables."""
    engine = create_async_engine(
        database_url,
        hide_parameters=True,
        connect_args={"connect_timeout": DATABASE_TIMEOUT_SECONDS},
    )
    distance = cosine_distance(
        bindparam("passage", type_=Vector(384)), bindparam("query", type_=Vector(384))
    )
    try:
        async with engine.connect() as connection:
            version, major = TypeAdapter(tuple[str, int]).validate_python(
                (
                    await connection.execute(
                        text(
                            """SELECT extversion,
                        current_setting('server_version_num')::int / 10000 AS major
                        FROM pg_catalog.pg_extension WHERE extname = 'vector'"""
                        ).columns(column("extversion", String()), column("major", Integer()))
                    )
                )
                .tuples()
                .one()
            )
            if version != EXPECTED_PGVECTOR_VERSION or major != EXPECTED_POSTGRESQL_MAJOR:
                raise SearchError
            rows: list[tuple[float, ...]] = []
            for query in queries:
                scores: list[float] = []
                for passage in passages:
                    result = await connection.execute(
                        select(1.0 - distance),
                        {"passage": list(passage), "query": list(query)},
                    )
                    scores.append(result.scalar_one())
                rows.append(tuple(scores))
            return tuple(rows)
    finally:
        await engine.dispose()
