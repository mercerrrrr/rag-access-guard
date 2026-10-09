"""Own only the private demo process and its kernel-contained descendants."""

import os
import socket
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event
from time import monotonic
from typing import ClassVar, Final, Literal, Self
from uuid import UUID, uuid4

import psutil
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from rag_access_guard_api.adapters.ollama_job import WindowsJob
from rag_access_guard_api.schemas.generation import InferenceUnavailableError

DEMO_PORT: Final = 11435


class SupervisorConfig(BaseModel):
    """Parse private operator configuration; callers cannot supply endpoint or paths."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )
    instance_id: UUID
    executable: Path
    host: Literal["http://127.0.0.1:11435"]
    state_path: Path
    model_store_path: Path

    @model_validator(mode="after")
    def absolute_paths(self) -> Self:
        """Reject cwd-dependent process and private-storage locations."""
        if not all(
            path.is_absolute() for path in (self.executable, self.state_path, self.model_store_path)
        ):
            raise ValueError
        if any(
            (parent / ".git").exists()
            for path in (self.state_path.resolve(), self.model_store_path.resolve())
            for parent in path.parents
        ):
            raise ValueError
        return self


def load_supervisor_config(path: Path) -> SupervisorConfig:
    """Load only an absolute private operator configuration outside Git."""
    if not path.is_absolute() or any(
        (parent / ".git").exists() for parent in path.resolve().parents
    ):
        raise InferenceUnavailableError
    try:
        return SupervisorConfig.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        raise InferenceUnavailableError from None


class ProcessIdentity(BaseModel):
    """Bind a process identifier to creation time and canonical executable."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    pid: int
    created: float
    executable: Path

    @classmethod
    def read(cls, pid: int) -> Self:
        """Read the identity before any process action."""
        process = psutil.Process(pid)
        return cls(pid=pid, created=process.create_time(), executable=Path(process.exe()).resolve())

    def alive(self) -> bool:
        """Distinguish actual exit from PID reuse and retained Windows objects."""
        try:
            actual = self.read(self.pid)
        except psutil.NoSuchProcess:
            return False
        if actual != self:
            raise InferenceUnavailableError
        try:
            _ = psutil.Process(self.pid).wait(timeout=0)
        except psutil.NoSuchProcess:
            return False
        except psutil.TimeoutExpired:
            return True
        return False


class Ownership(BaseModel):
    """Private evidence used before any process operation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    instance_id: UUID
    host: Literal["http://127.0.0.1:11435"]
    root: ProcessIdentity
    job_name: str
    quarantined: bool = False


class OllamaSupervisor:
    """Mutate only an instance proven by private ownership and a named Windows job."""

    def __init__(self, config: SupervisorConfig, *, command: tuple[str, ...] | None = None) -> None:
        """Bind operator configuration and an optional controlled-test command."""
        self.config: SupervisorConfig = config
        self._command: tuple[str, ...] = command or (str(config.executable), "serve")
        self._process: subprocess.Popen[bytes] | None = None
        self._spawned: Ownership | None = None
        self._claim: Ownership | None = None
        self._failed: bool = False

    def _write_state(self, state: Ownership) -> None:
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.config.state_path.parent, delete=False
        ) as temporary:
            _ = temporary.write(state.model_dump_json())
            temporary_path = Path(temporary.name)
        try:
            _ = temporary_path.replace(self.config.state_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def begin_inference(self) -> Ownership:
        """Persist uncertainty before dispatch; never overwrite another live claim."""
        self._failed = True
        with self._coordination():
            state = self._ownership()
            if state.quarantined or not state.root.alive():
                raise InferenceUnavailableError
            job = WindowsJob(state.job_name)
            try:
                if not job.contains(state.root.pid):
                    raise InferenceUnavailableError
                claim = state.model_copy(update={"quarantined": True})
                self._write_state(claim)
                self._claim = claim
                return claim
            finally:
                job.close()

    def complete_inference(self, claim: Ownership) -> None:
        """Clear only this verified owner's strictly completed inference claim."""
        with self._coordination():
            state = self._ownership()
            if state != claim or self._claim != claim or not state.root.alive():
                raise InferenceUnavailableError
            job = WindowsJob(state.job_name)
            try:
                if not job.contains(state.root.pid):
                    raise InferenceUnavailableError
                self._write_state(state.model_copy(update={"quarantined": False}))
                self._claim = None
                self._failed = False
            finally:
                job.close()

    def close_recovery_child(self) -> None:
        """Stop/reap only the child spawned by this owner, never another live claim."""
        with self._coordination():
            spawned = self._spawned
            if spawned is None:
                return
            if not self.config.state_path.exists():
                if self._process is not None:
                    _ = self._process.wait(timeout=5)
                    self._process = None
                self._spawned = None
                return
            state = self._ownership()
            if (
                state.root != spawned.root
                or state.job_name != spawned.job_name
                or (state.quarantined and state != self._claim)
            ):
                raise InferenceUnavailableError
            self._stop()

    def _ownership(self) -> Ownership:
        state = Ownership.model_validate_json(self.config.state_path.read_bytes())
        prefix = f"Local\\rag-demo-{self.config.instance_id}-"
        try:
            job_id = UUID(state.job_name.removeprefix(prefix))
        except ValueError:
            raise InferenceUnavailableError from None
        if (
            state.instance_id != self.config.instance_id
            or state.host != self.config.host
            or state.root.executable != self.config.executable.resolve()
            or state.job_name != prefix + str(job_id)
        ):
            raise InferenceUnavailableError
        return state

    @contextmanager
    def _coordination(self) -> Generator[None]:
        if sys.platform != "win32":
            raise InferenceUnavailableError
        import msvcrt  # noqa: PLC0415 -- Windows-only owned-instance coordination.

        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.state_path.with_suffix(".lock").open("a+b") as lock:
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise InferenceUnavailableError from None
            try:
                yield
            finally:
                _ = lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def start(self) -> None:
        """Create suspended, contain before execution, persist identity, then resume."""
        with self._coordination():
            self._start()

    def _start(self) -> None:
        if sys.platform != "win32":
            raise InferenceUnavailableError
        if self.config.state_path.exists():
            state = self._ownership()
            if state.quarantined:
                raise InferenceUnavailableError
            if state.root.alive():
                job = WindowsJob(state.job_name)
                try:
                    if not job.contains(state.root.pid):
                        raise InferenceUnavailableError
                    return
                finally:
                    job.close()
            self._stop()
        with socket.socket() as endpoint:
            if endpoint.connect_ex(("127.0.0.1", 11435)) == 0:
                raise InferenceUnavailableError
        environment = dict(os.environ)
        environment.update(
            OLLAMA_HOST=self.config.host,
            OLLAMA_NUM_PARALLEL="1",
            OLLAMA_MAX_QUEUE="1",
            OLLAMA_MODELS=str(self.config.model_store_path),
        )
        job = WindowsJob(f"Local\\rag-demo-{self.config.instance_id}-{uuid4()}", create=True)
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(  # noqa: S603 -- fixed operator executable, no shell.
                self._command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                creationflags=0x08000004,
                startupinfo=subprocess.STARTUPINFO(
                    lpAttributeList={"handle_list": [job.inherited_handle()]}
                ),
            )
            identity = ProcessIdentity.read(process.pid)
            if identity.executable != self.config.executable.resolve():
                raise InferenceUnavailableError  # noqa: TRY301 -- own partial-spawn cleanup.
            job.assign(process.pid)
            state = Ownership(
                instance_id=self.config.instance_id,
                host=self.config.host,
                root=identity,
                job_name=job.name,
            )
            self._write_state(state)
            psutil.Process(process.pid).resume()
            self._process = process
            self._spawned = state
            self._failed = False
        except (OSError, psutil.Error, InferenceUnavailableError):
            if process is not None:
                process.kill()
                _ = process.wait(timeout=5)
            raise InferenceUnavailableError from None
        finally:
            job.close()

    def owned_processes(self) -> tuple[ProcessIdentity, ...]:
        """Read verified Job members without publishing private identities."""
        state = self._ownership()
        _ = state.root.alive()
        try:
            job = WindowsJob(state.job_name)
        except FileNotFoundError:
            raise InferenceUnavailableError from None
        try:
            if state.root.alive() and not job.contains(state.root.pid):
                raise InferenceUnavailableError
            return self._members(job)
        finally:
            job.close()

    @contextmanager
    def hold_containment(self) -> Generator[None]:
        """Keep kernel containment reopenable even when the admitted root exits."""
        state = self._ownership()
        job = WindowsJob(state.job_name)
        try:
            if state.quarantined or not state.root.alive() or not job.contains(state.root.pid):
                raise InferenceUnavailableError
            yield
        finally:
            job.close()

    def _members(self, job: WindowsJob) -> tuple[ProcessIdentity, ...]:
        members: list[ProcessIdentity] = []
        for pid in job.pids():
            try:
                identity = ProcessIdentity.read(pid)
            except psutil.NoSuchProcess:
                continue
            if not identity.alive():
                continue
            try:
                contained = job.contains(pid)
            except OSError:
                if identity.alive():
                    raise InferenceUnavailableError from None
                continue
            if not contained:
                raise InferenceUnavailableError
            members.append(identity)
        return tuple(members)

    def stop(self) -> None:
        """Terminate only verified containment and confirm all runner exits."""
        with self._coordination():
            if self._claim is not None and self._ownership() != self._claim:
                raise InferenceUnavailableError
            self._stop()

    def _stop(self) -> None:
        if not self.config.state_path.exists():
            return
        state = self._ownership()
        alive = state.root.alive()
        try:
            job = WindowsJob(state.job_name)
        except FileNotFoundError:
            raise InferenceUnavailableError from None
        try:
            if alive and not job.contains(state.root.pid):
                raise InferenceUnavailableError
            identities = self._members(job)
            job.terminate()
            deadline = monotonic() + 5
            interval = Event()
            while job.pids() or any(member.alive() for member in identities):
                if monotonic() >= deadline:
                    raise InferenceUnavailableError
                _ = interval.wait(0.01)
            if self._process is not None:
                _ = self._process.wait(timeout=5)
                self._process = None
            self.config.state_path.unlink()
            self._spawned = None
            self._claim = None
            self._failed = False
        finally:
            job.close()

    def status(self) -> Literal["starting", "ready", "stopped", "unavailable"]:
        """Report readiness without treating missing containment as exit proof."""
        if not self.config.state_path.exists():
            return "stopped"
        try:
            state = self._ownership()
            if self._failed or state.quarantined or not state.root.alive():
                return "unavailable"
            with socket.socket() as endpoint:
                if endpoint.connect_ex(("127.0.0.1", 11435)) != 0:
                    return "starting"
            members = self.owned_processes()
            return (
                "ready"
                if any(
                    row.status == psutil.CONN_LISTEN and row.laddr.port == DEMO_PORT
                    for member in members
                    if member.alive()
                    for row in psutil.Process(member.pid).net_connections(kind="tcp")
                )
                else "unavailable"
            )
        except (OSError, psutil.Error, ValidationError, InferenceUnavailableError):
            return "unavailable"
