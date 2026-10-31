from typing import Protocol

import numpy as np
from numpy.typing import NDArray

__version__: str

class SessionOptions:
    intra_op_num_threads: int
    inter_op_num_threads: int
    enable_cpu_mem_arena: bool
    enable_mem_pattern: bool

class NodeArg:
    name: str

class FloatMatrix(Protocol):
    @property
    def shape(self) -> tuple[int, ...]: ...
    @property
    def dtype(self) -> np.dtype[np.float32]: ...
    def __getitem__(self, index: tuple[int, int]) -> np.float32: ...

class InferenceSession:
    def __init__(
        self, path: str, *, sess_options: SessionOptions, providers: list[str]
    ) -> None: ...
    def get_inputs(self) -> list[NodeArg]: ...
    def run(
        self, output_names: list[str], input_feed: dict[str, NDArray[np.int64]]
    ) -> list[FloatMatrix]: ...
