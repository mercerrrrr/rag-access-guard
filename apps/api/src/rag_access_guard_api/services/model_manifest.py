"""Frozen identity of the supported local model and its tokenization contract."""

from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

MODEL_DIGEST: Final = "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"
TOKENIZER_REVISION: Final = "768f209d9ea81521153ed38c47d515654e938aea"
TOKENIZER_SHA256: Final = "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"
TEMPLATE_SHA256: Final = "2d54db2b9bb29ce7db54fea63a891f5859603813c555b1f88b5e0994652897f9"
OLLAMA_VERSION: Final = "0.34.4"


class ModelManifest(BaseModel):
    """Only an audited model/template/tokenizer combination can enable generation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)
    model_name: Literal["qwen3:4b"] = "qwen3:4b"
    model_digest: Literal["359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"] = (
        MODEL_DIGEST
    )
    tokenizer_id: Literal["Qwen/Qwen3-4B-Thinking-2507"] = "Qwen/Qwen3-4B-Thinking-2507"
    tokenizer_revision: Literal["768f209d9ea81521153ed38c47d515654e938aea"] = TOKENIZER_REVISION
    context_window: Annotated[int, Field(ge=2048, le=32768)] = 8192
    max_output_tokens: Annotated[int, Field(ge=1, le=1024)] = 1024
    prompt_revision: Literal["rag-single-turn-v1"] = "rag-single-turn-v1"
