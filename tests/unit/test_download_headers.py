from dataclasses import FrozenInstanceError

import pytest

from rag_access_guard_api.schemas.sources import OriginalContent


def test_original_value_is_immutable_and_does_not_repr_bytes() -> None:
    original = OriginalContent(b"SYNTHETIC_PRIVATE", "text/plain", "server.txt")
    assert "SYNTHETIC_PRIVATE" not in repr(original)
    with pytest.raises(FrozenInstanceError):
        setattr(original, "data", b"replacement")  # noqa: B010 -- exercise frozen runtime boundary.
