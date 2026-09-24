import math

import anyio

from rag_access_guard_api.adapters.embeddings import get_embedding_adapter


def test_real_e5_russian_and_english_embeddings() -> None:
    async def run() -> None:
        adapter = get_embedding_adapter()
        passages = await adapter.embed_passages(
            ("Правила доступа к документам.", "Document access rules.")
        )
        query = await adapter.embed_query("Кто может читать документ?")
        assert len(passages) == 2
        for vector in (*passages, query):
            assert len(vector) == 384
            assert all(math.isfinite(value) for value in vector)
            assert abs(math.hypot(*vector) - 1.0) <= 1e-5
        assert passages[0] != passages[1]
        assert query != passages[0]

    anyio.run(run)
