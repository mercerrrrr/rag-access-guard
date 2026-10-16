"""Frozen discriminated suite inputs independent of entry points and adapters."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from experiments.run_config import RunOptions
from experiments.scenario_types import FrozenModel


class SecurityInvocation(FrozenModel):
    """Preserve the original schema-one invocation without new defaults."""

    suite: Literal["security"] = "security"
    options: RunOptions


class DemoQualityInvocation(FrozenModel):
    """Require explicit private inputs and a deliberate evaluation mode."""

    suite: Literal["demo-quality"] = "demo-quality"
    config: Path
    scenarios: Path
    output: Path
    mode: Literal["validate", "calibrate", "holdout", "rehearsal"]
    resume: bool = False

    @model_validator(mode="after")
    def absolute_paths(self) -> Self:
        """Reject relative identities before files or connections can be opened."""
        if not all(path.is_absolute() for path in (self.config, self.scenarios, self.output)):
            message = "Demo-quality paths must be absolute"
            raise ValueError(message)
        return self


type SuiteInvocation = Annotated[
    SecurityInvocation | DemoQualityInvocation, Field(discriminator="suite")
]
