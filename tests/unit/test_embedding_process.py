import json
import subprocess
import sys
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

import anyio
import pytest
from anyio.to_thread import run_sync
from pydantic import ValidationError

from rag_access_guard_api.adapters import e5_process
from rag_access_guard_api.adapters.embedding_child import EmbeddingChild
from rag_access_guard_api.adapters.embeddings import (
    E5EmbeddingAdapter,
    IsolatedE5EmbeddingAdapter,
    get_embedding_adapter,
)
from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime
from rag_access_guard_api.adapters.ollama_job import WindowsJob
from rag_access_guard_api.adapters.ollama_supervisor import ProcessIdentity
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.embedding_work import EmbeddingWorkRequest
from rag_access_guard_api.schemas.generation import InferenceBusyError, InferenceUnavailableError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process containment")


@dataclass(frozen=True)
class WorkerCase:
    adapter: IsolatedE5EmbeddingAdapter
    directory: Path
    runtime: InferenceRuntime


def wait_file(path: Path) -> None:
    deadline = monotonic() + 15
    while not path.exists():
        if monotonic() >= deadline:
            raise TimeoutError
        _ = Event().wait(0.02)


def assert_exited(directory: Path) -> None:
    identities = [
        ProcessIdentity.model_validate_json(path.read_bytes())
        for path in (directory / "root.json", directory / "descendant.json")
        if path.exists()
    ]
    deadline = monotonic() + 10
    while any(identity.alive() for identity in identities):
        if monotonic() >= deadline:
            pytest.fail("Owned E5 process remained alive")
        _ = Event().wait(0.02)


@pytest.fixture
def worker_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[WorkerCase]:
    helper = Path(__file__).parents[1] / "helpers" / "controlled_e5.py"
    _ = (tmp_path / "mode").write_text("success", encoding="utf-8")

    def command() -> tuple[str, ...]:
        return (sys.executable, str(helper), str(tmp_path), (tmp_path / "mode").read_text())

    monkeypatch.setattr(e5_process, "worker_command", command)
    runtime = InferenceRuntime()
    try:
        yield WorkerCase(
            IsolatedE5EmbeddingAdapter(tmp_path, runtime=runtime, retain_model=False),
            tmp_path,
            runtime,
        )
    finally:
        runtime.close()
        assert_exited(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows demonstration resource boundary")
@pytest.mark.parametrize("profile", ["qwen35-extractive-demo-v1", "qwen35-extractive-text-demo-v1"])
def test_extractive_factory_isolates_native_embedding_allocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PATH", str(tmp_path))
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", profile)
    adapter = get_embedding_adapter()
    assert isinstance(adapter, E5EmbeddingAdapter)
    assert type(adapter).__name__ == "IsolatedE5EmbeddingAdapter"
    assert not adapter.retain_model


def encode(
    case: WorkerCase, texts: tuple[str, ...], *, passage: bool = False
) -> tuple[tuple[float, ...], ...]:
    async def call() -> tuple[tuple[float, ...], ...]:
        if passage or not texts:
            return await case.adapter.embed_passages(texts)
        return (await case.adapter.embed_query(texts[0]),)

    return anyio.run(call)


def test_worker_request_is_private_absolute_and_bounded(tmp_path: Path) -> None:
    request = EmbeddingWorkRequest(
        nonce=uuid4(), path=tmp_path, texts=("PRIVATE_CANARY",), kind="query"
    )
    assert "PRIVATE_CANARY" not in repr(request)
    for replacement in ({"path": "relative"}, {"texts": ["x"] * 17}, {"kind": "untrusted"}):
        raw = request.model_dump(mode="json") | replacement
        with pytest.raises(ValidationError) as error:
            _ = EmbeddingWorkRequest.model_validate_json(json.dumps(raw))
        assert "PRIVATE_CANARY" not in str(error.value)


def test_isolated_batches_preserve_count_order_and_prefix(worker_case: WorkerCase) -> None:
    result = encode(worker_case, tuple(f"text-{index}" for index in range(17)), passage=True)
    assert len(result) == 17
    assert all(result[index][index] == 1.0 for index in range(16))
    assert result[16][0] == 1.0
    assert (worker_case.directory / "batches.jsonl").read_text().splitlines() == [
        '{"count": 16, "kind": "passage"}',
        '{"count": 1, "kind": "passage"}',
    ]
    assert_exited(worker_case.directory)


@pytest.mark.parametrize(
    "mode",
    [
        "exit",
        "invalid-json",
        "wrong-nonce",
        "bad-count",
        "bad-dimension",
        "bad-norm",
        "nonfinite",
        "oversize",
    ],
)
def test_invalid_worker_output_is_denied_after_physical_exit(
    worker_case: WorkerCase, mode: str
) -> None:
    _ = (worker_case.directory / "mode").write_text(mode)
    with pytest.raises(EmbeddingError):
        _ = encode(worker_case, ("PRIVATE_CANARY",))
    assert_exited(worker_case.directory)
    assert not worker_case.runtime.closed


def test_worker_timeout_reaps_root_and_descendant(
    worker_case: WorkerCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = (worker_case.directory / "mode").write_text("descendant")
    monkeypatch.setattr(e5_process, "WORKER_TIMEOUT_SECONDS", 3)
    started = monotonic()
    with pytest.raises(EmbeddingError):
        _ = encode(worker_case, ("CONTROLLED",))
    assert monotonic() - started < 10
    assert (worker_case.directory / "descendant.json").exists()
    assert_exited(worker_case.directory)


@pytest.mark.anyio
async def test_cancelled_waiter_keeps_physical_slot_until_child_exit(
    worker_case: WorkerCase,
) -> None:
    _ = (worker_case.directory / "mode").write_text("hold")
    async with anyio.create_task_group() as requests:
        _ = requests.start_soon(worker_case.adapter.embed_query, "CONTROLLED")
        await run_sync(wait_file, worker_case.directory / "root.json")
        requests.cancel_scope.cancel()
    assert worker_case.runtime.snapshot().embeddings == "busy"
    with pytest.raises(InferenceBusyError):
        _ = await worker_case.runtime.run_embedding(lambda: 1)
    (worker_case.directory / "release").touch()

    def wait_idle() -> None:
        deadline = monotonic() + 10
        while worker_case.runtime.snapshot().embeddings == "busy":
            if monotonic() >= deadline:
                raise TimeoutError
            _ = Event().wait(0.02)

    await run_sync(wait_idle)
    assert_exited(worker_case.directory)


@pytest.mark.anyio
async def test_runtime_close_reaps_active_worker(worker_case: WorkerCase) -> None:
    _ = (worker_case.directory / "mode").write_text("descendant")

    async def embed() -> None:
        with pytest.raises(EmbeddingError):
            _ = await worker_case.adapter.embed_query("CONTROLLED")

    with anyio.fail_after(10):
        async with anyio.create_task_group() as requests:
            _ = requests.start_soon(embed)
            await run_sync(wait_file, worker_case.directory / "descendant.json")
            worker_case.runtime.close()
    assert_exited(worker_case.directory)


def test_parent_death_kills_entire_embedding_job(tmp_path: Path) -> None:
    helper = Path(__file__).parents[1] / "helpers" / "controlled_e5.py"
    process = subprocess.Popen(  # noqa: S603 -- fixed owned native lifecycle fixture.
        [sys.executable, str(helper), str(tmp_path), "parent"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        wait_file(tmp_path / "descendant.json")
    finally:
        process.kill()
        _ = process.wait(timeout=10)
    assert_exited(tmp_path)


def test_uncertain_cleanup_quarantines_runtime(
    worker_case: WorkerCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = e5_process.confirm_worker_exit

    def uncertain(job: WindowsJob, process: EmbeddingChild) -> None:
        original(job, process)
        raise OSError

    monkeypatch.setattr(e5_process, "confirm_worker_exit", uncertain)
    with pytest.raises(EmbeddingError):
        _ = encode(worker_case, ("CONTROLLED",))
    assert worker_case.runtime.closed
    with pytest.raises(InferenceUnavailableError):
        _ = worker_case.runtime.try_acquire_generation(uuid4())


def test_kill_on_close_cannot_mutate_an_opened_job() -> None:
    with pytest.raises(ValueError, match=r"^$"):
        _ = WindowsJob("not-created", kill_on_close=True)


def test_fresh_transport_parent_does_not_import_torch(tmp_path: Path) -> None:
    helper = Path(__file__).parents[1] / "helpers" / "controlled_e5.py"
    result = subprocess.run(  # noqa: S603 -- fixed clean-process import boundary fixture.
        [sys.executable, str(helper), str(tmp_path), "parent-success"],
        capture_output=True,
        check=False,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.strip() == b'{"torch_in_parent": false}'


def test_empty_batch_is_denied_before_process_start(worker_case: WorkerCase) -> None:
    with pytest.raises(EmbeddingError):
        _ = encode(worker_case, ())
    assert not (worker_case.directory / "root.json").exists()


def test_creation_assignment_failure_never_creates_uncontained_child(
    worker_case: WorkerCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(job: WindowsJob) -> int:
        del job
        return 0

    monkeypatch.setattr(WindowsJob, "creation_handle", refuse)
    with pytest.raises(EmbeddingError):
        _ = encode(worker_case, ("CONTROLLED",))
    assert not (worker_case.directory / "root.json").exists()
    assert not worker_case.runtime.closed


def test_non_windows_factory_preserves_in_process_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PATH", str(tmp_path))
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen35-extractive-text-demo-v1")
    monkeypatch.setattr(sys, "platform", "linux")
    adapter = get_embedding_adapter()
    assert type(adapter) is E5EmbeddingAdapter
    assert not adapter.retain_model
