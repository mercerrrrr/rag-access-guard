from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from uuid import UUID, uuid4

from anyio.to_thread import run_sync
from sqlalchemy import select
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase

from rag_access_guard_api.persistence import ChatTurn
from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse


class RetryModel:
    def __init__(self) -> None:
        self.inputs: list[tuple[str, str]] = []
        self.entered: tuple[Event, Event] = (Event(), Event())
        self.resume: tuple[Event, Event] = (Event(), Event())
        self.answers: tuple[str, str] = ("RETRY_DISCARDED_0", "RETRY_FINAL_1")

    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        index = len(self.inputs)
        self.inputs.append((user_input, system_supplied_context))
        self.entered[index].set()
        if not await run_sync(self.resume[index].wait, 15):
            raise TimeoutError
        return self.answers[index]


@dataclass(frozen=True, slots=True)
class RetryCase:
    chat: ChatCase
    model: RetryModel
    grants: tuple[tuple[UUID, UUID], ...]

    def run_with_revocations(self, count: int) -> MessageResponse:
        request = MessageRequest(
            request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION"
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.chat.send, request)
            try:
                for index in range(count):
                    if not self.model.entered[index].wait(5):
                        break
                    document, grant = self.grants[index]
                    response = self.chat.client.delete(
                        f"/api/admin/documents/{document}/grants/{grant}",
                        headers=ChatHttp.csrf(self.chat.client),
                    )
                    assert response.status_code == 204
                    self.model.resume[index].set()
            finally:
                for event in self.model.resume:
                    event.set()
            return pending.result(10)

    def stored_answers(self) -> tuple[str, ...]:
        with self.chat.database.connect() as connection:
            return tuple(
                answer
                for answer in connection.execute(select(ChatTurn.answer)).scalars()
                if answer is not None
            )
