"""Sanitized protected API errors and cache policy at the HTTP boundary."""

from typing import override

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from python_multipart.exceptions import MultipartParseError
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from rag_access_guard_api.schemas.embedding_vectors import (
    EmbeddingError,
    VersionActivationConflictError,
)
from rag_access_guard_api.schemas.search import (
    InvalidSearchError,
    RetrievalNotConfiguredError,
    SearchError,
)
from rag_access_guard_api.services.errors import (
    AlreadyAuthenticatedError,
    ForbiddenError,
    GrantConflictError,
    RateLimitedError,
    RoleConflictError,
    UnauthenticatedError,
)
from rag_access_guard_api.services.text_documents import DocumentError


class AuthCacheMiddleware(BaseHTTPMiddleware):
    """Prevent caching protected successes and errors, including validation failures."""

    @override
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Attach response policy after endpoint and exception processing."""
        response = await call_next(request)
        if request.url.path.startswith(
            ("/api/auth/", "/api/admin/", "/api/documents", "/api/search")
        ):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Cookie"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response


async def _role_conflict(_request: Request, _: RoleConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "Role already exists"})


async def _invalid_multipart(_request: Request, _: MultipartParseError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "Invalid document"})


async def _indexing_unavailable(_request: Request, _: EmbeddingError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Indexing unavailable"})


async def _activation_conflict(
    _request: Request, _: VersionActivationConflictError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "Version activation conflict"})


async def _search_unavailable(_request: Request, _: SearchError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Search unavailable"})


async def _invalid_search(_request: Request, _: InvalidSearchError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "Invalid query"})


async def _search_unconfigured(_request: Request, _: RetrievalNotConfiguredError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "Retrieval not configured"})


def register_auth_errors(app: FastAPI) -> None:
    """Translate known failures without credentials or submitted document details."""
    app.add_middleware(AuthCacheMiddleware)
    _ = app.exception_handler(RoleConflictError)(_role_conflict)
    _ = app.exception_handler(MultipartParseError)(_invalid_multipart)
    _ = app.exception_handler(EmbeddingError)(_indexing_unavailable)
    _ = app.exception_handler(VersionActivationConflictError)(_activation_conflict)

    _ = app.exception_handler(SearchError)(_search_unavailable)
    _ = app.exception_handler(InvalidSearchError)(_invalid_search)
    _ = app.exception_handler(RetrievalNotConfiguredError)(_search_unconfigured)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, _: Exception) -> JSONResponse:
        headers = (
            {"Cache-Control": "private, no-store", "Vary": "Cookie"}
            if request.url.path.startswith(
                ("/api/auth/", "/api/admin/", "/api/documents", "/api/search")
            )
            else None
        )
        return JSONResponse(
            status_code=500, content={"detail": "Internal server error"}, headers=headers
        )

    @app.exception_handler(UnauthenticatedError)
    async def unauthenticated(request: Request, _: UnauthenticatedError) -> JSONResponse:
        detail = "Invalid credentials" if request.url.path == "/api/auth/login" else "Unauthorized"
        return JSONResponse(status_code=401, content={"detail": detail})

    @app.exception_handler(ForbiddenError)
    async def forbidden(_request: Request, _: ForbiddenError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})

    @app.exception_handler(AlreadyAuthenticatedError)
    async def conflict(_request: Request, _: AlreadyAuthenticatedError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "Already authenticated"})

    @app.exception_handler(GrantConflictError)
    async def grant_conflict(_request: Request, _: GrantConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "Grant already exists"})

    @app.exception_handler(RateLimitedError)
    async def rate_limited(_: Request, error: RateLimitedError) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"},
            headers={"Retry-After": str(error.retry_after)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation(_request: Request, _: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    @app.exception_handler(SQLAlchemyError)
    async def unavailable(_request: Request, _: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "Service unavailable"})

    @app.exception_handler(DocumentError)
    async def invalid_document(_request: Request, error: DocumentError) -> JSONResponse:
        details = {
            404: "Not found",
            413: "Upload too large",
            415: "Unsupported media type",
            422: "Invalid document",
            503: "Service unavailable",
        }
        detail = (
            "Text layer required" if error.code == "text_layer_required" else details[error.status]
        )
        return JSONResponse(status_code=error.status, content={"detail": detail})
