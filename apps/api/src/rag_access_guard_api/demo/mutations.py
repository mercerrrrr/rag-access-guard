"""Authenticated atomic policy mutations, each with a marker and completion recheck."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import assert_never

from rag_access_guard_api.demo.access import AccessDefinition
from rag_access_guard_api.demo.contracts import DemoMarker
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.ledger import complete_step, lock_marker, read_ledger
from rag_access_guard_api.demo.plan import SeedBinding, SeedLedger, definition_hash
from rag_access_guard_api.services import grants, roles
from rag_access_guard_api.services.indexing import require_index_authority
from rag_access_guard_api.services.security import MutationUoW, PolicyUnitOfWork


@dataclass(frozen=True, slots=True)
class SeedAuthority:
    """An ordinary authenticated session and the owned instance for step transactions."""

    policy: PolicyUnitOfWork
    marker: DemoMarker
    session_token: str = field(repr=False)
    csrf_token: str = field(repr=False)


@asynccontextmanager
async def seed_transaction(
    authority: SeedAuthority,
) -> AsyncGenerator[tuple[MutationUoW, SeedLedger]]:
    """Keep policy→session→marker→resource order; never run parsing or inference here."""
    async with authority.policy.mutation(authority.session_token) as uow:
        require_index_authority(uow, authority.csrf_token)
        await lock_marker(uow.connection, authority.marker)
        yield uow, await read_ledger(uow.connection, authority.marker)


def binding_for(ledger: SeedLedger, key: str) -> SeedBinding:
    """Resolve only an actually completed server identity, never synthesize a UUID."""
    binding = next((binding for binding in ledger.bindings if binding.key == key), None)
    if binding is None:
        raise DemoOwnershipError(code="incomplete_dependency")
    return binding


async def apply_access(authority: SeedAuthority, access: AccessDefinition) -> int:
    """Completed authorizations are skipped even when operators removed their rows."""
    created = 0
    for wanted in access.roles:
        key = f"role:{wanted.code}"
        async with seed_transaction(authority) as (uow, ledger):
            if any(binding.key == key for binding in ledger.bindings):
                continue
            role = await roles.create_role(
                uow, code=wanted.service_code, display_name=wanted.display_name
            )
            await complete_step(
                uow.connection,
                authority.marker,
                SeedBinding(key, role.id, definition_sha256=definition_hash(wanted)),
            )
            created += 1
    for wanted in access.memberships:
        key = f"membership:{wanted.user_login}:{wanted.role_code}"
        async with seed_transaction(authority) as (uow, ledger):
            if any(binding.key == key for binding in ledger.bindings):
                continue
            user = binding_for(ledger, f"user:{wanted.user_login}")
            role = binding_for(ledger, f"role:{wanted.role_code}")
            await roles.set_membership(uow, role.object_id, user.object_id, present=True)
            await complete_step(
                uow.connection,
                authority.marker,
                SeedBinding(key, user.object_id, version_id=role.object_id),
            )
            created += 1
    for wanted in access.grants:
        key = f"grant:{wanted.subject_kind}.{wanted.subject_key}:{wanted.document_key}"
        async with seed_transaction(authority) as (uow, ledger):
            if any(binding.key == key for binding in ledger.bindings):
                continue
            document = binding_for(ledger, f"document:{wanted.document_key}")
            subject = binding_for(ledger, f"{wanted.subject_kind}:{wanted.subject_key}")
            match wanted.subject_kind:
                case "user":
                    operation = grants.grant_user
                case "role":
                    operation = grants.grant_role
                case _:
                    assert_never(wanted.subject_kind)
            grant = await operation(uow, document.object_id, subject.object_id)
            await complete_step(uow.connection, authority.marker, SeedBinding(key, grant.id))
            created += 1
    return created
