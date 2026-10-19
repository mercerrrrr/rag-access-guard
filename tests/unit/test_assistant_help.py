import pytest

from rag_access_guard_api.services.assistant_help import HELP_MESSAGE, classify_help


@pytest.mark.parametrize(
    "value",
    [
        "Привет!",
        "здравствуйте",
        "ДОБРЫЙ ДЕНЬ",
        "помощь",
        "  Как пользоваться?  ",
        "что ты умеешь",
        "  добрый   день...?!  ",
    ],
)
def test_complete_help_phrase_is_recognized(value: str) -> None:
    # Given a complete phrase with only permitted matching normalization.
    # When classified, then the server selects its local help outcome.
    assert classify_help(value) == "assistant_help"


@pytest.mark.parametrize(
    "value",
    [
        "Привет, как оформить практику?",
        "Что ты умеешь в документах?",
        "привет,",
        "!привет",
        "помощь мне",
        "",
        "hello",
        "добрыйдень",
    ],
)
def test_substantive_or_partial_phrase_keeps_normal_generation(value: str) -> None:
    # Given input outside the closed whole-phrase vocabulary.
    # When classified, then it cannot bypass the ordinary RAG path.
    assert classify_help(value) is None


def test_help_copy_names_the_sources_section_without_viewport_direction() -> None:
    assert HELP_MESSAGE == (
        "Я помогаю искать ответы в доступных документах. "
        "Задайте конкретный вопрос или выберите пример. "
        "Специальные команды не нужны. "
        "Источники ответа можно открыть в разделе «Источники ответа»."
    )
