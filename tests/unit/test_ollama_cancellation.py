import anyio
import httpx2
import pytest

from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime
from rag_access_guard_api.adapters.ollama import OllamaAdapter


@pytest.mark.anyio
async def test_outer_cancellation_quarantines_external_upstream() -> None:
    entered = anyio.Event()
    runtime = InferenceRuntime()

    async def respond(request: httpx2.Request) -> httpx2.Response:
        assert isinstance(request.stream, httpx2.AsyncByteStream)
        async for _chunk in request.stream:
            pass
        entered.set()
        await anyio.sleep_forever()
        raise AssertionError

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        adapter = OllamaAdapter(client, runtime=runtime)
        async with anyio.create_task_group() as group:

            async def generate() -> None:
                _ = await adapter.generate(user_input="Q", system_supplied_context="C")

            _ = group.start_soon(generate)
            await entered.wait()
            group.cancel_scope.cancel()
        assert runtime.snapshot().generation == "unavailable"
    await runtime.aclose()
