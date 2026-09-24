import json
from hashlib import sha256
from pathlib import Path

import pytest


def configure_search(path: Path, monkeypatch: pytest.MonkeyPatch, threshold: float = 0.8) -> None:
    config = {
        "threshold": threshold,
        "no_results": False,
        "dataset_sha256": "a" * 64,
        "corpus_sha256": "b" * 64,
        "model_id": "intfloat/multilingual-e5-small",
        "model_revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        "tokenizer_revision": (
            "intfloat/multilingual-e5-small@"
            "614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special"
        ),
        "chunker_revision": "e5-window400-overlap50-offsets-v1",
        "scorer_revision": "micro-f1-top5-v1",
        "similarity_revision": "pgvector-0.8.6-vector-cosine-v1",
    }
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"))
    config["config_sha256"] = sha256(encoded.encode()).hexdigest()
    _ = path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("RAG_ACCESS_GUARD_RETRIEVAL_CONFIG_PATH", str(path))
