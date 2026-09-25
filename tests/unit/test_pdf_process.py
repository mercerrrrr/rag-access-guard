import json
import sys
from pathlib import Path
from threading import Event, Thread
from typing import override

import psutil
import pytest
from tests.helpers.pdf_factory import empty_pdf

from rag_access_guard_api.adapters import pdf_process, pdf_protocol
from rag_access_guard_api.adapters.pdf_parser import parse_pdf
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.text_documents import DocumentError


def test_worker_exit_before_buffered_input_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    output_closed = Event()

    class OrderedThread(Thread):
        @override
        def run(self) -> None:
            if self.name == "pdf-input":
                assert output_closed.wait(timeout=5)
            try:
                super().run()
            finally:
                if self.name == "pdf-output":
                    output_closed.set()

    command = (
        sys.executable,
        "-I",
        "-c",
        "import os,sys; os.close(0); os.close(1); sys.exit(17)",
    )
    monkeypatch.setattr(pdf_process, "_worker_command", lambda: command)
    monkeypatch.setattr(pdf_process, "Thread", OrderedThread)
    assert pdf_process.run_worker(b"%PDF-small-input") == (17, b"")


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
    monkeypatch.setattr(pdf_process, "_worker_command", lambda: command)
    monkeypatch.setattr(pdf_protocol, "TIMEOUT_SECONDS", 2)
    if case == "output":
        monkeypatch.setattr(pdf_protocol, "MAX_RESPONSE_BYTES", 65536)
    if case == "memory":
        monkeypatch.setattr(pdf_protocol, "MAX_RSS_BYTES", 64 * 1024 * 1024)
    data = b"%PDF-" + b"x" * (8 * 1024 * 1024) if case == "no-read" else empty_pdf()
    with pytest.raises(DocumentError) as error:
        _ = parse_pdf(UploadPayload(filename="a.pdf", media_type="application/pdf", data=data))
    assert error.value.status == (503 if case == "crash" else 422)
    assert not psutil.pid_exists(int(pid_file.read_text()))
    assert str(error.value) == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"parser_revision": "forged"},
        {"page_count": 201},
        {"page_count": True},
        {"empty_page_count": 2},
        {"unexpected": "private"},
        {"text": "\x00"},
        {"text": " "},
        {"text": "x\ry"},
        {"text": "x" * (8 * 1024 * 1024 + 1)},
    ],
    ids=[
        "revision",
        "pages",
        "boolean",
        "empty-count",
        "extra-field",
        "nul",
        "blank",
        "newlines",
        "text-limit",
    ],
)
def test_parent_revalidates_untrusted_worker_result(
    changes: dict[str, str | int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response: dict[str, str | int] = {
        "text": "Valid",
        "parser_revision": pdf_protocol.PDF_REVISION,
        "media_type": "application/pdf",
        "page_count": 1,
        "empty_page_count": 0,
    }
    response.update(changes)
    payload = json.dumps(response).encode()

    def forged_result(_data: bytes) -> tuple[int, bytes]:
        return 0, payload

    monkeypatch.setattr(pdf_process, "run_worker", forged_result)
    with pytest.raises(DocumentError) as error:
        _ = parse_pdf(
            UploadPayload(filename="a.pdf", media_type="application/pdf", data=empty_pdf())
        )
    assert error.value.status == 422


def test_worker_does_not_inherit_service_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_PDF_TEST_SECRET", "sensitive")
    command = (
        sys.executable,
        "-I",
        "-c",
        "import os,sys; sys.stdout.write(str('RAG_PDF_TEST_SECRET' in os.environ))",
    )
    monkeypatch.setattr(pdf_process, "_worker_command", lambda: command)
    assert pdf_process.run_worker(b"") == (0, b"False")


def test_rss_limit_is_not_confused_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    script = "import time; data=bytearray(128*1024*1024); time.sleep(3)"
    command = (sys.executable, "-I", "-c", script)
    monkeypatch.setattr(pdf_process, "_worker_command", lambda: command)
    monkeypatch.setattr(pdf_protocol, "MAX_RSS_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(pdf_protocol, "TIMEOUT_SECONDS", 20)
    with pytest.raises(DocumentError) as error:
        _ = pdf_process.run_worker(b"")
    assert error.value.status == 422
