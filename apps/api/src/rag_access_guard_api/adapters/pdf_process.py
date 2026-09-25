"""Bound stdin/stdout and supervise the parser without blocking the event loop."""

import os
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from threading import Event, Thread
from time import monotonic
from typing import BinaryIO

import psutil
from pydantic import BaseModel, ValidationError

from rag_access_guard_api.adapters import pdf_protocol
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
            if len(output.data) + len(chunk) > pdf_protocol.MAX_RESPONSE_BYTES:
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
        pipe.close()


def run_worker(data: bytes) -> tuple[int, bytes]:
    """Reap the fixed parser command on every exit, including resource failures."""
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    command = _worker_command()
    try:
        with subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        ) as process:
            return _supervise(process, data)
    except (OSError, psutil.Error, ValidationError):
        raise DocumentError(503, "parse_failed") from None


def _worker_command() -> tuple[str, ...]:
    return sys.executable, "-I", "-m", "rag_access_guard_api.adapters.pdf_worker"


def _collect_children(monitor: psutil.Process, children: dict[int, psutil.Process]) -> None:
    with suppress(psutil.NoSuchProcess):
        children.update((child.pid, child) for child in monitor.children(recursive=True))


def _tree_rss(monitor: psutil.Process, children: dict[int, psutil.Process]) -> int:
    _collect_children(monitor, children)
    total = 0
    for member in (monitor, *children.values()):
        try:
            total += _MemoryInfo.model_validate(
                member.memory_info(), from_attributes=True, strict=True
            ).rss
        except psutil.NoSuchProcess:
            continue
    return total


def _supervise(process: subprocess.Popen[bytes], data: bytes) -> tuple[int, bytes]:
    if process.stdin is None or process.stdout is None:
        raise DocumentError(503, "parse_failed")
    output = _Output()
    reader = Thread(target=_read, args=(process.stdout, output), name="pdf-output")
    writer = Thread(target=_write, args=(process.stdin, data), name="pdf-input")
    deadline = monotonic() + pdf_protocol.TIMEOUT_SECONDS
    monitor = psutil.Process(process.pid)
    children: dict[int, psutil.Process] = {}
    interval = Event()
    reader.start()
    writer.start()
    try:
        while True:
            if output.failed.is_set() or monotonic() >= deadline:
                raise DocumentError(422, "parse_failed")
            status = process.poll()
            if status is not None and output.done.is_set():
                return status, bytes(output.data)
            if _tree_rss(monitor, children) > pdf_protocol.MAX_RSS_BYTES:
                raise DocumentError(422, "parse_failed")
            _ = interval.wait(pdf_protocol.POLL_SECONDS)
    finally:
        _collect_children(monitor, children)
        for child in reversed(tuple(children.values())):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                continue
        if process.poll() is None:
            process.kill()
        _ = process.wait()
        _, alive = psutil.wait_procs(tuple(children.values()), timeout=3)
        writer.join()
        reader.join()
        if alive:
            raise DocumentError(503, "parse_failed")
