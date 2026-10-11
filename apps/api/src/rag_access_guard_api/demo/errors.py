"""Safe setup errors never carry credentialed URLs or private input content."""

from typing import override


class DemoOwnershipError(Exception):
    """Safe code; exception tracebacks remain mutable for context-manager propagation."""

    def __init__(self, *, code: str) -> None:
        """Carry only a non-secret error code through transaction boundaries."""
        super().__init__(code)
        self.code: str = code

    @override
    def __str__(self) -> str:
        return self.code
