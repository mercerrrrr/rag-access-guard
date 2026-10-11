"""Use ordinary ingestion and server UUIDs; publish completion in the same transaction."""

from dataclasses import dataclass, replace

from anyio.to_thread import run_sync
from sqlalchemy import select

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.inputs import SeedInputs
from rag_access_guard_api.demo.ledger import complete_step
from rag_access_guard_api.demo.mutations import SeedAuthority, seed_transaction
from rag_access_guard_api.demo.paths import private_path
from rag_access_guard_api.demo.plan import SeedBinding
from rag_access_guard_api.persistence import Document
from rag_access_guard_api.schemas.demo_corpus import CorpusDocument
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services import documents, indexing, ingestion
from rag_access_guard_api.services.demo_corpus import read_bounded
from rag_access_guard_api.services.origin import canonical_origin_hash


@dataclass(frozen=True, slots=True)
class SeedDocumentStep:
    """Bind an external version request to its previously observed completion."""

    document: CorpusDocument
    expected: SeedBinding | None


def read_upload(inputs: SeedInputs, document: CorpusDocument) -> UploadPayload:
    """Recheck the private source boundary and bound actual reads before ingestion."""
    path = private_path(inputs.corpus_root / document.relative_path)
    with path.open("rb") as stream:
        data = read_bounded(stream)
    return UploadPayload(filename=path.name, media_type=document.media_type, data=data)


async def commit_document_step(
    authority: SeedAuthority, step: SeedDocumentStep, prepared: ingestion.PreparedUpload
) -> bool:
    """A stale active pointer or completion never publishes a competing version."""
    document, expected = step.document, step.expected
    desired = SeedBinding(
        f"document:{document.key}",
        authority.marker.instance_id,
        upload_sha256=document.upload_sha256,
        origin_sha256=canonical_origin_hash(document.origin),
    )
    async with seed_transaction(authority) as (uow, ledger):
        tracked = next((binding for binding in ledger.bindings if binding.key == desired.key), None)
        if tracked != expected:
            if tracked is not None and (tracked.upload_sha256, tracked.origin_sha256) == (
                desired.upload_sha256,
                desired.origin_sha256,
            ):
                return False
            raise DemoOwnershipError(code="step_changed")
        if expected is None:
            created = await documents.register_text_document(uow, document.title, prepared)
            identifier, version_id = created.id, created.active_version_id
        else:
            current = (
                (
                    await uow.connection.execute(
                        select(Document.active_version_id, Document.is_active)
                        .where(Document.id == expected.object_id)
                        .with_for_update()
                    )
                )
                .tuples()
                .one_or_none()
            )
            if current is None or not current[1] or current[0] != expected.version_id:
                raise DemoOwnershipError(code="tracked_document_drift")
            version = await ingestion.store_version(uow, expected.object_id, prepared)
            if version.status != "ready":
                raise DemoOwnershipError(code="ingestion_failed")
            await ingestion.activate_version(uow, version)
            identifier, version_id = expected.object_id, version.id
        if version_id is None:
            raise DemoOwnershipError(code="ingestion_failed")
        await complete_step(
            uow.connection,
            authority.marker,
            replace(desired, object_id=identifier, version_id=version_id),
        )
    return True


async def apply_document(
    authority: SeedAuthority, inputs: SeedInputs, step: SeedDocumentStep
) -> bool:
    """Hash-bound parser/tokenizer/embedding work runs with no database locks held."""
    document = step.document
    upload = await run_sync(read_upload, inputs, document)
    prepared = await indexing.prepare_index(upload, embeddings.get_embedding_adapter())
    if prepared.manifest.source_sha256 != document.upload_sha256 or not prepared.vectors:
        raise DemoOwnershipError(code="ingestion_failed")
    prepared = replace(prepared, origin=document.origin)
    return await commit_document_step(authority, step, prepared)
