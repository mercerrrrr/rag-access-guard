"""Safe model failures shared by the port and infrastructure adapters."""


class GenerationUnavailable(Exception):  # noqa: N818 -- generation port contract.
    """No usable output; deliberately carries no prompt or server response."""
