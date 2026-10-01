"""Monotonic stage measurements, including interrupted operations."""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter


@dataclass
class StageClock:
    """Keep stage intervals disjoint from the total logical request duration."""

    logical_started: float
    values: list[tuple[str, float]] = field(default_factory=list)

    @contextmanager
    def measure(self, stage: str) -> Generator[None]:
        """Retain elapsed milliseconds even when the measured call raises."""
        start = perf_counter()
        try:
            yield
        finally:
            self.values.append((stage, (perf_counter() - start) * 1000))
