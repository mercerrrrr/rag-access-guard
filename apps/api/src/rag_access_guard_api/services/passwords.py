"""Explicit Argon2id password policy; callers offload expensive operations."""

from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

_HASHER: Final = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4, salt_len=16, hash_len=32, type=Type.ID
)
MIN_PASSWORD_CHARACTERS: Final = 8
MAX_PASSWORD_BYTES: Final = 1024


def hash_password(password: str) -> str:
    """Hash an unmodified password satisfying the creation policy."""
    if (
        len(password) < MIN_PASSWORD_CHARACTERS
        or len(password.encode("utf-8")) > MAX_PASSWORD_BYTES
    ):
        message = "Password must have at least 8 characters and at most 1024 UTF-8 bytes"
        raise ValueError(message)
    return _HASHER.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    """Verify without normalizing or leaking mismatch details."""
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return False
    try:
        return _HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False
