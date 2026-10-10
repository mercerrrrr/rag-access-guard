import os
import signal
import sys
from contextlib import suppress
from pathlib import Path
from threading import Event
from threading import enumerate as enumerate_threads
from time import monotonic
from typing import ClassVar

import psutil
import pytest
from pydantic import BaseModel, ConfigDict

from rag_access_guard_api.adapters import docx_process, docx_protocol
from rag_access_guard_api.services.text_documents import DocumentError

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX owned-session cleanup")


class Witness(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)
    child_pid: int
    root_pid: int
    group: int
    session: int
    created: float


def _kill_owned_child(witness: Witness) -> None:
    with suppress(psutil.NoSuchProcess, ProcessLookupError):
        child = psutil.Process(witness.child_pid)
        if (
            child.create_time() == witness.created
            and os.getpgid(child.pid) == witness.group
            and os.getsid(child.pid) == witness.session
        ):
            os.killpg(witness.group, signal.SIGKILL)


@pytest.mark.parametrize("inherited_stdout", [True, False], ids=["held-output", "early-eof"])
def test_owned_group_disappears_when_leader_exits_before_sampling(
    *, inherited_stdout: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    witness_path = tmp_path / "child.json"
    release = tmp_path / "release"
    child_code = "".join(
        (
            "import json,os,psutil,time; from pathlib import Path; ",
            "w=dict(child_pid=os.getpid(),root_pid=os.getppid(),group=os.getpgrp(),",
            "session=os.getsid(0),created=psutil.Process().create_time()); ",
            f"target=Path({str(witness_path)!r}); partial=target.with_suffix('.partial'); ",
            "partial.write_text(json.dumps(w)); partial.replace(target); ",
            "" if inherited_stdout else "os.close(1); ",
            "time.sleep(60)",
        )
    )
    root_code = "".join(
        (
            "import subprocess,sys,time; from pathlib import Path; ",
            f"subprocess.Popen([sys.executable,'-I','-c',{child_code!r}]); ",
            f"gate=Path({str(release)!r}); ",
            "exec('while not gate.exists(): time.sleep(0.001)')",
        )
    )
    witnesses: list[Witness] = []
    leaders: list[int] = []
    exited: list[bool] = []
    identities: list[bool] = []
    original = psutil.Process.children

    def sample_after_exit(
        monitor: psutil.Process, *, recursive: bool = False
    ) -> list[psutil.Process]:
        if not witnesses:
            deadline = monotonic() + 3
            while not witness_path.exists() and monotonic() < deadline:
                _ = Event().wait(0.001)
            witness = Witness.model_validate_json(witness_path.read_bytes())
            witnesses.append(witness)
            leaders.append(monitor.pid)
            child = psutil.Process(witness.child_pid)
            identities.append(
                child.create_time() == witness.created
                and child.ppid() == monitor.pid
                and os.getpgid(child.pid) == witness.group
                and os.getsid(child.pid) == witness.session
            )
            release.touch()
            while monitor.status() != psutil.STATUS_ZOMBIE and monotonic() < deadline:
                _ = Event().wait(0.001)
            exited.append(monitor.status() == psutil.STATUS_ZOMBIE)
        return original(monitor, recursive=recursive)

    monkeypatch.setattr(
        docx_process, "_worker_command", lambda: (sys.executable, "-I", "-c", root_code)
    )
    monkeypatch.setattr(psutil.Process, "children", sample_after_exit)
    monkeypatch.setattr(docx_protocol, "TIMEOUT_SECONDS", 1)
    began = monotonic()
    try:
        if inherited_stdout:
            with pytest.raises(DocumentError) as error:
                _ = docx_process.run_worker(b"")
            assert error.value.status == 422
        else:
            assert docx_process.run_worker(b"") == (0, b"")
        assert witnesses
        witness = witnesses[0]
        assert leaders == [witness.root_pid]
        assert exited == [True]
        assert identities == [True]
        assert witness.group == witness.root_pid == witness.session
        assert not psutil.pid_exists(witness.child_pid)
        with pytest.raises(ProcessLookupError):
            os.killpg(witness.group, 0)
        assert not any(
            thread.name in ("docx-input", "docx-output") for thread in enumerate_threads()
        )
        assert monotonic() - began < 2
    finally:
        if witnesses:
            _kill_owned_child(witnesses[0])
