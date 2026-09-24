from pathlib import Path

import anyio
import pytest

from rag_access_guard_api.adapters.embeddings import E5EmbeddingAdapter, EmbeddingError


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_model_fails_closed(tmp_path: Path, *, corrupt: bool) -> None:
    if corrupt:
        _ = (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    async def run() -> None:
        with pytest.raises(EmbeddingError):
            _ = await E5EmbeddingAdapter(tmp_path).embed_passages(("Synthetic document",))

    anyio.run(run)
