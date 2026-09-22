from datetime import UTC, datetime, timedelta

import pytest

from rag_access_guard_api.services.security import session_is_current


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("boundary", ["idle", "absolute"])
def test_session_clock_boundaries(offset: int, boundary: str) -> None:
    # Given exact independently chosen absolute or idle boundaries.
    cutoff = datetime(2026, 9, 22, 12, tzinfo=UTC)
    last_seen = cutoff - timedelta(minutes=30 if boundary == "idle" else 1)
    absolute = cutoff if boundary == "absolute" else cutoff + timedelta(hours=1)
    # When the clock crosses by one microsecond.
    current = session_is_current(cutoff + timedelta(microseconds=offset), last_seen, absolute)
    # Then equality already denies access.
    assert current is (offset < 0)
