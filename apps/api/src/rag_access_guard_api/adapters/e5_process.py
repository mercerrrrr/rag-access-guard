"""Short-lived, kernel-contained E5 execution for the Windows demonstration."""

import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Final, Literal
from uuid import uuid4

import psutil
from pydantic import TypeAdapter, ValidationError

from rag_access_guard_api.adapters.embedding_child import EmbeddingChild, spawn_contained
from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime
from rag_access_guard_api.adapters.ollama_job import WindowsJob
from rag_access_guard_api.adapters.ollama_supervisor import ProcessIdentity
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError, validate_vectors
from rag_access_guard_api.schemas.embedding_work import EmbeddingWorkRequest, EmbeddingWorkResponse
from rag_access_guard_api.schemas.generation import InferenceUnavailableError
from rag_access_guard_api.schemas.relevance_work import RelevanceWorkRequest, RelevanceWorkResponse

MAX_REQUEST_BYTES: Final = 32 * 1024**2
MAX_RESPONSE_BYTES: Final = 1024**2
WORKER_TIMEOUT_SECONDS: Final = 60


class EmbeddingProcessUncertainError(EmbeddingError):
    """An owned process exit could not be proved; admission must remain closed."""


def worker_command() -> tuple[str, ...]:
    """Use the fixed installed module, never a client or configuration command."""
    return (sys.executable, "-m", "rag_access_guard_api.adapters.e5_worker")


def confirm_worker_exit(job: WindowsJob, process: EmbeddingChild) -> None:
    """Keep containment until the kernel reports no active member, including late children."""
    members: list[ProcessIdentity] = []
    uncertain = False
    try:
        for pid in job.pids():
            try:
                members.append(ProcessIdentity.read(pid))
            except psutil.NoSuchProcess:
                continue
        job.terminate()
        _ = process.wait(timeout=5)
        deadline = monotonic() + 5
        while job.pids() or any(member.alive() for member in members):
            if monotonic() >= deadline:
                uncertain = True
                break
            _ = Event().wait(0.01)
    except (
        OSError,
        psutil.Error,
        InferenceUnavailableError,
        subprocess.TimeoutExpired,
        TimeoutError,
        OverflowError,
    ):
        uncertain = True
    finally:
        job.close()
    if uncertain:
        raise EmbeddingProcessUncertainError


def _close_pipes(process: EmbeddingChild) -> None:
    with suppress(OSError):
        process.close()


def _start_child(job: WindowsJob) -> EmbeddingChild:
    process = spawn_contained(worker_command(), job.creation_handle())
    try:
        identity = ProcessIdentity.read(process.pid)
        if identity.executable != Path(sys.executable).resolve():
            raise EmbeddingError  # noqa: TRY301 -- partial spawn still needs physical cleanup.
        psutil.Process(process.pid).resume()
    except (OSError, psutil.Error, EmbeddingError, InferenceUnavailableError):
        try:
            confirm_worker_exit(job, process)
        finally:
            _close_pipes(process)
        raise EmbeddingError from None
    return process


@contextmanager
def _watch_child(
    job: WindowsJob, process: EmbeddingChild, runtime: InferenceRuntime
) -> Generator[Event]:
    finished, expired = Event(), Event()
    deadline = monotonic() + WORKER_TIMEOUT_SECONDS

    def watch() -> None:
        while not finished.wait(0.05):
            if runtime.closed or monotonic() >= deadline:
                expired.set()
                # A stuck pipe cannot delay fail-closed admission, even if Windows refuses exit.
                runtime.close()
                try:
                    job.terminate()
                except OSError:
                    with suppress(OSError):
                        process.kill()
                    job.close()
                return

    watcher = Thread(target=watch, daemon=True, name="rag-e5-owner")
    watcher.start()
    try:
        yield expired
    finally:
        finished.set()
        watcher.join(timeout=5)
        if watcher.is_alive():
            raise EmbeddingProcessUncertainError


def _exchange(
    process: EmbeddingChild, request: EmbeddingWorkRequest, expired: Event
) -> tuple[tuple[float, ...], ...]:
    _ = process.stdin.write(request.model_dump_json().encode())
    process.stdin.close()
    raw_response = TypeAdapter(bytes).validate_python(
        process.stdout.read(MAX_RESPONSE_BYTES + 1), strict=True
    )
    if expired.is_set() or len(raw_response) > MAX_RESPONSE_BYTES:
        raise EmbeddingError
    if process.wait(timeout=5) != 0:
        raise EmbeddingError
    response = EmbeddingWorkResponse.model_validate_json(raw_response)
    if response.nonce != request.nonce:
        raise EmbeddingError
    validate_vectors(response.vectors, len(request.texts))
    return response.vectors


def _run_batch(
    request: EmbeddingWorkRequest, runtime: InferenceRuntime
) -> tuple[tuple[float, ...], ...]:
    if len(request.model_dump_json().encode()) > MAX_REQUEST_BYTES or runtime.closed:
        raise EmbeddingError
    job = WindowsJob(f"Local\\rag-e5-{uuid4()}", create=True, kill_on_close=True)
    process: EmbeddingChild | None = None
    try:
        process = _start_child(job)
        with _watch_child(job, process, runtime) as expired:
            vectors = _exchange(process, request, expired)
    except (OSError, psutil.Error, ValueError, ValidationError, subprocess.TimeoutExpired):
        raise EmbeddingError from None
    else:
        return vectors
    finally:
        if process is None:
            job.close()
        else:
            try:
                confirm_worker_exit(job, process)
            finally:
                _close_pipes(process)


def encode_isolated(
    path: Path,
    texts: tuple[str, ...],
    kind: Literal["query", "passage"],
    runtime: InferenceRuntime,
) -> tuple[tuple[float, ...], ...]:
    """Preserve E5 batch16 ordering while giving every batch a physical exit boundary."""
    if not texts or sys.platform != "win32":
        raise EmbeddingError
    result: list[tuple[float, ...]] = []
    try:
        for start in range(0, len(texts), 16):
            request = EmbeddingWorkRequest(
                nonce=uuid4(), path=path.resolve(), texts=texts[start : start + 16], kind=kind
            )
            result.extend(_run_batch(request, runtime))
    except ValidationError:
        raise EmbeddingError from None
    return tuple(result)


def _exchange_relevance(
    process: EmbeddingChild, request: RelevanceWorkRequest, expired: Event
) -> tuple[float, ...]:
    _ = process.stdin.write(request.model_dump_json().encode())
    process.stdin.close()
    raw = TypeAdapter(bytes).validate_python(
        process.stdout.read(MAX_RESPONSE_BYTES + 1), strict=True
    )
    if expired.is_set() or len(raw) > MAX_RESPONSE_BYTES or process.wait(timeout=5) != 0:
        raise EmbeddingError
    response = RelevanceWorkResponse.model_validate_json(raw)
    if response.nonce != request.nonce or len(response.scores) != len(request.texts):
        raise EmbeddingError
    return response.scores


def score_relevance_isolated(
    path: Path, question: str, texts: tuple[str, ...], runtime: InferenceRuntime
) -> tuple[float, ...]:
    """Reuse creation-time containment and prove exit before releasing physical admission."""
    request = RelevanceWorkRequest(
        nonce=uuid4(), config_path=path.resolve(), question=question, texts=texts
    )
    if runtime.closed or len(request.model_dump_json().encode()) > MAX_REQUEST_BYTES:
        raise EmbeddingError
    job = WindowsJob(f"Local\\rag-relevance-{uuid4()}", create=True, kill_on_close=True)
    process: EmbeddingChild | None = None
    try:
        process = _start_child(job)
        with _watch_child(job, process, runtime) as expired:
            scores = _exchange_relevance(process, request, expired)
    except (OSError, psutil.Error, ValueError, ValidationError, subprocess.TimeoutExpired):
        raise EmbeddingError from None
    else:
        return scores
    finally:
        if process is None:
            job.close()
        else:
            try:
                confirm_worker_exit(job, process)
            finally:
                _close_pipes(process)
