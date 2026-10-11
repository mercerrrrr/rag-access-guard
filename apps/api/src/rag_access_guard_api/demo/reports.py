"""Private immutable reports and whole-dependency suggestions from actual active metadata."""

from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from rag_access_guard import SourceRef
from rag_access_guard_api.demo.contracts import DemoConfig, SeedDocumentBinding, SeedReport
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.inputs import SeedInputs
from rag_access_guard_api.demo.mutations import SeedAuthority, binding_for
from rag_access_guard_api.demo.plan import SeedLedger
from rag_access_guard_api.demo.private_write import exclusive_private_write, private_directory
from rag_access_guard_api.persistence import Document, DocumentChunk, DocumentVersion
from rag_access_guard_api.schemas.demo_corpus import CorpusValue
from rag_access_guard_api.services.indexing import require_index_authority


class SeedSuggestion(CorpusValue):
    """Complete required SourceRefs; this export is metadata and never an authorization."""

    id: str
    question: str
    required_source_refs: tuple[SourceRef, ...]


class SeedSuggestions(CorpusValue):
    """Private suggestion bundle carrying immutable provenance, not grants."""

    schema_version: int = 1
    instance_id: str
    dataset_id: str
    suggestions: tuple[SeedSuggestion, ...]


def report_bindings(ledger: SeedLedger) -> tuple[SeedDocumentBinding, ...]:
    """Expose complete durable document/version hashes without content or local paths."""
    return tuple(
        SeedDocumentBinding(
            key=binding.key.removeprefix("document:"),
            document_id=binding.object_id,
            version_id=binding.version_id,
            upload_sha256=binding.upload_sha256,
            origin_sha256=binding.origin_sha256,
        )
        for binding in ledger.bindings
        if binding.key.startswith("document:")
        and binding.version_id is not None
        and binding.upload_sha256 is not None
        and binding.origin_sha256 is not None
    )


async def export_suggestions(
    authority: SeedAuthority, inputs: SeedInputs, ledger: SeedLedger
) -> SeedSuggestions:
    """Fail the complete export on stale, missing, unready, or chunkless dependencies."""
    suggestions: list[SeedSuggestion] = []
    async with authority.policy.protected_read(authority.session_token) as uow:
        require_index_authority(uow, authority.csrf_token)
        for suggestion in inputs.manifest.suggestions:
            refs: list[SourceRef] = []
            for key in suggestion.required_document_keys:
                binding = binding_for(ledger, f"document:{key}")
                row = (
                    (
                        await uow.connection.execute(
                            select(
                                Document.is_active,
                                Document.active_version_id,
                                DocumentVersion.status,
                            )
                            .join(DocumentVersion, Document.active_version_id == DocumentVersion.id)
                            .where(Document.id == binding.object_id)
                        )
                    )
                    .tuples()
                    .one_or_none()
                )
                if row is None or not row[0] or row[2] != "ready" or row[1] != binding.version_id:
                    raise DemoOwnershipError(code="suggestion_dependency_unavailable")
                chunks = (
                    (
                        await uow.connection.execute(
                            select(DocumentChunk.id)
                            .where(DocumentChunk.document_version_id == binding.version_id)
                            .order_by(DocumentChunk.ordinal)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not chunks or binding.version_id is None:
                    raise DemoOwnershipError(code="suggestion_dependency_unavailable")
                refs.extend(
                    SourceRef(
                        document_id=binding.object_id,
                        document_version_id=binding.version_id,
                        chunk_id=chunk,
                    )
                    for chunk in chunks
                )
            suggestions.append(
                SeedSuggestion(
                    id=suggestion.id, question=suggestion.question, required_source_refs=tuple(refs)
                )
            )
    return SeedSuggestions(
        instance_id=str(inputs.marker.instance_id),
        dataset_id=inputs.manifest.dataset_id,
        suggestions=tuple(suggestions),
    )


def output_locations(config: DemoConfig) -> tuple[Path, Path]:
    """Return a fresh protected run directory; explicit paths remain exclusive-create."""
    run_root = private_directory(config.ownership_manifest.parent / "seed-runs")
    run = private_directory(run_root / uuid4().hex)
    report = config.report_path or run / "seed-report.json"
    suggestions = config.suggestions_path or run / "suggestions.json"
    for path in (report, suggestions):
        _ = private_directory(path.parent)
    return report, suggestions


def write_outputs(report: SeedReport, suggestions: SeedSuggestions | None) -> SeedReport:
    """Publish immutable private artifacts and return their excluded typed locations."""
    if report.report_path is None:
        raise DemoOwnershipError(code="report_path_missing")
    if suggestions is not None:
        if report.suggestions_path is None:
            raise DemoOwnershipError(code="suggestions_path_missing")
        exclusive_private_write(report.suggestions_path, suggestions.model_dump_json().encode())
    exclusive_private_write(report.report_path, report.model_dump_json().encode())
    return report
