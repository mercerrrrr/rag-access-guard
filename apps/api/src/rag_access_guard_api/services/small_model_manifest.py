"""Pinned small-model artifacts for the resource-constrained demonstration."""

from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict

SMALL_TOKENIZER_SHA256: Final = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"
SMALL_TEMPLATE_SHA256: Final = "eb4402837c7829a690fa845de4d7f3fd842c2adee476d5341da8a46ea9255175"


class SmallInstructManifest(BaseModel):
    """Keep weights, tokenizer and built-in system identity inseparable."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)
    model_name: Literal["qwen2.5:1.5b-instruct-q4_K_M"] = "qwen2.5:1.5b-instruct-q4_K_M"
    model_digest: Literal["65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b"] = (
        "65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b"
    )
    tokenizer_id: Literal["Qwen/Qwen2.5-1.5B-Instruct"] = "Qwen/Qwen2.5-1.5B-Instruct"
    tokenizer_revision: Literal["989aa7980e4cf806f80c7fef2b1adb7bc71aa306"] = (
        "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
    )
    model_system_sha256: Literal[
        "66b9ea09bd5b7099cbb4fc820f31b575c0366fa439b08245566692c6784e281e"
    ] = "66b9ea09bd5b7099cbb4fc820f31b575c0366fa439b08245566692c6784e281e"
    context_window: Literal[4096] = 4096
    max_output_tokens: Literal[512] = 512
    prompt_revision: Literal["rag-instruct-ru-origin-v2"] = "rag-instruct-ru-origin-v2"


class SmallExtractiveManifest(BaseModel):
    """Pin the extractive contract independently of the existing instruct manifest."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)
    model_name: Literal["qwen2.5:1.5b-instruct-q4_K_M"] = "qwen2.5:1.5b-instruct-q4_K_M"
    model_digest: Literal["65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b"] = (
        "65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b"
    )
    tokenizer_id: Literal["Qwen/Qwen2.5-1.5B-Instruct"] = "Qwen/Qwen2.5-1.5B-Instruct"
    tokenizer_revision: Literal["989aa7980e4cf806f80c7fef2b1adb7bc71aa306"] = (
        "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
    )
    model_system_sha256: Literal[
        "66b9ea09bd5b7099cbb4fc820f31b575c0366fa439b08245566692c6784e281e"
    ] = "66b9ea09bd5b7099cbb4fc820f31b575c0366fa439b08245566692c6784e281e"
    context_window: Literal[4096] = 4096
    max_output_tokens: Literal[512] = 512
    prompt_revision: Literal["rag-excerpts-ru-v1"] = "rag-excerpts-ru-v1"
