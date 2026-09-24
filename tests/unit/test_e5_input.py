import anyio
import pytest
import torch
from tokenizers import Tokenizer

from rag_access_guard_api.adapters.e5_runtime import average_pool
from rag_access_guard_api.adapters.embeddings import EmbeddingError, get_embedding_adapter
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, get_tokenizer


def test_attention_mask_excludes_padding() -> None:
    hidden = torch.tensor([[[1.0, 3.0], [3.0, 5.0], [1000.0, 1000.0]]])
    result = average_pool(hidden, torch.tensor([[1, 1, 0]]))
    assert torch.equal(result, torch.tensor([[2.0, 4.0]]))


@pytest.mark.parametrize("text", ["Документ", "Document"])
def test_passage_and_query_prefixes_are_explicit(text: str) -> None:
    counter = get_tokenizer()
    native = Tokenizer.from_file(f".cache/e5/{MODEL_REVISION}/tokenizer.json")
    passage = counter.embedding_input_ids(text, kind="passage")
    query = counter.embedding_input_ids(text, kind="query")
    assert passage != query
    assert passage == tuple(native.encode("passage: " + text).ids)
    assert query == tuple(native.encode("query: " + text).ids)
    assert len(passage) == counter.embedding_input_tokens(text, kind="passage")


def test_input_513_tokens_is_rejected_without_truncation() -> None:
    question = " ".join(["test"] * 508)
    assert get_tokenizer().embedding_input_tokens(question, kind="query") == 513

    async def run() -> None:
        with pytest.raises(EmbeddingError):
            _ = await get_embedding_adapter().embed_query(question)

    anyio.run(run)
