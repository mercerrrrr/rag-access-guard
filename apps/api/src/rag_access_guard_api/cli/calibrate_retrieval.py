"""Offline calibration from an explicitly supplied synthetic dataset."""

import json
import os
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Annotated

import anyio
import typer
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from rag_access_guard_api.adapters.embeddings import MODEL_ID, get_embedding_adapter
from rag_access_guard_api.adapters.tokenizer import (
    MODEL_REVISION,
    TOKENIZER_IDENTITY,
    TokenizerUnavailableError,
)
from rag_access_guard_api.adapters.vector_scoring import SIMILARITY_REVISION, calibration_scores
from rag_access_guard_api.config import DEFAULT_DATABASE_URL
from rag_access_guard_api.schemas.calibration import CalibrationDataset, EvaluationManifest
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError, validate_vectors
from rag_access_guard_api.schemas.retrieval_config import RetrievalConfig
from rag_access_guard_api.schemas.search import SearchError
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.calibration import ScoredQuery, score_thresholds
from rag_access_guard_api.services.chunking import CHUNKER_REVISION

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


async def calibrate(dataset: CalibrationDataset, output: Path) -> RetrievalConfig:
    """Run pinned E5 and retain the runtime pgvector scoring grid."""
    embedder = get_embedding_adapter()
    vectors = await embedder.embed_passages(tuple(chunk.text for chunk in dataset.corpus))
    validate_vectors(vectors, len(dataset.corpus))
    query_vectors: list[tuple[float, ...]] = []
    for query in dataset.queries:
        vector = await embedder.embed_query(query.query)
        validate_vectors((vector,), 1)
        query_vectors.append(vector)
    scored = await calibration_scores(
        os.environ.get("RAG_ACCESS_GUARD_DATABASE_URL", str(DEFAULT_DATABASE_URL)),
        vectors,
        tuple(query_vectors),
    )
    rows = tuple(
        ScoredQuery(
            scores=tuple(
                (chunk.id, value) for chunk, value in zip(dataset.corpus, scores, strict=True)
            ),
            relevant=frozenset(query.relevant_chunk_ids),
        )
        for query, scores in zip(dataset.queries, scored, strict=True)
    )
    result = score_thresholds(rows)
    dataset_json = json.dumps(
        dataset.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    corpus_json = json.dumps(
        [
            chunk.model_dump(mode="json")
            for chunk in sorted(dataset.corpus, key=lambda item: item.id)
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    config = RetrievalConfig(
        threshold=result.chosen.threshold,
        no_results=result.chosen.threshold > 1.0,
        dataset_sha256=sha256(dataset_json.encode()).hexdigest(),
        corpus_sha256=sha256(corpus_json.encode()).hexdigest(),
        model_id=MODEL_ID,
        model_revision=MODEL_REVISION,
        tokenizer_revision=TOKENIZER_IDENTITY,
        chunker_revision=CHUNKER_REVISION,
        scorer_revision="micro-f1-top5-v1",
        similarity_revision=SIMILARITY_REVISION,
        config_sha256="0" * 64,
    )
    bound = config.model_copy(update={"config_sha256": config.computed_hash()})
    bound.require_compatible()
    report = {
        "config": bound.model_dump(),
        "grid": [asdict(row) for row in result.grid],
        "chosen": asdict(result.chosen),
        "precision": SIMILARITY_REVISION,
        "tie_order": "similarity-desc-uuid-asc",
        "query_scores": [
            {
                "query_id": query.id,
                "scores": [(str(identity), value) for identity, value in row.scores],
            }
            for query, row in zip(dataset.queries, rows, strict=True)
        ],
    }
    target = anyio.Path(output)
    await target.parent.mkdir(parents=True, exist_ok=True)
    _ = await target.with_name("calibration-report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2), encoding="utf-8"
    )
    _ = await target.write_text(
        json.dumps(bound.model_dump(), sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    return bound


@app.command()
def run(
    dataset: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option(dir_okay=False)],
    evaluation_manifest: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Calibrate once before evaluation; refuse overlapping supplied split identities."""
    inputs = {dataset.resolve()}
    if evaluation_manifest is not None:
        inputs.add(evaluation_manifest.resolve())
    report_path = output.with_name("calibration-report.json").resolve()
    if output.resolve() == report_path or inputs & {output.resolve(), report_path}:
        typer.echo("Calibration output must differ from its inputs", err=True)
        raise typer.Exit(code=1)
    try:
        parsed = CalibrationDataset.model_validate_json(dataset.read_bytes())
        if evaluation_manifest is not None:
            parsed.require_disjoint(
                EvaluationManifest.model_validate_json(evaluation_manifest.read_bytes())
            )
        config = anyio.run(
            calibrate, parsed, output, backend_options={"loop_factory": create_event_loop}
        )
    except (
        OSError,
        ValidationError,
        SearchError,
        EmbeddingError,
        TokenizerUnavailableError,
        SQLAlchemyError,
    ) as error:
        typer.echo("Calibration failed", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"threshold={config.threshold:.17g} config_sha256={config.config_sha256}")


def main() -> None:
    """Run without printing dataset contents on errors."""
    app()
