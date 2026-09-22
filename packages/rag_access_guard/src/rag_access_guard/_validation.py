"""Primitive checks at the host-to-package boundary."""

import re


def require_nonnegative(value: int) -> None:
    """Reject invalid counters without coercing their values."""
    if type(value) is not int or value < 0:
        msg = "Expected a nonnegative integer"
        raise ValueError(msg)


def require_sha256(value: str) -> None:
    """Require the canonical lowercase SHA-256 representation."""
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        msg = "Expected a lowercase SHA-256 digest"
        raise ValueError(msg)


def require_tuple(value: object) -> None:
    """Prevent mutable collections inside frozen public values."""
    if not isinstance(value, tuple):
        msg = "Expected an immutable tuple"
        raise TypeError(msg)
