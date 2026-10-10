import sys
from threading import Thread
from threading import enumerate as enumerate_threads
from typing import NoReturn, override

import psutil
import pytest

from rag_access_guard_api.adapters import docx_process
from rag_access_guard_api.services.text_documents import DocumentError


@pytest.mark.parametrize("failed_thread", ["docx-output", "docx-input"])
def test_thread_start_failure_returns_503_after_actual_cleanup(
    failed_thread: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pids: list[int] = []
    original = psutil.Process

    class RecordingProcess(original):
        @override
        def __init__(self, pid: int | None = None) -> None:
            super().__init__(pid)
            pids.append(self.pid)

    class FailingThread(Thread):
        @override
        def start(self) -> None:
            if self.name == failed_thread:
                message = "PRIVATE_THREAD_FAILURE"
                raise RuntimeError(message)
            super().start()

    monkeypatch.setattr(psutil, "Process", RecordingProcess)
    monkeypatch.setattr(docx_process, "Thread", FailingThread)
    monkeypatch.setattr(
        docx_process,
        "_worker_command",
        lambda: (sys.executable, "-I", "-c", "import time; time.sleep(30)"),
    )
    with pytest.raises(DocumentError) as error:
        _ = docx_process.run_worker(b"")
    assert (error.value.status, error.value.code) == (503, "parse_failed")
    assert str(error.value) == ""
    assert pids
    assert not any(psutil.pid_exists(pid) for pid in pids)
    assert not any(thread.name in ("docx-input", "docx-output") for thread in enumerate_threads())


def test_unrelated_runtime_error_is_not_reclassified(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_memory(_process: psutil.Process) -> NoReturn:
        message = "UNRELATED_RUNTIME_BUG"
        raise RuntimeError(message)

    monkeypatch.setattr(psutil.Process, "memory_info", failing_memory)
    monkeypatch.setattr(
        docx_process,
        "_worker_command",
        lambda: (sys.executable, "-I", "-c", "import time; time.sleep(30)"),
    )
    with pytest.raises(RuntimeError, match="UNRELATED_RUNTIME_BUG"):
        _ = docx_process.run_worker(b"")
    assert not any(thread.name in ("docx-input", "docx-output") for thread in enumerate_threads())
