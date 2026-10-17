"""Closed publisher-origin notices used in context and answer presentation."""

from enum import StrEnum
from typing import Final


class OriginLabel(StrEnum):
    """These notices describe provenance, not the truth of a document's claims."""

    SYNTHETIC = "Учебный пример; вымышленный материал, не официальный документ."
    OFFICIAL = (
        "Открытый источник; происхождение не подтверждает текущую юридическую действительность."
    )
    UPLOAD = "Пользовательский материал; достоверность издателя не подтверждена."


SYNTHETIC_LABEL: Final = OriginLabel.SYNTHETIC.value
OFFICIAL_LABEL: Final = OriginLabel.OFFICIAL.value
UPLOAD_LABEL: Final = OriginLabel.UPLOAD.value
