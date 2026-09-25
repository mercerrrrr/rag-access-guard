from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import Connection, Engine, text


@dataclass(frozen=True, slots=True)
class ChatHttp:
    owner: TestClient
    other: TestClient
    database: Engine

    @staticmethod
    def csrf(client: TestClient) -> Mapping[str, str]:
        return {"Origin": "https://rag.test", "X-CSRF-Token": client.cookies["__Host-rag_csrf"]}


def seed_thread(connection: Connection) -> UUID:
    return TypeAdapter(UUID).validate_python(
        connection.execute(
            text("""INSERT INTO chat_threads (id, owner_user_id)
        SELECT gen_random_uuid(), id FROM users WHERE login='reader' RETURNING id""")
        ).scalar_one()
    )


def seed_pending(connection: Connection, thread_id: UUID) -> UUID:
    return TypeAdapter(UUID).validate_python(
        connection.execute(
            text("""INSERT INTO chat_turns
        (id,thread_id,request_id,request_sha256,expected_thread_revision,user_input,lease_expires_at)
        VALUES(gen_random_uuid(),:thread,gen_random_uuid(),decode(repeat('ab',32),'hex'),
        0,'Synthetic question',clock_timestamp()+interval '180 seconds') RETURNING id"""),
            {"thread": thread_id},
        ).scalar_one()
    )
