import sys
from functools import partial
from pathlib import Path
from threading import Event
from time import monotonic

import anyio
import pytest
from anyio.to_thread import run_sync
from tests.unit.test_embedding_process import assert_exited, wait_file

from rag_access_guard_api.adapters import e5_process
from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.generation import InferenceBusyError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process containment")


def configure_worker(tmp_path: Path, mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = Path(__file__).parents[1] / "helpers/controlled_relevance.py"
    monkeypatch.setattr(
        e5_process, "worker_command", lambda: (sys.executable, str(helper), str(tmp_path), mode)
    )


@pytest.mark.parametrize(
    "mode",
    ["exit", "invalid-json", "wrong-nonce", "bad-count", "nonfinite", "text-score", "model-prose"],
)
def test_invalid_relevance_response_is_denied_after_worker_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    configure_worker(tmp_path, mode, monkeypatch)
    runtime = InferenceRuntime()
    try:
        with pytest.raises(EmbeddingError):
            _ = e5_process.score_relevance_isolated(
                tmp_path / "config.json", "QUESTION", ("PRIVATE_CANARY",), runtime
            )
        assert_exited(tmp_path)
        assert not runtime.closed
    finally:
        runtime.close()


@pytest.mark.anyio
async def test_cancelled_relevance_waiter_keeps_capacity_until_physical_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_worker(tmp_path, "hold", monkeypatch)
    runtime = InferenceRuntime()

    async def assess() -> None:
        _ = await runtime.run_embedding(
            partial(
                e5_process.score_relevance_isolated,
                tmp_path / "config.json",
                "QUESTION",
                ("PRIVATE_CANARY",),
                runtime,
            )
        )

    try:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(assess)
            await run_sync(wait_file, tmp_path / "root.json")
            tasks.cancel_scope.cancel()
        assert runtime.snapshot().embeddings == "busy"
        with pytest.raises(InferenceBusyError):
            _ = await runtime.run_embedding(lambda: 1)
        (tmp_path / "release").touch()

        def wait_idle() -> None:
            deadline = monotonic() + 10
            while runtime.snapshot().embeddings == "busy":
                if monotonic() >= deadline:
                    raise TimeoutError
                _ = Event().wait(0.02)

        await run_sync(wait_idle)
        assert_exited(tmp_path)
    finally:
        (tmp_path / "release").touch()
        await runtime.aclose()
