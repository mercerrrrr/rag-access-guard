"""Contained worker fixture; checks transport and lifecycle, never model quality."""

import json
import os
import sys
from pathlib import Path
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING
from uuid import uuid4

from rag_access_guard_api.adapters.ollama_supervisor import ProcessIdentity
from rag_access_guard_api.schemas.relevance_work import RelevanceWorkRequest

if TYPE_CHECKING:
    from pydantic import JsonValue


def main() -> None:
    """Emit one controllable nonce-bound response without loading a model."""
    directory, mode = Path(sys.argv[1]), sys.argv[2]
    request = RelevanceWorkRequest.model_validate_json(sys.stdin.buffer.read())
    _ = (directory / "root.json").write_text(
        ProcessIdentity.read(os.getpid()).model_dump_json(), encoding="utf-8"
    )
    if mode == "hold":
        deadline = monotonic() + 20
        while not (directory / "release").exists():
            if monotonic() >= deadline:
                raise TimeoutError
            _ = Event().wait(0.01)
    if mode == "exit":
        raise SystemExit(3)
    if mode == "invalid-json":
        _ = sys.stdout.write("{")
        return
    scores: list[JsonValue] = [0.5 for _ in request.texts]
    if mode == "bad-count":
        scores.append(0.5)
    if mode == "nonfinite":
        scores[0] = float("nan")
    if mode == "text-score":
        scores[0] = "0.5"
    payload: dict[str, JsonValue] = {
        "kind": "relevance",
        "nonce": str(uuid4() if mode == "wrong-nonce" else request.nonce),
        "scores": scores,
    }
    if mode == "model-prose":
        payload["answer"] = "UNTRUSTED_PROSE"
    _ = sys.stdout.write(json.dumps(payload))


if __name__ == "__main__":
    main()
