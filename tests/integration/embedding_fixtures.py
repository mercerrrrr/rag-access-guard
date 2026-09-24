from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeterministicEmbedder:
    model_id: str = "intfloat/multilingual-e5-small"
    revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    dimension: int = 384

    async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, *(0.0 for _ in range(383))) for _ in texts)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return (await self.embed_passages((text,)))[0]
