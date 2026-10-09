"""Safe authenticated readiness projection without operator or content metadata."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict


class ModelStatus(BaseModel):
    """Expose only the selected audited name and its readiness."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    name: str | None
    state: Literal["ready", "unavailable", "disabled", "test"]


class SearchStatus(BaseModel):
    """Represent checked artifacts and calibration, independent of model readiness."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    state: Literal["ready", "unavailable"]


class SystemStatus(BaseModel):
    """A readiness snapshot never acts as a cached authorization decision."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["disabled", "test", "local"]
    model: ModelStatus
    search: SearchStatus
