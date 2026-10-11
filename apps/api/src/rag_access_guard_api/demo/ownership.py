"""Read-only target identity checks before any migration or setup mutation."""

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from rag_access_guard_api.config import is_loopback
from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.files import load_private_json

__all__ = ["DemoOwnershipError", "validate_owned_target", "validate_target_locator"]


def validate_owned_target(config: DemoConfig, marker: DemoMarker | None) -> None:
    """Check the configured local target and its existing ownership marker."""
    target = validate_target_locator(config)
    if marker is None:
        raise DemoOwnershipError(code="marker_absent")
    if marker != load_private_json(config.ownership_manifest, DemoMarker):
        raise DemoOwnershipError(code="marker_mismatch")
    if (marker.dataset_id, marker.database_name, marker.database_role) != (
        config.dataset_id,
        target.database,
        target.username,
    ):
        raise DemoOwnershipError(code="marker_mismatch")


def validate_target_locator(config: DemoConfig) -> URL:
    """Reject unsafe URL identities before opening even a read-only connection."""
    try:
        target = make_url(config.database_url.get_secret_value())
    except (ArgumentError, ValueError):
        raise DemoOwnershipError(code="invalid_database_url") from None
    if (
        target.drivername != "postgresql+psycopg"
        or target.host is None
        or not is_loopback(target.host)
        or target.database != "rag_access_guard_demo"
        or target.username is None
        or target.username in {"postgres", "rag_guard"}
        or bool(target.query)
    ):
        raise DemoOwnershipError(code="unsafe_target")
    return target
