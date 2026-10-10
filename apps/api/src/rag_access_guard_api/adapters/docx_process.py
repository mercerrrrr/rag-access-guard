"""Bound stdin/stdout and supervise the parser without blocking the event loop."""

import os
import signal
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from threading import Event, Thread
from time import monotonic
from typing import BinaryIO
from uuid import uuid4

import psutil
from pydantic import BaseModel, ValidationError

from rag_access_guard_api.adapters import docx_protocol
from rag_access_guard_api.adapters.ollama_job import WindowsJob
from rag_access_guard_api.schemas.generation import InferenceUnavailableError
from rag_access_guard_api.services.text_documents import DocumentError


@dataclass(slots=True)
class _Output:
    data: bytearray = field(default_factory=bytearray)
    failed: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)


class _MemoryInfo(BaseModel):
    rss: int


def _read(pipe: BinaryIO, output: _Output) -> None:
    try:
        while chunk := pipe.read(65536):
            if len(output.data) + len(chunk) > docx_protocol.MAX_RESPONSE_BYTES:
                output.failed.set()
                return
            output.data.extend(chunk)
    except OSError:
        output.failed.set()
    finally:
        output.done.set()


def _write(pipe: BinaryIO, data: bytes) -> None:
    try:
        _ = pipe.write(data)
    except OSError:
        pass
    finally:
        with suppress(OSError):
            pipe.close()


def run_worker(data: bytes) -> tuple[int, bytes]:
    """Reap the fixed parser command on every exit, including resource failures."""
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    command = _worker_command()
    job: WindowsJob | None = None
    try:
        with subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            creationflags=0x08000004 if sys.platform == "win32" else 0,
            start_new_session=sys.platform != "win32",
        ) as process:
            try:
                if sys.platform == "win32":
                    job = WindowsJob(f"Local\\rag-docx-{uuid4().hex}", create=True)
                    job.assign(process.pid)
                    psutil.Process(process.pid).resume()
            except (OSError, psutil.Error, InferenceUnavailableError):
                process.kill()
                _ = process.wait()
                raise
            try:
                return _supervise(process, data, job)
            finally:
                if process.poll() is None:
                    _reap(process, {}, job)
    except (OSError, psutil.Error, ValidationError, InferenceUnavailableError, OverflowError):
        raise DocumentError(503, "parse_failed") from None
    finally:
        if job is not None:
            job.close()


def _worker_command() -> tuple[str, ...]:
    return sys.executable, "-I", "-m", "rag_access_guard_api.adapters.docx_worker"


def _collect_children(monitor: psutil.Process, children: dict[int, psutil.Process]) -> None:
    with suppress(psutil.NoSuchProcess):
        children.update((child.pid, child) for child in monitor.children(recursive=True))


def _collect_job(job: WindowsJob | None, children: dict[int, psutil.Process]) -> None:
    if job is not None:
        for pid in job.pids():
            with suppress(psutil.NoSuchProcess):
                children[pid] = psutil.Process(pid)


def _tree_rss(
    monitor: psutil.Process, children: dict[int, psutil.Process], job: WindowsJob | None
) -> int:
    _collect_children(monitor, children)
    _collect_job(job, children)
    total = 0
    members = {monitor.pid: monitor, **children}
    for member in members.values():
        try:
            total += _MemoryInfo.model_validate(
                member.memory_info(), from_attributes=True, strict=True
            ).rss
        except psutil.NoSuchProcess:
            continue
    return total


def _supervise(
    process: subprocess.Popen[bytes], data: bytes, job: WindowsJob | None
) -> tuple[int, bytes]:
    if process.stdin is None or process.stdout is None:
        raise DocumentError(503, "parse_failed")
    output = _Output()
    reader = Thread(target=_read, args=(process.stdout, output), name="docx-output")
    writer = Thread(target=_write, args=(process.stdin, data), name="docx-input")
    deadline = monotonic() + docx_protocol.TIMEOUT_SECONDS
    children: dict[int, psutil.Process] = {}
    interval = Event()
    try:
        monitor = psutil.Process(process.pid)
        try:
            reader.start()
            writer.start()
        except RuntimeError:
            raise DocumentError(503, "parse_failed") from None
        while True:
            if output.failed.is_set() or monotonic() >= deadline:
                raise DocumentError(422, "parse_failed")
            status = process.poll()
            if status is not None and output.done.is_set():
                return status, bytes(output.data)
            if _tree_rss(monitor, children, job) > docx_protocol.MAX_RSS_BYTES:
                raise DocumentError(422, "parse_failed")
            _ = interval.wait(docx_protocol.POLL_SECONDS)
    finally:
        try:
            _reap(process, children, job)
        finally:
            if writer.ident is not None:
                writer.join()
            if reader.ident is not None:
                reader.join()


def _reap(
    process: subprocess.Popen[bytes], children: dict[int, psutil.Process], job: WindowsJob | None
) -> None:
    deadline = monotonic() + 3
    failed = False
    try:
        with suppress(psutil.NoSuchProcess):
            _collect_children(psutil.Process(process.pid), children)
        _collect_job(job, children)
    except (OSError, psutil.Error, InferenceUnavailableError, OverflowError):
        failed = True
    failed = _stop_owned(process, children, job) or failed
    _, alive = psutil.wait_procs(tuple(children.values()), timeout=max(0, deadline - monotonic()))
    try:
        active = job.pids() if job is not None else _group_active_after_wait(process.pid, deadline)
    except (OSError, InferenceUnavailableError, OverflowError):
        failed = True
        active = ()
    if failed or alive or active:
        raise DocumentError(503, "parse_failed")


def _group_active_after_wait(group: int, deadline: float) -> bool:
    """Wait for owned-group disappearance; sampled children and pipe EOF miss orphans."""
    pause = Event()
    while True:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return False
        remaining = deadline - monotonic()
        if remaining <= 0:
            return True
        _ = pause.wait(min(docx_protocol.POLL_SECONDS, remaining))


def _stop_owned(
    process: subprocess.Popen[bytes], children: dict[int, psutil.Process], job: WindowsJob | None
) -> bool:
    failed = False
    try:
        if job is not None:
            job.terminate()
        else:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    except (OSError, InferenceUnavailableError):
        failed = True
    for child in reversed(tuple(children.values())):
        with suppress(psutil.Error):
            child.kill()
    if process.poll() is None:
        process.kill()
    _ = process.wait()
    return failed
