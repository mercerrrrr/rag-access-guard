"""Session-protected administrative document registry."""

from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.datastructures import UploadFile

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_admin_mutation, check_origin
from rag_access_guard_api.routes.uploads import read_upload
from rag_access_guard_api.schemas.documents import (
    DocumentList,
    DocumentPatch,
    DocumentSummary,
    DocumentVersionList,
    DocumentVersionSummary,
)
from rag_access_guard_api.services.documents import (
    list_documents,
    list_versions,
    register_text_document,
    update_document,
)
from rag_access_guard_api.services.ingestion import ingest_text_version, prepare_upload
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.text_documents import (
    DocumentError,
    validate_title,
)


def build_documents_router(engine: AsyncEngine, settings: Settings) -> APIRouter:
    """Bind document operations to the shared policy and session gate."""
    router = APIRouter(prefix="/api/admin/documents", tags=["documents"])
    cookies = AuthCookies(settings.loopback_development)
    policy = PolicyUnitOfWork(engine)

    @router.post("", status_code=201)
    async def register(request: Request) -> DocumentSummary:
        """Validate bounded input before taking the exclusive policy lock."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.protected_read(credentials.session) as read:
            check_admin_mutation(read, credentials.csrf)
        async with request.form(max_files=1, max_fields=1, max_part_size=4096) as form:
            title, upload = form.get("title"), form.get("file")
            if (
                set(form) != {"title", "file"}
                or not isinstance(title, str)
                or not isinstance(upload, UploadFile)
            ):
                raise DocumentError(422)
            title = validate_title(title)
            prepared = await to_thread.run_sync(prepare_upload, await read_upload(upload))
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            return await register_text_document(uow, title, prepared)

    @router.post("/{document_id}/versions", status_code=201)
    async def ingest(document_id: UUID, request: Request) -> DocumentVersionSummary:
        """Accept a bounded file; no client-supplied version or provenance fields."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.protected_read(credentials.session) as read:
            check_admin_mutation(read, credentials.csrf)
        async with request.form(max_files=1, max_fields=0, max_part_size=4096) as form:
            upload = form.get("file")
            if len(form.multi_items()) != 1 or not isinstance(upload, UploadFile):
                raise DocumentError(422)
            payload = await read_upload(upload)
        return await ingest_text_version(
            engine, credentials.session, document_id, upload=payload, csrf_token=credentials.csrf
        )

    @router.get("")
    async def listing(request: Request) -> DocumentList:
        """Return metadata only after a fresh administrative check."""
        check_origin(request, settings)
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return DocumentList(items=await list_documents(uow))

    @router.patch("/{document_id}")
    async def patch(document_id: UUID, payload: DocumentPatch, request: Request) -> DocumentSummary:
        """Atomically update metadata and its security revision."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            return await update_document(
                uow, document_id, title=payload.title, is_active=payload.is_active
            )

    @router.get("/{document_id}/versions")
    async def versions(document_id: UUID, request: Request) -> DocumentVersionList:
        """Expose stored version metadata, never source content."""
        check_origin(request, settings)
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return DocumentVersionList(items=await list_versions(uow, document_id))

    return router
