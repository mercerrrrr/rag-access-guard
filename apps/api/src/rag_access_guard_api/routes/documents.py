"""Session-protected administrative document registry."""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.datastructures import UploadFile

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.documents import (
    DocumentList,
    DocumentPatch,
    DocumentSummary,
    DocumentVersionList,
)
from rag_access_guard_api.services.documents import (
    list_documents,
    list_versions,
    register_text_document,
    require_admin,
    update_document,
)
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.security import MutationUoW, PolicyUnitOfWork, ReadUoW
from rag_access_guard_api.services.text_documents import (
    MAX_TEXT_BYTES,
    DocumentError,
    parse_text,
    validate_title,
)
from rag_access_guard_api.services.tokens import matches_token


def _check_mutation(uow: ReadUoW | MutationUoW, csrf: str) -> None:
    require_admin(uow)
    if not matches_token(csrf, uow.csrf_digest):
        raise ForbiddenError


def build_documents_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Bind document operations to the shared policy and session gate."""
    router = APIRouter(prefix="/api/admin/documents", tags=["documents"])
    cookies = AuthCookies(settings.loopback_development)

    @router.post("", status_code=201)
    async def register(request: Request) -> DocumentSummary:
        """Validate bounded input before taking the exclusive policy lock."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.protected_read(credentials.session) as read:
            _check_mutation(read, credentials.csrf)
        async with request.form(max_files=1, max_fields=1, max_part_size=4096) as form:
            title, upload = form.get("title"), form.get("file")
            if (
                set(form) != {"title", "file"}
                or not isinstance(title, str)
                or not isinstance(upload, UploadFile)
            ):
                raise DocumentError(422)
            title = validate_title(title)
            raw = await upload.read(MAX_TEXT_BYTES + 1)
            filename, media_type = upload.filename or "", upload.content_type or ""
            _ = parse_text(raw, filename, media_type)
        async with policy.mutation(credentials.session) as uow:
            _check_mutation(uow, credentials.csrf)
            return await register_text_document(
                uow, title=title, original_bytes=raw, filename=filename, media_type=media_type
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
            _check_mutation(uow, credentials.csrf)
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
