from uuid import uuid4

import anyio
from anyio.lowlevel import checkpoint
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.e2e import progress_control


class Answer:
    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        return f"released:{user_input}:{system_supplied_context}"


def test_generation_is_unresolved_until_its_hold_is_released() -> None:
    async def scenario() -> None:
        # Given a hold for exactly one question at the model boundary.
        control = progress_control.ProgressControl(Answer())
        identifier = uuid4()
        control.arm(identifier, "held question")
        completed: list[str] = []

        async def generate() -> None:
            completed.append(
                await control.generate(
                    user_input="held question",
                    system_supplied_context="protected context",
                )
            )

        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(generate)
            try:
                await control.wait_entered(identifier)
                await checkpoint()
                assert completed == []
            finally:
                # When the test releases the boundary.
                control.release(identifier)
        # Then the original model result is returned.
        assert completed == ["released:held question:protected context"]

    anyio.run(scenario)


def test_controls_require_the_launcher_secret_and_are_hidden_from_openapi() -> None:
    application = FastAPI()
    control = progress_control.ProgressControl(Answer())
    identifier = uuid4()
    progress_control.mount_progress_control(
        application,
        control,
        "test-only-secret",
    )
    with TestClient(application) as client:
        path = "/__e2e/progress/holds"
        payload = {"hold_id": str(identifier), "question": "held question"}
        assert client.post(path, json=payload).status_code == 404
        assert (
            client.post(path, json=payload, headers={"X-E2E-Control": "wrong"}).status_code == 404
        )
        try:
            assert (
                client.post(
                    path,
                    json=payload,
                    headers={"X-E2E-Control": "test-only-secret"},
                ).status_code
                == 204
            )
            assert not application.openapi()["paths"]
        finally:
            control.release(identifier)


def test_unrelated_question_does_not_wait_for_another_hold() -> None:
    async def scenario() -> None:
        control = progress_control.ProgressControl(Answer())
        identifier = uuid4()
        control.arm(identifier, "held question")
        try:
            assert (
                await control.generate(
                    user_input="another question",
                    system_supplied_context="context",
                )
                == "released:another question:context"
            )
        finally:
            control.release(identifier)

    anyio.run(scenario)
