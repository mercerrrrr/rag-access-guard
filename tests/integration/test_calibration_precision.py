import json
from pathlib import Path
from typing import override

import anyio
import pytest
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import Engine, Float, Uuid, column, text
from typer.testing import CliRunner

from rag_access_guard_api.adapters.embeddings import E5EmbeddingAdapter
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION
from rag_access_guard_api.cli import calibrate_retrieval
from rag_access_guard_api.config import Settings
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.calibration import ThresholdScore
from tests.calibration_corpus import synthetic_calibration_dataset
from tests.integration.embedding_fixtures import DeterministicEmbedder


class BoundaryEmbedder(DeterministicEmbedder):
    @override
    async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ((0.8, 0.6, *((0.0,) * 382)),) * len(texts)

    @override
    async def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0, *((0.0,) * 383))


class QueryScores(BaseModel):
    scores: tuple[tuple[str, float], ...]


class Report(BaseModel):
    query_scores: tuple[QueryScores, ...]
    grid: tuple[ThresholdScore, ...]


def test_calibration_scores_match_pgvector_boundary(
    auth_database: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calibrate_retrieval, "get_embedding_adapter", BoundaryEmbedder)
    output = tmp_path / "config.json"
    _ = anyio.run(
        calibrate_retrieval.calibrate,
        synthetic_calibration_dataset(),
        output,
        backend_options={"loop_factory": create_event_loop},
    )
    with auth_database.connect() as connection:
        score = TypeAdapter(float).validate_python(
            connection.execute(
                text(
                    "SELECT 1.0 - (CAST(:passage AS vector) <=> CAST(:query AS vector)) AS score"
                ).columns(column("score", Float())),
                {
                    "passage": json.dumps([0.8, 0.6, *([0.0] * 382)]),
                    "query": json.dumps([1.0, *([0.0] * 383)]),
                },
            ).scalar_one()
        )
    report = Report.model_validate_json(output.with_name("calibration-report.json").read_bytes())
    assert all(value == score for row in report.query_scores for _, value in row.scores)


def test_real_calibration_grid_replays_pgvector_counts(auth_database: Engine) -> None:
    config_path = Settings().retrieval_config_path
    assert config_path is not None
    report = Report.model_validate_json(
        config_path.with_name("calibration-report.json").read_bytes()
    )
    dataset = synthetic_calibration_dataset()
    embedder = E5EmbeddingAdapter(Path(f".cache/e5/{MODEL_REVISION}"))
    passages = anyio.run(embedder.embed_passages, tuple(chunk.text for chunk in dataset.corpus))
    queries = tuple(anyio.run(embedder.embed_query, query.query) for query in dataset.queries)
    corpus = json.dumps(
        [
            {"id": str(chunk.id), "embedding": json.dumps(vector)}
            for chunk, vector in zip(dataset.corpus, passages, strict=True)
        ]
    )
    statement = text(
        """SELECT id FROM jsonb_to_recordset(CAST(:corpus AS jsonb))
        AS c(id uuid, embedding text)
        WHERE 1.0 - (embedding::vector <=> CAST(:query AS vector)) >= :threshold
        ORDER BY embedding::vector <=> CAST(:query AS vector), id LIMIT 5"""
    ).columns(column("id", Uuid()))
    with auth_database.connect() as connection:
        for row in report.grid:
            tp = fp = fn = 0
            for query, vector in zip(dataset.queries, queries, strict=True):
                predicted = set(
                    connection.execute(
                        statement,
                        {"corpus": corpus, "query": json.dumps(vector), "threshold": row.threshold},
                    ).scalars()
                )
                relevant = set(query.relevant_chunk_ids)
                tp += len(predicted & relevant)
                fp += len(predicted - relevant)
                fn += len(relevant - predicted)
            assert (tp, fp, fn) == (row.tp, row.fp, row.fn), row.threshold


def test_config_cannot_overwrite_calibration_report(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.json"
    _ = dataset.write_text(synthetic_calibration_dataset().model_dump_json(), encoding="utf-8")
    result = CliRunner().invoke(
        calibrate_retrieval.app,
        ["--dataset", str(dataset), "--output", str(tmp_path / "calibration-report.json")],
    )
    assert result.exit_code == 1
    assert "must differ" in result.output
