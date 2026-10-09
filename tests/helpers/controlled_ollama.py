"""Real loopback process fixture, not an Ollama model-quality substitute."""

import argparse
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Timer
from time import monotonic
from typing import ClassVar, override

import psutil
from pydantic import JsonValue

from rag_access_guard_api.services.model_manifest import (
    MODEL_DIGEST,
    OLLAMA_VERSION,
    TOKENIZER_REVISION,
)


class Arguments(argparse.Namespace):
    directory: Path = Path()
    role: str = "root"


def record(directory: Path, event: str) -> None:
    process = psutil.Process()
    with (directory / "trace.jsonl").open("a", encoding="utf-8") as trace:
        _ = trace.write(
            json.dumps(
                {
                    "event": event,
                    "pid": process.pid,
                    "created": process.create_time(),
                    "executable": process.exe(),
                }
            )
            + "\n"
        )


def child(directory: Path, role: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 -- fixed controlled fixture, no shell.
        [sys.executable, str(Path(__file__).resolve()), str(directory), "--role", role],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )


class Handler(BaseHTTPRequestHandler):
    directory: ClassVar[Path]

    def respond(self, payload: dict[str, JsonValue]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        _ = self.wfile.write(raw)

    def do_GET(self) -> None:
        record(self.directory, self.path)
        if self.path == "/api/version":
            self.respond({"version": OLLAMA_VERSION})
        elif self.path == "/api/tags":
            self.respond({"models": [{"name": "qwen3:4b", "digest": MODEL_DIGEST}]})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        _ = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        record(self.directory, self.path)
        if self.path == "/api/show":
            template = Path(
                os.environ.get(
                    "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
                    f".cache/qwen3/{TOKENIZER_REVISION}/tokenizer.json",
                )
            ).with_name("template.txt")
            self.respond(
                {
                    "model_info": {
                        "general.architecture": "qwen3",
                        "qwen3.context_length": 262144,
                        "tokenizer.ggml.model": "gpt2",
                        "tokenizer.ggml.pre": "qwen2",
                        "tokenizer.ggml.add_bos_token": False,
                        "tokenizer.ggml.eos_token_id": 151645,
                    },
                    "template": "mismatch"
                    if (self.directory / "profile-fault").exists()
                    else template.read_text(encoding="utf-8"),
                    "system": "",
                }
            )
        elif self.path == "/api/chat":
            (self.directory / "accepted").touch()
            deadline = monotonic() + 120
            interval = Event()
            while not (self.directory / "release").exists():
                if monotonic() >= deadline:
                    self.send_error(503)
                    return
                _ = interval.wait(0.01)
            self.respond(
                {
                    "done": True,
                    "done_reason": "stop",
                    "message": {"role": "assistant", "content": "CONTROLLED_ANSWER"},
                }
            )
        else:
            self.send_error(404)

    @override
    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> None:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("directory", type=Path)
    _ = parser.add_argument("--role", choices=("root", "runner", "grandchild"), default="root")
    arguments = parser.parse_args(namespace=Arguments())
    arguments.directory.mkdir(parents=True, exist_ok=True)
    record(arguments.directory, arguments.role)
    spawned: subprocess.Popen[bytes] | None = None
    if arguments.role != "grandchild":
        spawned = child(arguments.directory, "runner" if arguments.role == "root" else "grandchild")
    try:
        if arguments.role == "root":
            Handler.directory = arguments.directory
            with ThreadingHTTPServer(("127.0.0.1", 11435), Handler) as server:
                server.daemon_threads = True
                expiry = Timer(120, server.shutdown)
                expiry.daemon = True
                expiry.start()
                (arguments.directory / "listening").touch()
                server.serve_forever(poll_interval=0.05)
                expiry.cancel()
        else:
            (arguments.directory / arguments.role).touch()
            _ = Event().wait(120)
    finally:
        if spawned is not None:
            spawned.terminate()
            _ = spawned.wait(timeout=5)


if __name__ == "__main__":
    main()
