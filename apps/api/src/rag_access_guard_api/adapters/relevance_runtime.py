"""Local-only relevance execution, imported by the contained worker on Windows."""

import gc
from pathlib import Path
from typing import TYPE_CHECKING

from tokenizers import Tokenizer

from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.schemas.relevance_config import (
    PAIR_LIMIT,
    SESSION_INTER_THREADS,
    SESSION_THREADS,
    load_relevance_config,
    verify_relevance_artifacts,
)

if TYPE_CHECKING:
    import onnxruntime as ort


def _pair_score(session: "ort.InferenceSession", ids: tuple[int, ...]) -> float:
    """Accept only the graph's one finite scalar output for a complete input pair."""
    import numpy as np  # noqa: PLC0415 -- no NumPy allocation in disabled API modes.

    output = session.run(
        ["logits"],
        {
            "input_ids": np.asarray([ids], dtype=np.int64),
            "attention_mask": np.ones((1, len(ids)), dtype=np.int64),
        },
    )
    if len(output) != 1 or output[0].shape != (1, 1) or output[0].dtype != np.float32:
        raise GenerationUnavailable
    return float(output[0][0, 0])


def score_local(path: Path, question: str, texts: tuple[str, ...]) -> tuple[float, ...]:
    """Release the transient native session before the physical executor completes."""
    config = load_relevance_config(path)
    verify_relevance_artifacts(config)
    tokenizer = Tokenizer.from_file(str(config.model_path / "tokenizer.json"))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    pairs = tuple(tuple(tokenizer.encode(question, text).ids) for text in texts)
    if any(len(ids) > PAIR_LIMIT for ids in pairs):
        raise GenerationUnavailable

    import onnxruntime as ort  # noqa: PLC0415 -- load CPU runtime only for this opted-in operation.
    from onnxruntime.capi.onnxruntime_pybind11_state import (  # noqa: PLC0415
        EPFail,
        Fail,
        InvalidArgument,
        InvalidGraph,
        InvalidProtobuf,
        NoSuchFile,
        RuntimeException,
    )
    from onnxruntime.capi.onnxruntime_pybind11_state import (  # noqa: PLC0415
        NotImplemented as RuntimeNotImplemented,
    )

    session: ort.InferenceSession | None = None
    try:
        options = ort.SessionOptions()
        options.intra_op_num_threads = SESSION_THREADS
        options.inter_op_num_threads = SESSION_INTER_THREADS
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        session = ort.InferenceSession(
            str(config.model_path / "onnx/model_quint8_avx2.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        if {node.name for node in session.get_inputs()} != {"input_ids", "attention_mask"}:
            raise GenerationUnavailable
        return tuple(_pair_score(session, ids) for ids in pairs)
    except (
        EPFail,
        Fail,
        InvalidArgument,
        InvalidGraph,
        InvalidProtobuf,
        NoSuchFile,
        RuntimeException,
        RuntimeNotImplemented,
    ):
        raise GenerationUnavailable from None
    finally:
        del session
        _ = gc.collect()
