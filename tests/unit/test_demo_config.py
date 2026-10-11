from pathlib import Path

import pytest
from tests.unit.test_demo_ownership import config_for

from rag_access_guard_api.demo.files import load_config
from rag_access_guard_api.demo.ownership import DemoOwnershipError
from rag_access_guard_api.demo.private_write import exclusive_private_write, private_directory


def test_config_rejects_duplicate_members_before_any_write(tmp_path: Path) -> None:
    # Given: duplicate identities in a private JSON boundary.
    path = private_directory(tmp_path / "configuration") / "config.json"
    exclusive_private_write(path, b'{"dataset_id":"one","dataset_id":"two"}')
    # When/Then: parsing fails with a safe code rather than exposing inputs.
    with pytest.raises(DemoOwnershipError, match="invalid_config"):
        _ = load_config(path)


def test_config_secret_is_redacted_in_repr_and_serialization(tmp_path: Path) -> None:
    # Given: a typed shared configuration containing a credentialed URL.
    config = config_for(tmp_path)
    # When: ordinary diagnostics serialize it.
    rendered = repr(config) + config.model_dump_json()
    # Then: the URL secret is absent.
    assert "PRIVATE" not in rendered
