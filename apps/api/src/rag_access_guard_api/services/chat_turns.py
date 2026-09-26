"""Short, thread-locked reservation and completion writes."""

from dataclasses import dataclass
from datetime import timedelta
from typing import assert_never
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update

from rag_access_guard import SourceRef
from rag_access_guard_api.persistence import ChatThread, ChatTurn, TurnSource
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services.audit import AuditRecord, write_audit
from rag_access_guard_api.services.chat_state import NeutralReason, StoredTurn, request_hash
from rag_access_guard_api.services.security import ReadUoW, database_clock


@dataclass(frozen=True, slots=True)
class ReleasedAnswer:
    """Only a successful in-transaction release gate constructs this value."""

    body: str
    source_refs: tuple[SourceRef, ...]


async def load_turns(uow: ReadUoW, thread_id: UUID) -> tuple[StoredTurn, ...]:
    """Read metadata, not protected answer text, in deterministic turn order."""
    rows = (
        await uow.connection.execute(
            select(
                ChatTurn.id,
                ChatTurn.thread_id,
                ChatTurn.request_id,
                ChatTurn.request_sha256,
                ChatTurn.expected_thread_revision,
                ChatTurn.user_input,
                ChatTurn.state,
                ChatTurn.provenance_complete,
                ChatTurn.neutral_reason,
                ChatTurn.lease_expires_at,
            )
            .where(ChatTurn.thread_id == thread_id)
            .order_by(ChatTurn.created_at, ChatTurn.id)
        )
    ).mappings()
    return tuple(StoredTurn.model_validate(row) for row in rows)


async def insert_pending(uow: ReadUoW, thread_id: UUID, request: MessageRequest) -> None:
    """Reserve one request under its thread lock with a server-time crash lease."""
    now = await database_clock(uow.connection)
    _ = await uow.connection.execute(
        insert(ChatTurn).values(
            id=uuid4(),
            thread_id=thread_id,
            request_id=request.request_id,
            request_sha256=request_hash(request),
            expected_thread_revision=request.expected_thread_revision,
            user_input=request.user_input,
            lease_expires_at=now + timedelta(seconds=180),
        )
    )
    if request.expected_thread_revision == 0:
        _ = await uow.connection.execute(
            update(ChatThread)
            .where(ChatThread.id == thread_id)
            .values(
                title=" ".join(request.user_input.split())[:120],
            )
        )


async def complete_turn(
    uow: ReadUoW,
    turn: StoredTurn,
    result: ReleasedAnswer | NeutralReason,
) -> int:
    """Atomically finalize the pair, its source closure, revision and content-free audit."""
    now = await database_clock(uow.connection)
    revision = (
        await uow.connection.execute(
            update(ChatThread)
            .where(
                ChatThread.id == turn.thread_id,
            )
            .values(revision=ChatThread.revision + 1)
            .returning(ChatThread.revision)
        )
    ).scalar_one()
    match result:
        case ReleasedAnswer(body=body, source_refs=refs):
            _ = await uow.connection.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn.id)
                .values(
                    state="available",
                    answer=body,
                    provenance_complete=True,
                    completed_at=now,
                    ordinal=revision,
                    lease_expires_at=None,
                )
            )
            for position, ref in enumerate(dict.fromkeys(refs)):
                _ = await uow.connection.execute(
                    insert(TurnSource).values(
                        turn_id=turn.id,
                        document_id=ref.document_id,
                        document_version_id=ref.document_version_id,
                        chunk_id=ref.chunk_id,
                        position=position,
                    )
                )
            count = len(set(refs))
            outcome = "allowed"
        case "no_context" | "generation_unavailable" | "policy_changed" | "interrupted":
            _ = await uow.connection.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn.id)
                .values(
                    state="neutral",
                    answer=None,
                    provenance_complete=False,
                    server_generated_neutral=True,
                    neutral_reason=result,
                    completed_at=now,
                    ordinal=revision,
                    lease_expires_at=None,
                )
            )
            count = 0
            outcome = "denied"
        case _:
            assert_never(result)
    await write_audit(
        uow.connection,
        AuditRecord(
            event_type="access_checked",
            stage="release",
            outcome=outcome,
            actor_user_id=uow.principal.principal_id,
            principal_id=uow.principal.principal_id,
            source_count=count,
        ),
        uow.revision,
    )
    return revision
