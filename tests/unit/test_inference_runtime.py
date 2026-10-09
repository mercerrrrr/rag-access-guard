from threading import Event
from uuid import uuid4

import anyio
import pytest
from anyio.lowlevel import checkpoint
from anyio.to_thread import run_sync
from fastapi.testclient import TestClient

from rag_access_guard_api.adapters.embeddings import E5EmbeddingAdapter, get_embedding_adapter
from rag_access_guard_api.adapters.inference_runtime import (
    InferenceBusyError,
    InferenceRuntime,
    InferenceUnavailableError,
    bind_runtime,
    current_runtime,
)
from rag_access_guard_api.main import create_app
from rag_access_guard_api.services.auth import AuthService


@pytest.mark.anyio
async def test_cancel_before_submission_accepts_no_physical_job() -> None:
    runtime = InferenceRuntime()
    try:
        with anyio.CancelScope() as scope:
            scope.cancel()
            scope.cancel()
            _ = await runtime.run_embedding(lambda: 1)
        assert runtime.accepted_embedding_jobs == 0
        assert runtime.snapshot().embeddings == "ready"
    finally:
        await runtime.aclose()


def test_independent_app_lifespans_bind_distinct_factory_runtimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def initialized(_self: AuthService) -> None:
        return

    monkeypatch.setattr(AuthService, "initialize", initialized)
    outer = current_runtime()
    observed: list[InferenceRuntime] = []

    async def capture() -> dict[str, str]:
        runtime = current_runtime()
        query = get_embedding_adapter()
        upload = E5EmbeddingAdapter(query.path) if isinstance(query, E5EmbeddingAdapter) else None
        assert isinstance(query, E5EmbeddingAdapter)
        assert upload is not None
        assert query.runtime is upload.runtime is runtime
        observed.append(runtime)
        return {"generation": runtime.snapshot().generation}

    first, second = create_app(), create_app()
    first.add_api_route("/_runtime", capture)
    second.add_api_route("/_runtime", capture)
    with TestClient(first) as a, TestClient(second) as b:
        assert a.get("/_runtime").json() == {"generation": "ready"}
        observed[0].set_generation_available(available=False)
        assert a.get("/_runtime").json() == {"generation": "unavailable"}
        assert b.get("/_runtime").json() == {"generation": "ready"}
        assert observed[0] is observed[1]
        assert observed[0] is not observed[2]
        assert observed[0] is not outer
    assert all(runtime.snapshot().generation == "unavailable" for runtime in observed)


def test_missing_app_scope_does_not_borrow_standalone_owner() -> None:
    with bind_runtime(None), pytest.raises(InferenceUnavailableError):
        _ = current_runtime()


@pytest.mark.anyio
async def test_cancelled_waiter_keeps_physical_worker_slot() -> None:
    entered, release, completed = Event(), Event(), Event()
    runtime = InferenceRuntime()

    def work() -> int:
        entered.set()
        _ = release.wait()
        completed.set()
        return 7

    try:
        with anyio.move_on_after(0.05) as deadline:
            _ = await runtime.run_embedding(work)
        assert deadline.cancel_called
        assert entered.is_set()
        assert runtime.snapshot().embeddings == "busy"
        with pytest.raises(InferenceBusyError):
            _ = await runtime.run_embedding(lambda: 2)
        release.set()
        assert await run_sync(completed.wait, 2)
        with anyio.fail_after(2):
            while runtime.snapshot().embeddings == "busy":
                await checkpoint()
        assert await runtime.run_embedding(lambda: 2) == 2
    finally:
        release.set()
        await runtime.aclose()


@pytest.mark.anyio
async def test_worker_error_releases_slot() -> None:
    runtime = InferenceRuntime()

    def fail() -> int:
        raise ArithmeticError

    try:
        with pytest.raises(ArithmeticError):
            _ = await runtime.run_embedding(fail)
        assert await runtime.run_embedding(lambda: 3) == 3
    finally:
        await runtime.aclose()


@pytest.mark.anyio
async def test_generation_has_zero_queue_and_releases_on_exception() -> None:
    runtime = InferenceRuntime()
    principal = uuid4()

    async def contend() -> None:
        async with runtime.try_acquire_generation(principal):
            assert runtime.snapshot().generation == "busy"
            for contender in (principal, uuid4()):
                with pytest.raises(InferenceBusyError):
                    _ = runtime.try_acquire_generation(contender)
            raise ArithmeticError

    try:
        with pytest.raises(ArithmeticError):
            await contend()
        assert runtime.snapshot().generation == "ready"
    finally:
        await runtime.aclose()


@pytest.mark.anyio
async def test_shutdown_does_not_claim_active_native_work_finished() -> None:
    release, entered = Event(), Event()
    runtime = InferenceRuntime()

    def work() -> int:
        entered.set()
        _ = release.wait()
        return 4

    try:
        with anyio.move_on_after(0.05):
            _ = await runtime.run_embedding(work)
        assert entered.is_set()
        await runtime.aclose()
        assert runtime.snapshot().embeddings == "unavailable"
        with pytest.raises(InferenceUnavailableError):
            _ = await runtime.run_embedding(lambda: 5)
        with pytest.raises(InferenceUnavailableError):
            _ = runtime.try_acquire_generation(uuid4())
    finally:
        release.set()
        await runtime.aclose()
