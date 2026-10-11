"""Pure stable comparison of desired logical keys, completed bindings, and current rows."""

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from pydantic import BaseModel

from rag_access_guard_api.demo.access import AccessDefinition
from rag_access_guard_api.schemas.demo_corpus import DemoCorpusManifest
from rag_access_guard_api.services.origin import canonical_origin_hash


@dataclass(frozen=True, slots=True)
class SeedBinding:
    """A completed server identity with immutable content and definition hashes."""

    key: str
    object_id: UUID
    version_id: UUID | None = None
    upload_sha256: str | None = None
    origin_sha256: str | None = None
    definition_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class SeedLedger:
    """Completed steps survive revoked authorization and interrupted apply."""

    bindings: tuple[SeedBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class SeedState:
    """Actual projections and the independently parsed external access matrix."""

    access: AccessDefinition
    objects: tuple[SeedBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class ContentChange:
    """Safe evidence of a version conflict without private document text."""

    key: str
    old_upload_sha256: str | None
    new_upload_sha256: str
    old_origin_sha256: str | None
    new_origin_sha256: str


@dataclass(frozen=True, slots=True)
class SeedPlan:
    """Pure classifications; missing completed authorizations are never repaired."""

    planned_new_objects: tuple[str, ...] = ()
    content_changes: tuple[ContentChange, ...] = ()
    drift: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    preserved_operator_changes: tuple[str, ...] = ()


def plan_seed(
    manifest: DemoCorpusManifest, ledger: SeedLedger, current_state: SeedState
) -> SeedPlan:
    """Compare values only; this function never connects or invokes a model."""
    desired = {binding.key: binding for binding in desired_bindings(manifest, current_state.access)}
    completed = {binding.key: binding for binding in ledger.bindings}
    actual = {binding.key: binding for binding in current_state.objects}
    new: list[str] = []
    changes: list[ContentChange] = []
    drift = [key for key in completed if key not in desired and key != "bootstrap"]
    unchanged: list[str] = []
    preserved: list[str] = []
    for key, wanted in sorted(desired.items()):
        tracked, current = completed.get(key), actual.get(key)
        if tracked is None:
            (new if current is None else drift).append(key)
        elif current is None:
            (preserved if key.startswith(("grant:", "membership:")) else drift).append(key)
        elif current != tracked or tracked.definition_sha256 != wanted.definition_sha256:
            drift.append(key)
        elif key.startswith("document:") and (tracked.upload_sha256, tracked.origin_sha256) != (
            wanted.upload_sha256,
            wanted.origin_sha256,
        ):
            changes.append(
                ContentChange(
                    key.removeprefix("document:"),
                    tracked.upload_sha256,
                    wanted.upload_sha256 or "",
                    tracked.origin_sha256,
                    wanted.origin_sha256 or "",
                )
            )
        else:
            unchanged.append(key)
    return SeedPlan(
        tuple(new), tuple(changes), tuple(sorted(set(drift))), tuple(unchanged), tuple(preserved)
    )


def definition_hash(value: BaseModel) -> str:
    """Bind complete typed metadata without exposing its contents in the ledger."""
    return sha256(value.model_dump_json().encode()).hexdigest()


def desired_bindings(
    manifest: DemoCorpusManifest, access: AccessDefinition
) -> tuple[SeedBinding, ...]:
    """Project logical steps using separate native identities and content digests."""
    desired = {
        f"document:{document.key}": SeedBinding(
            f"document:{document.key}",
            UUID(int=0),
            upload_sha256=document.upload_sha256,
            origin_sha256=canonical_origin_hash(document.origin),
        )
        for document in manifest.documents
    }
    for user in access.users:
        key = f"user:{user.login}"
        desired[key] = SeedBinding(key, UUID(int=0), definition_sha256=definition_hash(user))
    for role in access.roles:
        key = f"role:{role.code}"
        desired[key] = SeedBinding(key, UUID(int=0), definition_sha256=definition_hash(role))
    for membership in access.memberships:
        key = f"membership:{membership.user_login}:{membership.role_code}"
        desired[key] = SeedBinding(key, UUID(int=0))
    for grant in access.grants:
        key = f"grant:{grant.subject_kind}.{grant.subject_key}:{grant.document_key}"
        desired[key] = SeedBinding(key, UUID(int=0))
    return tuple(desired.values())
