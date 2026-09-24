"""Model-output validation shared by inference and persistence boundaries."""

import math
from typing import Final

EMBEDDING_DIMENSION: Final = 384
NORM_TOLERANCE: Final = 1e-5


class EmbeddingError(Exception):
    """Reject invalid model output without including source text."""


class VersionActivationConflictError(Exception):
    """A newer version was activated while this version was being computed."""


def validate_vectors(vectors: tuple[tuple[float, ...], ...], count: int) -> None:
    """Reject the complete batch when any vector violates its storage contract."""
    if count < 1 or len(vectors) != count:
        raise EmbeddingError
    for vector in vectors:
        if (
            len(vector) != EMBEDDING_DIMENSION
            or not all(math.isfinite(value) for value in vector)
            or abs(math.hypot(*vector) - 1.0) > NORM_TOLERANCE
        ):
            raise EmbeddingError
