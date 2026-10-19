"""Recognize the closed local-help intent without changing the submitted request."""

from typing import Final, Literal

HELP_MESSAGE: Final = (
    "Я помогаю искать ответы в доступных документах. "
    "Задайте конкретный вопрос или выберите пример. "
    "Специальные команды не нужны. "
    "Источники ответа можно открыть в разделе «Источники ответа»."
)
_HELP_PHRASES: Final = frozenset(
    {"привет", "здравствуйте", "добрый день", "помощь", "как пользоваться", "что ты умеешь"}
)


def classify_help(user_input: str) -> Literal["assistant_help"] | None:
    """Select local help only for an exact complete help phrase."""
    normalized = " ".join(user_input.casefold().split()).rstrip(".!? ")
    return "assistant_help" if normalized in _HELP_PHRASES else None
