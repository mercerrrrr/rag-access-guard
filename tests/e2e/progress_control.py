from dataclasses import dataclass
from secrets import compare_digest
from typing import Annotated, ClassVar, Protocol
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field


class AnswerModel(Protocol):
    async def generate(self, *, user_input: str, system_supplied_context: str) -> str: ...


@dataclass(frozen=True, slots=True)
class Hold:
    question: str
    entered: anyio.Event
    resume: anyio.Event


class HoldRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    hold_id: UUID
    question: str = Field(min_length=1, max_length=4000)


class ProgressControl:
    """Mutable test fixture: only model execution waits; policy and storage stay real."""

    def __init__(self, model: AnswerModel) -> None:
        self.model: AnswerModel = model
        self.holds: dict[UUID, Hold] = {}

    def arm(self, identifier: UUID, question: str) -> None:
        if identifier in self.holds or any(
            hold.question == question for hold in self.holds.values()
        ):
            raise HTTPException(409, "Hold already exists")
        self.holds[identifier] = Hold(question, anyio.Event(), anyio.Event())

    async def wait_entered(self, identifier: UUID) -> None:
        with anyio.fail_after(15):
            await self._hold(identifier).entered.wait()

    def release(self, identifier: UUID) -> None:
        hold = self.holds.pop(identifier, None)
        if hold is not None:
            hold.resume.set()

    def _hold(self, identifier: UUID) -> Hold:
        hold = self.holds.get(identifier)
        if hold is None:
            raise HTTPException(404, "Hold not found")
        return hold

    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        hold = next((hold for hold in self.holds.values() if hold.question == user_input), None)
        if hold is not None:
            hold.entered.set()
            with anyio.fail_after(120):
                await hold.resume.wait()
        return await self.model.generate(
            user_input=user_input,
            system_supplied_context=system_supplied_context,
        )


def mount_progress_control(application: FastAPI, control: ProgressControl, secret: str) -> None:
    async def authorize(x_e2e_control: Annotated[str | None, Header()] = None) -> None:
        if x_e2e_control is None or not compare_digest(x_e2e_control, secret):
            raise HTTPException(404, "Not found")

    router = APIRouter(
        prefix="/__e2e/progress/holds",
        include_in_schema=False,
        dependencies=[Depends(authorize)],
    )

    @router.post("", status_code=204)
    async def arm(body: HoldRequest) -> Response:
        control.arm(body.hold_id, body.question)
        return Response(status_code=204)

    @router.post("/{identifier}/entered", status_code=204)
    async def entered(identifier: UUID) -> Response:
        await control.wait_entered(identifier)
        return Response(status_code=204)

    @router.post("/{identifier}/release", status_code=204)
    async def release(identifier: UUID) -> Response:
        control.release(identifier)
        return Response(status_code=204)

    application.include_router(router)
