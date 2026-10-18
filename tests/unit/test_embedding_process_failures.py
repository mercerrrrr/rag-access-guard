import subprocess
import sys
from pathlib import Path
from threading import Event
from time import monotonic

import anyio
import psutil
import pytest
from anyio.to_thread import run_sync

from rag_access_guard_api.adapters import e5_process
from rag_access_guard_api.adapters.embedding_child import EmbeddingChild
from rag_access_guard_api.adapters.embeddings import IsolatedE5EmbeddingAdapter
from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime
from rag_access_guard_api.adapters.ollama_job import WindowsJob
from rag_access_guard_api.adapters.ollama_supervisor import ProcessIdentity
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.generation import InferenceUnavailableError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process containment")


def wait_path(path: Path) -> None:
    deadline = monotonic() + 10
    while not path.exists():
        if monotonic() >= deadline:
            raise TimeoutError
        _ = Event().wait(0.01)


def test_parent_death_before_identity_check_leaves_no_suspended_root(tmp_path: Path) -> None:
    helper = Path(__file__).parents[1] / "helpers" / "controlled_e5.py"
    owner = subprocess.Popen(  # noqa: S603 -- fixed native ownership regression fixture.
        [sys.executable, str(helper), str(tmp_path), "parent-boundary"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    identity: ProcessIdentity | None = None
    try:
        wait_path(tmp_path / "created.json")
        identity = ProcessIdentity.model_validate_json((tmp_path / "created.json").read_bytes())
        owner.kill()
        _ = owner.wait(timeout=5)
        deadline = monotonic() + 2
        while identity.alive() and monotonic() < deadline:
            _ = Event().wait(0.01)
        assert not identity.alive(), "Creation left a suspended orphan outside containment"
        assert not (tmp_path / "root.json").exists()
    finally:
        if owner.poll() is None:
            owner.kill()
        _ = owner.wait(timeout=5)
        if identity is not None and identity.alive():
            psutil.Process(identity.pid).kill()
            _ = psutil.Process(identity.pid).wait(timeout=5)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IsolatedE5EmbeddingAdapter:
    helper = Path(__file__).parents[1] / "helpers" / "controlled_e5.py"
    monkeypatch.setattr(
        e5_process,
        "worker_command",
        lambda: (sys.executable, str(helper), str(tmp_path), "success"),
    )
    return IsolatedE5EmbeddingAdapter(tmp_path, runtime=InferenceRuntime(), retain_model=False)


@pytest.mark.anyio
async def test_capacity_waits_for_kernel_empty_job_including_late_members(
    isolated: IsolatedE5EmbeddingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    terminated, release, completed = Event(), Event(), Event()
    original_pids, original_terminate = WindowsJob.pids, WindowsJob.terminate

    def pids(job: WindowsJob) -> tuple[int, ...]:
        if terminated.is_set() and not release.is_set():
            return (0x7FFFFFFE,)
        return original_pids(job)

    def terminate(job: WindowsJob) -> None:
        original_terminate(job)
        terminated.set()

    monkeypatch.setattr(WindowsJob, "pids", pids)
    monkeypatch.setattr(WindowsJob, "terminate", terminate)

    async def embed() -> None:
        try:
            _ = await isolated.embed_query("CONTROLLED")
        finally:
            completed.set()

    try:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(embed)
            assert await run_sync(terminated.wait, 10)
            assert not await run_sync(completed.wait, 0.2)
            assert isolated.runtime.snapshot().embeddings == "busy"
            release.set()
    finally:
        release.set()
        isolated.runtime.close()


def test_identity_uncertainty_quarantines_before_future_completion(
    isolated: IsolatedE5EmbeddingAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def uncertain(identity: ProcessIdentity) -> bool:
        del identity
        raise InferenceUnavailableError

    monkeypatch.setattr(ProcessIdentity, "alive", uncertain)
    with pytest.raises(EmbeddingError):
        _ = anyio.run(isolated.embed_query, "CONTROLLED")
    assert isolated.runtime.closed


@pytest.mark.anyio
async def test_blocked_transport_quarantines_when_termination_fails(
    isolated: IsolatedE5EmbeddingAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = Path(__file__).parents[1] / "helpers" / "controlled_e5.py"
    monkeypatch.setattr(
        e5_process,
        "worker_command",
        lambda: (sys.executable, str(helper), str(tmp_path), "blocked-input"),
    )
    monkeypatch.setattr(e5_process, "WORKER_TIMEOUT_SECONDS", 1)
    termination_attempted = Event()
    original_terminate = WindowsJob.terminate

    def fail_termination(job: WindowsJob) -> None:
        del job
        termination_attempted.set()
        raise OSError

    monkeypatch.setattr(WindowsJob, "terminate", fail_termination)

    def fail_kill(process: EmbeddingChild) -> None:
        del process
        raise OSError

    monkeypatch.setattr(EmbeddingChild, "kill", fail_kill)

    async def embed() -> None:
        with pytest.raises(EmbeddingError):
            _ = await isolated.embed_query("CONTROLLED" * 200000)

    try:
        with anyio.fail_after(10):
            async with anyio.create_task_group() as tasks:
                _ = tasks.start_soon(embed)
                assert await run_sync(termination_attempted.wait, 5)
                assert isolated.runtime.closed
    finally:
        monkeypatch.setattr(WindowsJob, "terminate", original_terminate)
        (tmp_path / "release").touch()
        isolated.runtime.close()
