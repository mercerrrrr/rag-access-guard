"""Native worker lifecycle fixture; never evidence of embedding quality."""

import json
import os
import subprocess
import sys
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

import anyio

from rag_access_guard_api.adapters import e5_process
from rag_access_guard_api.adapters.embeddings import IsolatedE5EmbeddingAdapter
from rag_access_guard_api.adapters.ollama_supervisor import ProcessIdentity
from rag_access_guard_api.schemas.embedding_work import EmbeddingWorkRequest


def wait_release(directory: Path) -> None:
    deadline = monotonic() + 120
    while not (directory / "release").exists():
        if monotonic() >= deadline:
            raise TimeoutError
        _ = Event().wait(0.02)


def _parent(directory: Path, mode: str) -> None:
    def command() -> tuple[str, ...]:
        return (
            sys.executable,
            str(Path(__file__).resolve()),
            str(directory),
            "descendant" if mode == "parent" else "success",
        )

    e5_process.worker_command = command
    if mode == "parent-boundary":
        read_identity = ProcessIdentity.read

        def capture(pid: int) -> ProcessIdentity:
            identity = read_identity(pid)
            _ = (directory / "created.json").write_text(identity.model_dump_json())
            wait_release(directory)
            return identity

        ProcessIdentity.read = capture
    adapter = IsolatedE5EmbeddingAdapter(directory, retain_model=False)
    _ = anyio.run(adapter.embed_query, "CONTROLLED")
    _ = sys.stdout.write(json.dumps({"torch_in_parent": "torch" in sys.modules}) + "\n")


def _vectors(count: int, mode: str) -> list[list[float]]:
    vectors = [[float(column == row) for column in range(384)] for row in range(count)]
    if mode == "bad-count":
        vectors = vectors[:-1]
    if mode == "bad-dimension":
        vectors[0] = vectors[0][:-1]
    if mode == "bad-norm":
        vectors[0] = [0.0] * 384
    if mode == "nonfinite":
        vectors[0][0] = float("nan")
    return vectors


def main() -> None:
    directory, mode = Path(sys.argv[1]), sys.argv[2]
    if mode == "descendant-child":
        _ = (directory / "descendant.json").write_text(
            ProcessIdentity.read(os.getpid()).model_dump_json(), encoding="utf-8"
        )
        wait_release(directory)
        return
    if mode in {"parent", "parent-success", "parent-boundary"}:
        _parent(directory, mode)
        return

    if mode == "blocked-input":
        _ = (directory / "blocked").touch()
        wait_release(directory)

    request = EmbeddingWorkRequest.model_validate_json(sys.stdin.buffer.read())
    _ = (directory / "root.json").write_text(
        ProcessIdentity.read(os.getpid()).model_dump_json(), encoding="utf-8"
    )
    with (directory / "batches.jsonl").open("a", encoding="utf-8") as stream:
        _ = stream.write(json.dumps({"count": len(request.texts), "kind": request.kind}) + "\n")
    if mode == "exit":
        raise SystemExit(3)
    if mode == "descendant":
        _ = subprocess.Popen(  # noqa: S603 -- fixed native fixture descendant.
            [sys.executable, str(Path(__file__).resolve()), str(directory), "descendant-child"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    if mode in {"hold", "descendant"}:
        wait_release(directory)
    if mode == "oversize":
        _ = sys.stdout.buffer.write(b"x" * (1024**2 + 1))
        _ = sys.stdout.buffer.flush()
        wait_release(directory)
        return
    if mode == "invalid-json":
        _ = sys.stdout.write("{")
        return
    _ = sys.stdout.write(
        json.dumps(
            {
                "nonce": str(uuid4() if mode == "wrong-nonce" else request.nonce),
                "vectors": _vectors(len(request.texts), mode),
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
