from dataclasses import dataclass, field
from threading import Event

from pydantic import TypeAdapter
from sqlalchemy import Engine, text
from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass(slots=True)
class ReleaseSurface:
    app: ASGIApp
    database: Engine
    committed: Event = field(default_factory=Event)
    bodies: list[bytes] = field(default_factory=list)
    protected_observations: list[tuple[bool, int]] = field(default_factory=list)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def record(message: Message) -> None:
            if message["type"] == "http.response.body":
                body = TypeAdapter(bytes).validate_python(message.get("body", b""))
                self.bodies.append(body)
                if b"SYNTHETIC_ANSWER" in body:
                    with self.database.connect() as observer:
                        count = TypeAdapter(int).validate_python(
                            observer.execute(
                                text("SELECT count(*) FROM chat_turns WHERE answer IS NOT NULL")
                            ).scalar_one()
                        )
                    self.protected_observations.append((self.committed.is_set(), count))
            await send(message)

        await self.app(scope, receive, record)
