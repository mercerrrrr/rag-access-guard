"""Closed, metadata-minimal guidance projections for authenticated sessions."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from rag_access_guard_api.schemas.demo_corpus import CorpusId, CorpusValue


class Suggestion(CorpusValue):
    """One permitted question; source identities stay on the server."""

    id: CorpusId
    question: Annotated[str, Field(min_length=1, max_length=4096)]

    @field_validator("question")
    @classmethod
    def meaningful_question(cls, value: str) -> str:
        """Reject an empty display value without rewriting operator input."""
        if not value.strip():
            message = "Question must contain text"
            raise ValueError(message)
        return value


class SuggestionList(CorpusValue):
    """A fresh authorization projection, never a cached grant."""

    items: Annotated[tuple[Suggestion, ...], Field(max_length=4)]
    policy_revision: Annotated[int, Field(ge=0)]


class DemoProfile(CorpusValue):
    """The universal prototype profile exposes no operator-selected assets."""

    enabled: Literal[False] = False
    title: Literal["Помощник по базе знаний"] = "Помощник по базе знаний"
    subtitle: None = None
    notice: Literal["Исследовательский прототип"] = "Исследовательский прототип"
    logo_url: None = None
