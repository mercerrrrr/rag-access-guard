import math

import pytest

from rag_access_guard_api.adapters.embeddings import EmbeddingError, validate_vectors


@pytest.mark.parametrize(
    "vectors",
    [
        (),
        ((1.0,) * 383,),
        ((1.0, *(0.0 for _ in range(383))),) * 2,
        ((math.nan, *(0.0 for _ in range(383))),),
        ((math.inf, *(0.0 for _ in range(383))),),
        ((0.0,) * 384,),
        ((2.0, *(0.0 for _ in range(383))),),
    ],
)
def test_invalid_embedding_batch_is_rejected(vectors: tuple[tuple[float, ...], ...]) -> None:
    with pytest.raises(EmbeddingError):
        validate_vectors(vectors, 1)


def test_complete_normalized_embedding_batch_is_accepted() -> None:
    validate_vectors(((1.0, *(0.0 for _ in range(383))),), 1)
