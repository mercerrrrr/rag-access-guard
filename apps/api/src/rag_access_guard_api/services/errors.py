"""Content-free authentication failures suitable for HTTP boundaries."""


class UnauthenticatedError(Exception):
    """No currently valid server session or credentials."""


class ForbiddenError(Exception):
    """Origin or synchronizer token check failed."""


class AlreadyAuthenticatedError(Exception):
    """Account switching requires an explicit logout."""


class RateLimitedError(Exception):
    """A bounded authentication operation exhausted its allowance."""

    def __init__(self, retry_after: int) -> None:
        """Retain only the public retry delay."""
        super().__init__("Too many requests")
        self.retry_after: int = retry_after
