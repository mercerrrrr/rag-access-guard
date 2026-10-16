"""Actor-visible exact SQL top-five scores for the unchanged threshold scorer."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import Uuid, column, select

from rag_access_guard_api.schemas.embedding_vectors import validate_vectors
from rag_access_guard_api.schemas.search import MAX_RESULTS
from rag_access_guard_api.services.retrieval_query import (
    eligible_candidates,
    production_order,
    query_distance,
)
from rag_access_guard_api.services.security import ReadUoW


@dataclass(frozen=True, slots=True)
class ActorScore:
    """A database-computed score under the same ordering as production retrieval."""

    chunk_id: UUID
    score: float


async def actor_top_five(uow: ReadUoW, vector: tuple[float, ...]) -> tuple[ActorScore, ...]:
    """Select membership before score_thresholds applies its own stable tie ordering."""
    validate_vectors((vector,), 1)
    eligible = eligible_candidates(uow.principal.principal_id)
    distance = query_distance()
    query = (
        select(column("chunk_id", Uuid()), (1.0 - distance).label("score"))
        .select_from(eligible)
        .order_by(*production_order(distance))
        .limit(MAX_RESULTS)
    )
    rows = (await uow.connection.execute(query, {"query": list(vector)})).tuples().all()
    parsed = TypeAdapter(tuple[tuple[UUID, float], ...]).validate_python(tuple(rows))
    return tuple(ActorScore(chunk_id, score) for chunk_id, score in parsed)
