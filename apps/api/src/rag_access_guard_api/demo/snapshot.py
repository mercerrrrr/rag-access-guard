"""Metadata-only projections of real rows, kept separate from planning and mutation."""

from dataclasses import replace
from typing import assert_never

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.demo.access import AccessDefinition, SeedRole, SeedUser
from rag_access_guard_api.demo.contracts import DemoMarker
from rag_access_guard_api.demo.ledger import read_ledger
from rag_access_guard_api.demo.plan import SeedBinding, SeedLedger, SeedState, definition_hash
from rag_access_guard_api.persistence import (
    Document,
    DocumentGrant,
    DocumentOriginRecord,
    DocumentVersion,
    Role,
    User,
    UserRole,
)


async def snapshot(
    connection: AsyncConnection, marker: DemoMarker, access: AccessDefinition
) -> tuple[SeedLedger, SeedState]:
    """Caller holds policy first; no content, password, or vector columns are selected."""
    ledger = await read_ledger(connection, marker)
    tracked = {binding.key: binding for binding in ledger.bindings}
    objects: list[SeedBinding] = []
    users = {
        row[1]: row
        for row in (
            await connection.execute(
                select(User.id, User.login, User.display_name, User.is_admin, User.is_active)
            )
        )
        .tuples()
        .all()
    }
    roles = {
        row[1]: row
        for row in (await connection.execute(select(Role.id, Role.code, Role.display_name)))
        .tuples()
        .all()
    }
    for wanted in access.users:
        row = users.get(wanted.login)
        if row is not None and row[4]:
            value = SeedUser(login=row[1], display_name=row[2], is_admin=row[3])
            objects.append(
                SeedBinding(
                    f"user:{wanted.login}", row[0], definition_sha256=definition_hash(value)
                )
            )
    for wanted in access.roles:
        key = f"role:{wanted.code}"
        binding = tracked.get(key)
        row = (
            next((row for row in roles.values() if row[0] == binding.object_id), None)
            if binding is not None
            else roles.get(wanted.service_code)
        )
        if row is not None:
            value = SeedRole(code=wanted.code, service_code=row[1], display_name=row[2])
            objects.append(SeedBinding(key, row[0], definition_sha256=definition_hash(value)))
    documents = (
        (
            await connection.execute(
                select(
                    Document.id,
                    Document.is_active,
                    Document.active_version_id,
                    DocumentVersion.status,
                    DocumentVersion.content_sha256,
                    DocumentOriginRecord.origin_sha256,
                )
                .join(DocumentVersion, Document.active_version_id == DocumentVersion.id)
                .join(DocumentOriginRecord, DocumentVersion.id == DocumentOriginRecord.version_id)
            )
        )
        .tuples()
        .all()
    )
    for binding in ledger.bindings:
        if not binding.key.startswith("document:"):
            continue
        row = next((row for row in documents if row[0] == binding.object_id), None)
        if row is not None and row[1] and row[3] == "ready":
            objects.append(
                replace(binding, version_id=row[2], upload_sha256=row[4], origin_sha256=row[5])
            )
    objects.extend(await _authorization_projection(connection, access, objects))
    return ledger, SeedState(access, tuple(objects))


async def _authorization_projection(
    connection: AsyncConnection, access: AccessDefinition, resources: list[SeedBinding]
) -> list[SeedBinding]:
    identifiers = {binding.key: binding.object_id for binding in resources}
    objects: list[SeedBinding] = []
    membership_rows = set(
        (await connection.execute(select(UserRole.user_id, UserRole.role_id))).tuples()
    )
    for wanted in access.memberships:
        user_id, role_id = (
            identifiers.get(f"user:{wanted.user_login}"),
            identifiers.get(f"role:{wanted.role_code}"),
        )
        if user_id is not None and role_id is not None and (user_id, role_id) in membership_rows:
            objects.append(
                SeedBinding(
                    f"membership:{wanted.user_login}:{wanted.role_code}",
                    user_id,
                    version_id=role_id,
                )
            )
    grant_rows = (
        (
            await connection.execute(
                select(
                    DocumentGrant.id,
                    DocumentGrant.document_id,
                    DocumentGrant.user_id,
                    DocumentGrant.role_id,
                )
            )
        )
        .tuples()
        .all()
    )
    for wanted in access.grants:
        subject_id = identifiers.get(f"{wanted.subject_kind}:{wanted.subject_key}")
        document_id = identifiers.get(f"document:{wanted.document_key}")
        match wanted.subject_kind:
            case "user":
                subject_index = 2
            case "role":
                subject_index = 3
            case _:
                assert_never(wanted.subject_kind)
        row = next(
            (
                row
                for row in grant_rows
                if row[1] == document_id and row[subject_index] == subject_id
            ),
            None,
        )
        if subject_id is not None and document_id is not None and row is not None:
            objects.append(
                SeedBinding(
                    f"grant:{wanted.subject_kind}.{wanted.subject_key}:{wanted.document_key}",
                    row[0],
                )
            )
    return objects
