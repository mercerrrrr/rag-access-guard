import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rag_access_guard_api.schemas import relevance_config
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.schemas.relevance_config import RelevanceConfig, load_relevance_config


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"{}",
        b'{"model_path":"relative"}',
        b" " * (relevance_config.CONFIG_BYTE_LIMIT + 1),
    ],
)
def test_invalid_config_fails_closed_without_exposing_local_details(
    tmp_path: Path, raw: bytes
) -> None:
    path = tmp_path / "private-relevance.json"
    _ = path.write_bytes(raw)
    with pytest.raises(GenerationUnavailable) as error:
        _ = load_relevance_config(path)
    assert str(path) not in str(error.value)


def test_model_identity_is_pinned_and_unknown_rules_are_rejected(tmp_path: Path) -> None:
    config = RelevanceConfig(model_path=tmp_path)
    for replacement in ({"revision": "main"}, {"reject_below": -100}, {"runtime": "other"}):
        with pytest.raises(ValidationError):
            _ = RelevanceConfig.model_validate_json(
                json.dumps(config.model_dump(mode="json") | replacement)
            )


def test_identity_excludes_machine_path_but_binds_routing_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = RelevanceConfig(model_path=tmp_path / "first")
    second = RelevanceConfig(model_path=tmp_path / "second")
    assert first.computed_hash() == second.computed_hash()
    original = first.computed_hash()
    monkeypatch.setattr(relevance_config, "REJECT_BELOW", -1.0)
    assert first.computed_hash() != original


def test_unverified_artifact_bytes_cannot_load_the_model(tmp_path: Path) -> None:
    config = RelevanceConfig(model_path=tmp_path)
    (tmp_path / "onnx").mkdir()
    _ = (tmp_path / "onnx/model_quint8_avx2.onnx").write_bytes(b"untrusted")
    with pytest.raises(GenerationUnavailable):
        relevance_config.verify_relevance_artifacts(config)
