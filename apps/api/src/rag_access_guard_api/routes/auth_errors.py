"""Sanitized authentication errors and cache policy at the HTTP boundary."""

from typing import override

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from rag_access_guard_api.services.errors import (
    AlreadyAuthenticatedError,
    ForbiddenError,
    RateLimitedError,
    UnauthenticatedError,
)


class AuthCacheMiddleware(BaseHTTPMiddleware):
    """Prevent caching auth successes and errors, including validation failures."""

    @override
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Attach response policy after endpoint and exception processing."""
        response = await call_next(request)
        if request.url.path.startswith("/api/auth/"):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Vary"] = "Cookie"
        return response


def register_auth_errors(app: FastAPI) -> None:
    """Translate only known authentication failures without credential details."""
    app.add_middleware(AuthCacheMiddleware)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, _: Exception) -> JSONResponse:
        headers = (
            {"Cache-Control": "private, no-store", "Vary": "Cookie"}
            if request.url.path.startswith("/api/auth/")
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
