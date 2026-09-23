"""Multipart upload boundary before framework filename normalization."""

from email.message import Message

from pydantic import TypeAdapter
from starlette.datastructures import UploadFile

from rag_access_guard_api.adapters.text_parser import validate_filename
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.text_documents import MAX_TEXT_BYTES

_parameters = TypeAdapter(list[tuple[str, str | tuple[str | None, str | None, str]]])


async def read_upload(upload: UploadFile) -> UploadPayload:
    """Check original filename parameters, then read at most the file limit plus one."""
    for disposition in upload.headers.getlist("content-disposition"):
        header = Message()
        header["Content-Disposition"] = disposition
        parameters = _parameters.validate_python(
            header.get_params(header="content-disposition") or []
        )
        for name, value in parameters:
            # Multipart uses plain filename parameters, not RFC 2231 continuations.
            if name.lower() == "filename" and isinstance(value, str):
                validate_filename(value)
    return UploadPayload(
        filename=upload.filename or "",
        media_type=upload.content_type or "",
        data=await upload.read(MAX_TEXT_BYTES + 1),
    )
