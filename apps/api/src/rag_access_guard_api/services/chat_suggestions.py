"""Project private question bindings only after fresh whole-source authorization."""

from pathlib import Path
from typing import Final

from anyio.to_thread import run_sync
from pydantic import ValidationError

from rag_access_guard import Guard
from rag_access_guard_api.adapters.llm import FakeTokenCounter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.config import Settings
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.files import load_private_json
from rag_access_guard_api.demo.reports import SeedSuggestion, SeedSuggestions
from rag_access_guard_api.schemas.demo import Suggestion, SuggestionList
from rag_access_guard_api.services.security import ReadUoW

MAX_MANIFEST_SUGGESTIONS: Final = 100
MAX_VISIBLE_SUGGESTIONS: Final = 4


class SuggestionsUnavailableError(Exception):
    """A configured private bundle cannot be served; carry no input or path details."""


def _load(path: Path) -> tuple[SeedSuggestion, ...]:
    try:
        bundle = load_private_json(path, SeedSuggestions)
        if (
            bundle.schema_version != 1
            or len(bundle.suggestions) > MAX_MANIFEST_SUGGESTIONS
            or len({item.id for item in bundle.suggestions}) != len(bundle.suggestions)
        ):
            raise SuggestionsUnavailableError
        for item in bundle.suggestions:
            _ = Suggestion(id=item.id, question=item.question)
    except (DemoOwnershipError, ValidationError):
        raise SuggestionsUnavailableError from None
    else:
        return bundle.suggestions


async def list_suggestions(uow: ReadUoW) -> SuggestionList:
    """Authorize all dependency refs in this transaction, then limit the visible projection."""
    path = Settings().suggestions_path
    if path is None:
        return SuggestionList(items=(), policy_revision=uow.revision)
    candidates = await run_sync(_load, path)
    guard, reader = Guard(FakeTokenCounter()), PostgresPolicyReader(uow)
    visible: list[Suggestion] = []
    for item in candidates:
        decision = await guard.authorize_read(
            uow.principal.principal_id, item.required_source_refs, reader
        )
        if decision.allowed:
            visible.append(Suggestion(id=item.id, question=item.question))
            if len(visible) == MAX_VISIBLE_SUGGESTIONS:
                break
    return SuggestionList(items=tuple(visible), policy_revision=uow.revision)
