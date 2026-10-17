"""Metadata contract for the pinned text-only Qwen3.5 selection model."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExtractiveModelInfo(BaseModel):
    """Reject tokenizer or architecture substitutions even behind a matching tag."""

    architecture: Literal["qwen35"] = Field(alias="general.architecture")
    context_length: Literal[262144] = Field(alias="qwen35.context_length")
    tokenizer: Literal["gpt2"] = Field(alias="tokenizer.ggml.model")
    pretokenizer: Literal["qwen35"] = Field(alias="tokenizer.ggml.pre")
    add_bos: Literal[False] = Field(alias="tokenizer.ggml.add_bos_token")
    eos: Literal[248046] = Field(alias="tokenizer.ggml.eos_token_id")


class ExtractiveModelMetadata(BaseModel):
    """The selected model must not inject a system override or embedded history."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, hide_input_in_errors=True)
    template: str = Field(repr=False)
    model_info: ExtractiveModelInfo
    system: Literal[""] = ""
    messages: tuple[()] = ()


class TextExtractiveModelMetadata(ExtractiveModelMetadata):
    """Reject a vision projector reintroduced behind the text-only model tag."""

    projector_info: None = None
    capabilities: tuple[Literal["completion", "tools", "thinking"], ...]
