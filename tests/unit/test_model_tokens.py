from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_access_guard_api.adapters.llm import LLMUnavailableError
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.services.model_manifest import ModelManifest


def test_tokenizer_rejects_tampered_local_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tokenizer.json"
    _ = path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH", str(path))
    with pytest.raises(LLMUnavailableError):
        _ = ModelTokenCounter(ModelManifest())


def test_tokenizer_handles_cyrillic_emoji_and_json_escapes() -> None:
    counter = ModelTokenCounter(ModelManifest())
    question = 'Привет 👋 "тест"\n\\'
    context = '{"text":"Документ 📄"}'
    assert counter.count(question) == 10
    assert counter.count(context) == 9
    assert counter.count_request(user_input=question, system_supplied_context=context) > 100


def test_manifest_rejects_unverified_identity() -> None:
    with pytest.raises(ValidationError):
        _ = ModelManifest.model_validate({"model_digest": "0" * 64})


def test_tokenizer_identity_changes_with_request_budget() -> None:
    ordinary = ModelTokenCounter(ModelManifest())
    small = ModelTokenCounter(ModelManifest(context_window=2048))
    assert ordinary.identity != small.identity
