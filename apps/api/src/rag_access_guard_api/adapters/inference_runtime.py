"""Application admission and physical Future lifetime, independent of HTTP waiters."""

import atexit
from collections.abc import Callable, Generator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache, partial
from threading import Event, Lock
from types import TracebackType
from typing import TYPE_CHECKING, Final, Literal, Self
from uuid import UUID

import anyio
import psutil
from anyio.lowlevel import checkpoint
from anyio.to_thread import run_sync
from pydantic import ValidationError

from rag_access_guard_api.config import INFERENCE_RECOVERY_SECONDS
from rag_access_guard_api.schemas.generation import (
    InferenceBusyError,
    InferenceUnavailableError,
)

__all__ = ["InferenceBusyError", "InferenceUnavailableError"]

if TYPE_CHECKING:
    from rag_access_guard_api.adapters.ollama_supervisor import OllamaSupervisor


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Expose only readiness, without identities or process details."""

    generation: Literal["ready", "busy", "unavailable"]
    embeddings: Literal["ready", "busy", "unavailable"]


class GenerationPermit:
    """Release one acquired application permit exactly once, including cancellation."""

    def __init__(self, runtime: "InferenceRuntime") -> None:
        """Bind the permit to its issuing scope."""
        self._runtime: InferenceRuntime = runtime
        self._released: bool = False

    async def __aenter__(self) -> Self:
        """Enter the already acquired permit without waiting."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release admission on success, error or cancellation."""
        if not self._released:
            self._released = True
            self._runtime.release_generation()


class InferenceRuntime:
    """Mutate admission under a thread lock; a native Future owns its E5 slot."""

    def __init__(self) -> None:
        """Create one physical executor and independent coroutine admission."""
        self._lock: Lock = Lock()
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rag-e5"
        )
        self._closed: bool = False
        self._generation_unavailable: bool = False
        self._generation_principal: UUID | None = None
        self._embedding_busy: bool = False
        self._cancel_pending: Callable[[], bool] | None = None
        self._supervisor: OllamaSupervisor | None = None
        self.pre_admission_rejections: int = 0
        self.accepted_embedding_jobs: int = 0
        self.detached_embedding_waiters: int = 0

    @property
    def closed(self) -> bool:
        """Expose shutdown to a late recovery worker without restoring admission."""
        with self._lock:
            return self._closed

    def own_supervisor(self, supervisor: "OllamaSupervisor") -> "OllamaSupervisor":
        """Retain one validated owned process controller for the application lifetime."""
        with self._lock:
            if self._closed:
                raise InferenceUnavailableError
            if self._supervisor is None:
                self._supervisor = supervisor
            elif self._supervisor.config != supervisor.config:
                self._generation_unavailable = True
                raise InferenceUnavailableError
            return self._supervisor

    def try_acquire_generation(self, principal_id: UUID) -> GenerationPermit:
        """Acquire global/per-principal capacity one with no waiting queue."""
        with self._lock:
            if self._closed or self._generation_unavailable:
                self.pre_admission_rejections += 1
                raise InferenceUnavailableError
            if self._generation_principal is not None:
                self.pre_admission_rejections += 1
                raise InferenceBusyError
            self._generation_principal = principal_id
        return GenerationPermit(self)

    def release_generation(self) -> None:
        """Release coroutine admission; unavailable quarantine remains independent."""
        with self._lock:
            self._generation_principal = None

    def set_generation_available(self, *, available: bool) -> None:
        """Quarantine unknown upstream work until owned exit and profile recheck."""
        with self._lock:
            self._generation_unavailable = not available

    async def run_embedding[T](self, call: Callable[[], T]) -> T:
        """Cancel the waiter promptly, retaining capacity until actual Future completion."""
        await checkpoint()
        completed = Event()
        with self._lock:
            if self._closed:
                raise InferenceUnavailableError
            if self._embedding_busy:
                raise InferenceBusyError
            self._embedding_busy = True
            future = self._executor.submit(call)
            self._cancel_pending = future.cancel
            self.accepted_embedding_jobs += 1
        future.add_done_callback(partial(self._embedding_completed, completed=completed))

        def observe() -> T:
            try:
                return future.result()
            finally:
                _ = completed.wait()

        try:
            # Waiting is cancellable; this blocking observer never submits native inference.
            return await run_sync(observe, abandon_on_cancel=True)
        except anyio.get_cancelled_exc_class():
            with self._lock:
                self.detached_embedding_waiters += 1
            _ = future.cancel()
            raise

    def _embedding_completed[T](self, future: Future[T], *, completed: Event) -> None:
        del future
        with self._lock:
            self._embedding_busy = False
            self._cancel_pending = None
        completed.set()

    def snapshot(self) -> RuntimeSnapshot:
        """Read state without exposing queue, user or process identities."""
        with self._lock:
            return RuntimeSnapshot(
                generation=(
                    "unavailable"
                    if self._closed or self._generation_unavailable
                    else "busy"
                    if self._generation_principal is not None
                    else "ready"
                ),
                embeddings=(
                    "unavailable" if self._closed else "busy" if self._embedding_busy else "ready"
                ),
            )

    async def aclose(self) -> None:
        """Stop accepting; do not claim running native threads can be forcibly stopped."""
        self.close()
        supervisor = self._supervisor
        if supervisor is not None:
            with anyio.move_on_after(INFERENCE_RECOVERY_SECONDS, shield=True):
                try:
                    await run_sync(supervisor.close_recovery_child, abandon_on_cancel=True)
                except (
                    OSError,
                    RuntimeError,
                    psutil.Error,
                    ValidationError,
                    InferenceUnavailableError,
                ):
                    self.set_generation_available(available=False)

    def close(self) -> None:
        """Close admission without claiming active native work exited."""
        with self._lock:
            self._closed = True
            cancel = self._cancel_pending
        if cancel is not None:
            _ = cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)


_CURRENT: Final[ContextVar[InferenceRuntime | Literal["unavailable"] | None]] = ContextVar(
    "inference_runtime", default=None
)


def current_runtime() -> InferenceRuntime:
    """Use the bound application or one legacy process-scoped standalone owner."""
    runtime = _CURRENT.get()
    if runtime is None:
        return standalone_runtime()
    if runtime == "unavailable":
        raise InferenceUnavailableError
    return runtime


@cache
def standalone_runtime() -> InferenceRuntime:
    """Share one standalone bound; atexit close is not native termination proof."""
    runtime = InferenceRuntime()
    _ = atexit.register(runtime.close)
    return runtime


@contextmanager
def bind_runtime(runtime: InferenceRuntime | None) -> Generator[None]:
    """Propagate the same application runtime to query, upload and model adapters."""
    token = _CURRENT.set(runtime if runtime is not None else "unavailable")
    try:
        yield
    finally:
        _CURRENT.reset(token)
