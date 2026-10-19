"""Readiness verifies pinned bytes without allocating a second native tokenizer."""

from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest

from rag_access_guard_api.adapters import e5_artifacts
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.services import system_status


@pytest.mark.anyio
async def test_readiness_does_not_construct_tokenizer(monkeypatch: pytest.MonkeyPatch) -> None:
    checked = Mock()
    monkeypatch.setattr(system_status, "load_retrieval_config", Mock())
    monkeypatch.setattr(e5_artifacts, "E5TokenCounter", Mock(side_effect=AssertionError))
    monkeypatch.setattr(system_status, "verify_e5_files", checked, raising=False)
    readiness = system_status.SystemReadiness(Settings(llm_adapter="disabled"))
    assert (await readiness.snapshot()).search.state == "ready"
    checked.assert_called_once()


@pytest.mark.parametrize("damaged", [None, "config.json", "model.safetensors", "tokenizer.json"])
def test_file_probe_rechecks_every_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damaged: str | None,
) -> None:
    artifacts = tuple(
        (name, sha256(name.encode()).hexdigest())
        for name in (
            "config.json",
            "model.safetensors",
            "tokenizer.json",
        )
    )
    monkeypatch.setattr(e5_artifacts, "E5_ARTIFACTS", artifacts)
    monkeypatch.setattr(e5_artifacts, "E5TokenCounter", Mock(side_effect=AssertionError))
    for name, _ in artifacts:
        _ = (tmp_path / name).write_bytes(name.encode())
    e5_artifacts.verify_e5_files(tmp_path)
    if damaged is not None:
        _ = (tmp_path / damaged).write_bytes(b"changed")
        with pytest.raises(EmbeddingError):
            e5_artifacts.verify_e5_files(tmp_path)
