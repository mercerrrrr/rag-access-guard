"""Local ASGI server entry point."""

from typing import Final

import uvicorn
from uvicorn.loops.asyncio import asyncio_loop_factory

APPLICATION_IMPORT: Final = "rag_access_guard_api.main:app"
EVENT_LOOP_IMPORT: Final = "rag_access_guard_api.server:create_event_loop"
create_event_loop: Final = asyncio_loop_factory(use_subprocess=True)


def run() -> None:
    """Run the API on the local development interface."""
    uvicorn.run(
        APPLICATION_IMPORT,
        host="127.0.0.1",
        port=8000,
        loop=EVENT_LOOP_IMPORT,
    )
