"""Parse only the separately audited Qwen2.5 model metadata."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class SmallModelInfo(BaseModel):
    """Reject a foreign architecture or incompatible tokenizer configuration."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    architecture: Literal["qwen2"] = Field(alias="general.architecture")
    context_length: Literal[32768] = Field(alias="qwen2.context_length")
    tokenizer: Literal["gpt2"] = Field(alias="tokenizer.ggml.model")
    pretokenizer: Literal["qwen2"] = Field(alias="tokenizer.ggml.pre")
    add_bos: Literal[False] = Field(alias="tokenizer.ggml.add_bos_token")
    eos: Literal[151645] = Field(alias="tokenizer.ggml.eos_token_id")


class SmallModelMetadata(BaseModel):
    """Require the known system bytes and reject embedded conversation history."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, hide_input_in_errors=True)
    template: str = Field(repr=False)
    model_info: SmallModelInfo
    system: str = Field(repr=False)
    messages: tuple[()] = ()
