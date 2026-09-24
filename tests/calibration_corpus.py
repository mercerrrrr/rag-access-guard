from uuid import UUID

from rag_access_guard_api.schemas.calibration import (
    CalibrationDataset,
    CalibrationQuery,
    CorpusChunk,
)


def synthetic_calibration_dataset() -> CalibrationDataset:
    rows = (
        (
            "Для удалённого доступа обязателен корпоративный VPN и второй фактор.",
            "Что требуется для удалённого доступа?",
            "Remote access requires the corporate VPN and a second authentication factor.",
            "What does remote access require?",
        ),
        (
            "Заявка на закупку проходит согласование в отделе снабжения.",
            "Кто согласовывает заявку на закупку?",
            "Purchase requests are approved by the procurement department.",
            "Who approves purchase requests?",
        ),
        (
            "Резервное копирование выполняется ежедневно в полночь.",
            "Когда выполняется резервное копирование?",
            "Backups run every day at midnight.",
            "When do backups run?",
        ),
        (
            "Отчёт о командировке нужно представить в течение пяти рабочих дней.",  # noqa: RUF001
            "Когда нужно сдать отчёт о командировке?",  # noqa: RUF001
            "Travel expense reports must be submitted within five working days.",
            "When are travel expense reports due?",
        ),
    )
    corpus: list[CorpusChunk] = []
    queries: list[CalibrationQuery] = []
    for index, (ru_text, ru_query, en_text, en_query) in enumerate(rows):
        ru_id, en_id = UUID(int=2 * index + 1), UUID(int=2 * index + 2)
        corpus.extend((CorpusChunk(id=ru_id, text=ru_text), CorpusChunk(id=en_id, text=en_text)))
        queries.extend(
            (
                CalibrationQuery(
                    id=f"cal-ru-{index}",
                    language="ru",
                    query=ru_query,
                    relevant_chunk_ids=(ru_id, en_id),
                ),
                CalibrationQuery(
                    id=f"cal-en-{index}",
                    language="en",
                    query=en_query,
                    relevant_chunk_ids=(ru_id, en_id),
                ),
            )
        )
    queries.extend(
        (
            CalibrationQuery(
                id="cal-ru-none-1",
                language="ru",
                query="Какова масса спутника Юпитера Европы?",
                relevant_chunk_ids=(),
            ),
            CalibrationQuery(
                id="cal-ru-none-2",
                language="ru",
                query="Как приготовить клубничное варенье?",
                relevant_chunk_ids=(),
            ),
            CalibrationQuery(
                id="cal-en-none-1",
                language="en",
                query="What is the mass of Jupiter's moon Europa?",
                relevant_chunk_ids=(),
            ),
            CalibrationQuery(
                id="cal-en-none-2",
                language="en",
                query="How do I cook strawberry jam?",
                relevant_chunk_ids=(),
            ),
        )
    )
    return CalibrationDataset(
        schema_version=1, split="calibration", corpus=tuple(corpus), queries=tuple(queries)
    )
