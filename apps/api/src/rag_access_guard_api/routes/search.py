"""Session-owned search without client-supplied identity or provenance."""

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.search import SearchItem, SearchRequest, SearchResponse
from rag_access_guard_api.services.search import search_documents


def build_search_router(engine: AsyncEngine, settings: Settings) -> APIRouter:
    """Bind search to the same session, origin and CSRF protocol as protected writes."""
    router = APIRouter(prefix="/api/search", tags=["search"])
    cookies = AuthCookies(settings.loopback_development)

    @router.post("")
    async def search(payload: SearchRequest, request: Request) -> SearchResponse:
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        chunks = await search_documents(
            engine,
            credentials.session,
            payload.query,
            embedder=embeddings.get_embedding_adapter(),
            csrf_token=credentials.csrf,
            limit=payload.limit,
        )
        return SearchResponse(
            items=tuple(
                SearchItem(
                    source_ref=chunk.source_ref, text=chunk.text, token_count=chunk.token_count
                )
                for chunk in chunks
            )
        )

    return router
