"""Pinned text-only evidence-selection profile for the local demonstration."""

from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict

EXTRACTIVE_TOKENIZER_SHA256: Final = (
    "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"
)
EXTRACTIVE_TEMPLATE_SHA256: Final = (
    "b507b9c2f6ca642bffcd06665ea7c91f235fd32daeefdf875a0f938db05fb315"
)


EXTRACTIVE_OUTPUT_LIMIT: Final = 512


class ExtractiveManifest(BaseModel):
    """Bind the new answer contract without changing any existing generation bundle."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)
    model_name: Literal["qwen3.5:2b-q4_K_M"] = "qwen3.5:2b-q4_K_M"
    model_digest: Literal["1e2d21a4f03ab9e4a9c1976a4b7e88f21de61d65f2c5485d8fde8942be73f1e7"] = (
        "1e2d21a4f03ab9e4a9c1976a4b7e88f21de61d65f2c5485d8fde8942be73f1e7"
    )
    tokenizer_id: Literal["Qwen/Qwen3.5-2B"] = "Qwen/Qwen3.5-2B"
    tokenizer_revision: Literal["15852e8c16360a2fea060d615a32b45270f8a8fc"] = (
        "15852e8c16360a2fea060d615a32b45270f8a8fc"
    )
    context_window: Literal[4096] = 4096
    max_output_tokens: Literal[512] = EXTRACTIVE_OUTPUT_LIMIT
    prompt_revision: Literal["rag-excerpts-ru-v1"] = "rag-excerpts-ru-v1"


class TextExtractiveManifest(BaseModel):
    """Pin the derived text artifact separately from the original multimodal model."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)
    model_name: Literal["rag-qwen35-text:2b-q4_k_m"] = "rag-qwen35-text:2b-q4_k_m"
    model_digest: Literal["84e2c930d4ab82f0e2ec10fc0f7497b59085db8304d6c2f979cbafc4009e5c1c"] = (
        "84e2c930d4ab82f0e2ec10fc0f7497b59085db8304d6c2f979cbafc4009e5c1c"
    )
    tokenizer_id: Literal["Qwen/Qwen3.5-2B"] = "Qwen/Qwen3.5-2B"
    tokenizer_revision: Literal["15852e8c16360a2fea060d615a32b45270f8a8fc"] = (
        "15852e8c16360a2fea060d615a32b45270f8a8fc"
    )
    context_window: Literal[4096] = 4096
    max_output_tokens: Literal[512] = EXTRACTIVE_OUTPUT_LIMIT
    prompt_revision: Literal["rag-excerpts-ru-v1"] = "rag-excerpts-ru-v1"
