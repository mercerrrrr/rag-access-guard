from typing import Literal, assert_never
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import text
from tests.support.stored_chat import StoredChatCase

from rag_access_guard import SourceRef
from rag_access_guard_api.services.source_closure import closure_digest

type ProvenanceFault = Literal["incomplete", "empty", "partial", "witness", "orphan", "mixed"]


def corrupt_sources(fixture: StoredChatCase, fault: ProvenanceFault) -> None:
    target = fixture.answers[1].turn.id
    with fixture.case.database.begin() as connection:
        parameters = {"turn": target, "document": fixture.case.document.id}
        match fault:
            case "incomplete":
                _ = connection.execute(
                    text("ALTER TABLE chat_turns DROP CONSTRAINT ck_chat_turns_state")
                )
                _ = connection.execute(
                    text("UPDATE chat_turns SET provenance_complete=false WHERE id=:turn"),
                    parameters,
                )
            case "empty":
                _ = connection.execute(text("SET LOCAL session_replication_role = replica"))
                _ = connection.execute(
                    text("DELETE FROM turn_sources WHERE turn_id=:turn"), parameters
                )
            case "partial":
                _ = connection.execute(
                    text("DELETE FROM turn_sources WHERE turn_id=:turn AND document_id=:document"),
                    parameters,
                )
            case "witness":
                _ = connection.execute(
                    text("UPDATE chat_turns SET source_closure_sha256=NULL WHERE id=:turn"),
                    parameters,
                )
            case "orphan" | "mixed":
                _ = connection.execute(text("SET LOCAL session_replication_role = replica"))
                statement = (
                    """UPDATE turn_sources SET chunk_id=:replacement
                    WHERE turn_id=:turn AND document_id=:document"""
                    if fault == "orphan"
                    else """UPDATE turn_sources SET document_id=:replacement
                    WHERE turn_id=:turn AND document_id=:document"""
                )
                replacement = uuid4() if fault == "orphan" else fixture.independent_document.id
                _ = connection.execute(text(statement), parameters | {"replacement": replacement})
                refs = tuple(
                    SourceRef(document_id=d, document_version_id=v, chunk_id=c)
                    for d, v, c in TypeAdapter(tuple[tuple[UUID, UUID, UUID], ...]).validate_python(
                        connection.execute(
                            text(
                                """SELECT document_id, document_version_id, chunk_id
                                FROM turn_sources WHERE turn_id=:turn"""
                            ),
                            parameters,
                        )
                        .tuples()
                        .all()
                    )
                )
                _ = connection.execute(
                    text("UPDATE chat_turns SET source_closure_sha256=:digest WHERE id=:turn"),
                    {"turn": target, "digest": closure_digest(refs)},
                )
            case _:
                assert_never(fault)
