"""Canonical immutable origin metadata in the host application."""

import json
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.persistence.origins import DocumentOriginRecord
from rag_access_guard_api.schemas.origin import DocumentOrigin


class InvalidOriginError(Exception):
    """Corrupt publisher metadata fails closed without exposing stored fields."""


def canonical_origin_hash(origin: DocumentOrigin) -> str:
    """Compute the version's canonical metadata identity."""
    return sha256(
        json.dumps(
            origin.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


async def store_origin(
    connection: AsyncConnection, version_id: UUID, origin: DocumentOrigin
) -> None:
    """Participate in the new version's transaction; the database enforces append-only timing."""
    _ = await connection.execute(
        insert(DocumentOriginRecord).values(
            version_id=version_id,
            origin=origin.model_dump(mode="json"),
            origin_sha256=canonical_origin_hash(origin),
        )
    )


async def read_version_origin(
    connection: AsyncConnection, version_id: UUID
) -> DocumentOrigin | None:
    """Internal projection after the caller's source or administrative metadata gate."""
    row = (
        (
            await connection.execute(
                select(DocumentOriginRecord.origin, DocumentOriginRecord.origin_sha256).where(
                    DocumentOriginRecord.version_id == version_id
                )
            )
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        return None
    try:
        origin = DocumentOrigin.model_validate(row[0])
    except ValidationError:
        raise InvalidOriginError from None
    if canonical_origin_hash(origin) != row[1]:
        raise InvalidOriginError
    return origin
