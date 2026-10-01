"""Optional local inference preflight; no download or substitute model is permitted."""

from experiments.comparison_config import ComparisonConfig
from rag_access_guard_api.adapters.ollama_http import create_client
from rag_access_guard_api.adapters.ollama_identity import verify_model
from rag_access_guard_api.config import Settings


async def verify_live(config: ComparisonConfig) -> None:
    """Check local tokenizer bytes and exact Ollama metadata before creating a trial."""
    _ = config.token_counter()
    if config.model_manifest is not None:
        async with create_client(Settings().ollama_base_url) as client:
            await verify_model(client, config.model_manifest)
