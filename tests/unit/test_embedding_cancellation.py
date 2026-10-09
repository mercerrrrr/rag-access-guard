from pathlib import Path
from threading import Event, Thread

import anyio
import pytest
from anyio.to_thread import run_sync

from rag_access_guard_api.adapters.embeddings import E5EmbeddingAdapter


@pytest.mark.anyio
async def test_embedding_waiter_can_cancel_before_native_work_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release, cancelled, returned = Event(), Event(), Event(), Event()
    timely: list[bool] = []

    def encode(
        _self: E5EmbeddingAdapter, _texts: tuple[str, ...], _kind: str
    ) -> tuple[tuple[float, ...], ...]:
        entered.set()
        _ = release.wait()
        return ((1.0,),)

    def observe() -> None:
        assert cancelled.wait(2)
        timely.append(returned.wait(0.2))
        release.set()

    monkeypatch.setattr(E5EmbeddingAdapter, "_encode", encode)
    observer = Thread(target=observe)
    observer.start()
    try:
        with anyio.CancelScope() as scope:
            async with anyio.create_task_group() as group:

                async def cancel() -> None:
                    assert await run_sync(entered.wait, 2)
                    cancelled.set()
                    scope.cancel()

                _ = group.start_soon(cancel)
                _ = await E5EmbeddingAdapter(Path("unused")).embed_query("query")
        returned.set()
        await run_sync(observer.join, 2)
        assert timely == [True]
    finally:
        release.set()
        observer.join(2)
