import sys
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import NamedTuple, override

import psutil
import pytest
from tests.helpers.docx_factory import DOCX_MIME, paragraph_table_docx

from rag_access_guard_api.adapters import docx_process, docx_protocol
from rag_access_guard_api.adapters.docx_parser import parse_docx
from rag_access_guard_api.adapters.ollama_job import WindowsJob
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.text_documents import DocumentError


def test_worker_exit_before_buffered_input_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    output_closed = Event()
    input_waited = Event()

    class OrderedThread(Thread):
        @override
        def run(self) -> None:
            if self.name == "docx-input":
                assert output_closed.wait(timeout=5)
                input_waited.set()
            try:
                super().run()
            finally:
                if self.name == "docx-output":
                    output_closed.set()

    command = (
        sys.executable,
        "-I",
        "-c",
        "import os,sys; os.close(0); os.close(1); sys.exit(17)",
    )
    monkeypatch.setattr(docx_process, "_worker_command", lambda: command)
    monkeypatch.setattr(docx_process, "Thread", OrderedThread)
    assert docx_process.run_worker(b"PK\x03\x04small-input") == (17, b"")
    assert input_waited.is_set()


@pytest.mark.parametrize("case", ["timeout", "output", "memory", "crash", "no-read"])
def test_worker_failure_reaps_process(
    case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_file = tmp_path / "pid.txt"
    script = (
        "import os,sys,time; from pathlib import Path; "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); "
    )
    scripts = {
        "timeout": "time.sleep(60)",
        "output": (
            "sys.stdout.buffer.write(b'x' * 1048576); sys.stdout.buffer.flush(); time.sleep(60)"
        ),
        "memory": "data=bytearray(128*1024*1024); time.sleep(60)",
        "crash": "sys.stderr.write('PRIVATE_PARSER_DIAGNOSTIC'); sys.exit(17)",
        "no-read": "time.sleep(60)",
    }
    command = (sys.executable, "-I", "-c", script + scripts[case])
    monkeypatch.setattr(docx_process, "_worker_command", lambda: command)
    monkeypatch.setattr(docx_protocol, "TIMEOUT_SECONDS", 2)
    if case == "output":
        monkeypatch.setattr(docx_protocol, "MAX_RESPONSE_BYTES", 65536)
    if case == "memory":
        monkeypatch.setattr(docx_protocol, "MAX_RSS_BYTES", 64 * 1024 * 1024)
    data = (
        b"PK\x03\x04" + b"x" * (8 * 1024 * 1024)
        if case == "no-read"
        else paragraph_table_docx("Safe", (), "")
    )
    with pytest.raises(DocumentError) as error:
        _ = parse_docx(UploadPayload(filename="a.docx", media_type=DOCX_MIME, data=data))
    assert error.value.status == (503 if case == "crash" else 422)
    assert not psutil.pid_exists(int(pid_file.read_text()))
    assert str(error.value) == ""


def test_descendant_is_reaped_after_root_exits_before_monitoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid = tmp_path / "child.txt"
    child = "import time; time.sleep(2)"
    script = (
        "import subprocess,sys; from pathlib import Path; "
        f"p=subprocess.Popen([sys.executable,'-I','-c',{child!r}]); "
        f"Path({str(child_pid)!r}).write_text(str(p.pid))"
    )
    command = (sys.executable, "-I", "-c", script)
    original = psutil.Process.children

    def delayed_sample(monitor: psutil.Process, *, recursive: bool = False) -> list[psutil.Process]:
        _ = monitor.wait(timeout=3)
        return original(monitor, recursive=recursive)

    monkeypatch.setattr(docx_process, "_worker_command", lambda: command)
    monkeypatch.setattr(psutil.Process, "children", delayed_sample)
    monkeypatch.setattr(docx_protocol, "TIMEOUT_SECONDS", 0.2)
    start = monotonic()
    with pytest.raises(DocumentError):
        _ = docx_process.run_worker(b"")
    assert monotonic() - start < 1
    assert not psutil.pid_exists(int(child_pid.read_text()))


def test_job_root_membership_is_counted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    class Memory(NamedTuple):
        rss: int

    roots: list[int] = []

    def memory(process: psutil.Process) -> Memory:
        return Memory(1000 if roots and process.pid == roots[0] else 0)

    def collect(monitor: psutil.Process, children: dict[int, psutil.Process]) -> None:
        roots[:] = [monitor.pid]
        children[monitor.pid] = monitor

    monkeypatch.setattr(psutil.Process, "memory_info", memory)
    monkeypatch.setattr(docx_process, "_collect_children", collect)
    monkeypatch.setattr(docx_protocol, "MAX_RSS_BYTES", 1500)
    monkeypatch.setattr(
        docx_process,
        "_worker_command",
        lambda: (sys.executable, "-I", "-c", "import time; time.sleep(0.2)"),
    )
    assert docx_process.run_worker(b"") == (0, b"")


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows job monitor failure cleanup")
def test_monitor_construction_failure_waits_for_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid = tmp_path / "orphan.txt"
    child = "import time; time.sleep(2)"
    script = (
        "import subprocess,sys; from pathlib import Path; "
        f"p=subprocess.Popen([sys.executable,'-I','-c',{child!r}]); "
        f"Path({str(child_pid)!r}).write_text(str(p.pid))"
    )
    original = psutil.Process
    resumed = False
    injected = False

    class FailingMonitor(original):
        @override
        def __init__(self, pid: int | None = None) -> None:
            nonlocal injected
            super().__init__(pid)
            if resumed and not injected:
                injected = True
                _ = self.wait(timeout=3)
                raise psutil.NoSuchProcess(self.pid)

        @override
        def resume(self) -> None:
            nonlocal resumed
            super().resume()
            resumed = True

    monkeypatch.setattr(psutil, "Process", FailingMonitor)
    monkeypatch.setattr(
        docx_process, "_worker_command", lambda: (sys.executable, "-I", "-c", script)
    )
    with pytest.raises(DocumentError) as error:
        _ = docx_process.run_worker(b"")
    assert error.value.status == 503
    assert injected
    assert child_pid.exists()
    assert not psutil.pid_exists(int(child_pid.read_text()))


@pytest.mark.skipif(sys.platform != "win32", reason="native suspended Windows containment")
@pytest.mark.parametrize("fault", ["assign", "resume", "enumerate", "terminate"])
def test_native_containment_failure_reaps_root(fault: str, monkeypatch: pytest.MonkeyPatch) -> None:
    pids: list[int] = []

    class FailingJob(WindowsJob):
        @override
        def assign(self, pid: int) -> None:
            pids.append(pid)
            super().assign(pid)
            if fault == "assign":
                raise OSError

        @override
        def pids(self) -> tuple[int, ...]:
            if fault == "enumerate":
                raise OSError
            return super().pids()

        @override
        def terminate(self) -> None:
            if fault == "terminate":
                raise OSError
            super().terminate()

    def resume_failure(_process: psutil.Process) -> None:
        raise OSError

    monkeypatch.setattr(docx_process, "WindowsJob", FailingJob)
    monkeypatch.setattr(
        docx_process,
        "_worker_command",
        lambda: (sys.executable, "-I", "-c", "import time; time.sleep(2)"),
    )
    monkeypatch.setattr(docx_protocol, "TIMEOUT_SECONDS", 0.2)
    if fault == "resume":
        monkeypatch.setattr(psutil.Process, "resume", resume_failure)
    with pytest.raises(DocumentError) as error:
        _ = docx_process.run_worker(b"")
    assert error.value.status == 503
    assert pids
    assert not any(psutil.pid_exists(pid) for pid in pids)


def test_worker_does_not_inherit_database_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", "PRIVATE_SENTINEL")
    script = "import os,sys\nkey='RAG_ACCESS_GUARD_DATABASE_URL'\n"
    script += "sys.stdout.buffer.write(os.environ.get(key,'safe').encode())"
    command = (
        sys.executable,
        "-I",
        "-c",
        script,
    )
    monkeypatch.setattr(docx_process, "_worker_command", lambda: command)
    assert docx_process.run_worker(b"") == (0, b"safe")


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows job failure cleanup")
@pytest.mark.parametrize("fault", ["enumerate", "terminate"])
def test_native_cleanup_failure_waits_for_descendant(
    fault: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_pid = tmp_path / "descendant.txt"
    root_pids: list[int] = []
    child_script = (
        "from pathlib import Path; import os,time; "
        f"Path({str(child_pid)!r}).write_text(str(os.getpid())); time.sleep(2)"
    )
    script = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-I','-c',{child_script!r}]); time.sleep(2)"
    )

    class FailingJob(WindowsJob):
        @override
        def assign(self, pid: int) -> None:
            root_pids.append(pid)
            super().assign(pid)

        @override
        def pids(self) -> tuple[int, ...]:
            deadline = monotonic() + 1
            while not child_pid.exists() and monotonic() < deadline:
                _ = Event().wait(0.01)
            if fault == "enumerate":
                raise OSError
            return super().pids()

        @override
        def terminate(self) -> None:
            if fault == "terminate":
                raise OSError
            super().terminate()

    monkeypatch.setattr(docx_process, "WindowsJob", FailingJob)
    monkeypatch.setattr(
        docx_process, "_worker_command", lambda: (sys.executable, "-I", "-c", script)
    )
    monkeypatch.setattr(docx_protocol, "TIMEOUT_SECONDS", 0.3)
    with pytest.raises(DocumentError) as error:
        _ = docx_process.run_worker(b"")
    assert error.value.status == 503
    assert child_pid.exists()
    assert not psutil.pid_exists(int(child_pid.read_text()))
    assert not any(psutil.pid_exists(pid) for pid in root_pids)
