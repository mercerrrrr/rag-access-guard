"""OS-owned exclusive locks automatically release after process death."""

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_run_owner(output: Path) -> Generator[None]:
    """Deny another active owner without treating a stale PID file as authority."""
    with (output / "owner.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415 — Windows-only standard-library binding.

            _ = stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                message = "active_run_owner"
                raise ValueError(message) from None
            try:
                yield
            finally:
                _ = stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 — POSIX-only standard-library binding.

            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                message = "active_run_owner"
                raise ValueError(message) from None
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
