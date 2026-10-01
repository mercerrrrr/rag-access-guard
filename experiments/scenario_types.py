"""Versioned input schema for synthetic security scenarios."""

from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

type Key = Annotated[str, Field(min_length=1, pattern=r"^[a-z][a-z0-9_.-]*$")]
type Principal = Literal["student", "teacher", "staff", "admin"]
type Role = Literal["student", "teacher", "staff"]


class FrozenModel(BaseModel):
    """Reject unknown fields and coercion at the JSON boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)


class ScenarioClass(StrEnum):
    """Threat and control classes required by the experiment protocol."""

    ALLOW = "allow"
    DENY = "deny"
    NO_CONTEXT = "no_context"
    ADMIN_WITHOUT_GRANT = "admin_without_grant"
    DIRECT_REVOKE = "direct_revoke"
    ROLE_REVOKE = "role_revoke"
    MEMBERSHIP_REMOVAL = "membership_removal"
    ALTERNATIVE_ALLOW = "alternative_allow"
    HISTORY = "history"
    VERSION_REPLACEMENT = "version_replacement"
    PROVENANCE_MISSING = "provenance_missing"
    PROVENANCE_SWAP = "provenance_swap"
    POLICY_FAILURE = "policy_failure"
    REVOKE_BEFORE_RELEASE = "revoke_before_release"
    RELEASE_BEFORE_REVOKE = "release_before_revoke"
    STORED_READ = "stored_read"
    SOURCE_READ = "source_read"
    DOCUMENT_LIST = "document_list"
    UNKNOWN_SOURCE = "unknown_source"
    LOGOUT = "logout"
    SESSION_EXPIRY = "session_expiry"
    INACTIVE_USER = "inactive_user"
    MANUAL_PASTE = "manual_paste"
    PROMPT_INJECTION = "prompt_injection"
    UNTRUSTED_MARKUP = "untrusted_markup"
    FALSE_ATTACHMENT = "false_attachment"
    OWNERSHIP = "ownership"


class DirectGrant(FrozenModel):
    """One initial user-to-document allow edge."""

    kind: Literal["direct"]
    document: Key
    user: Principal


class RoleGrant(FrozenModel):
    """Initial role grant and the membership needed to exercise it."""

    kind: Literal["role"]
    document: Key
    role: Role
    members: tuple[Principal, ...]


type Grant = Annotated[DirectGrant | RoleGrant, Field(discriminator="kind")]


class Barrier(FrozenModel):
    """Event of an earlier ask at which a concurrent action commits."""

    ask_id: Key
    event: Literal["model_generated", "release_committed"]


class ActionBase(FrozenModel):
    """Stable action identity and explicit execution phase."""

    id: Key
    phase: Literal["prepare", "generation", "after_release", "read"]


class Ask(ActionBase):
    """Ask using the scenario question or an explicit follow-up."""

    kind: Literal["ask"]
    user_input: str | None = None


class DirectRevoke(ActionBase):
    """Withdraw one direct grant, optionally during a prior ask."""

    kind: Literal["revoke_direct"]
    document: Key
    user: Principal
    barrier: Barrier | None = None


class RoleRevoke(ActionBase):
    """Withdraw the document grant of a role."""

    kind: Literal["revoke_role"]
    document: Key
    role: Role


class RemoveMembership(ActionBase):
    """Remove one user from a granted role."""

    kind: Literal["remove_membership"]
    user: Principal
    role: Role


class ReplaceVersion(ActionBase):
    """Reindex an existing document with the specified fixture bytes."""

    kind: Literal["replace_version"]
    document: Key
    replacement: Key


class SessionChange(ActionBase):
    """Invalidate the actor's session or active-user state."""

    kind: Literal["logout", "expire_session", "deactivate_principal"]


class ReadThread(ActionBase):
    """Read the thread established by an earlier ask."""

    kind: Literal["read_thread"]
    ask_id: Key
    reader: Principal | None = None


class ReadSource(ActionBase):
    """Open an earlier source witness or an unknown synthetic identity."""

    kind: Literal["read_source"]
    document: Key
    ask_id: Key | None = None
    unknown: bool = False


class ReadDocuments(ActionBase):
    """Read the actor's current accessible-document list."""

    kind: Literal["read_documents"]


class PolicyFailure(ActionBase):
    """Make the policy adapter unavailable at the next prepare boundary."""

    kind: Literal["policy_failure"]


class TamperProvenance(ActionBase):
    """Explicit integrity fault injected only by the experimental host."""

    kind: Literal["tamper_provenance"]
    document: Key
    mode: Literal["missing", "swap", "false_attachment"]
    attached_document: Key | None = None


type Action = Annotated[
    Ask
    | DirectRevoke
    | RoleRevoke
    | RemoveMembership
    | ReplaceVersion
    | SessionChange
    | ReadThread
    | ReadSource
    | ReadDocuments
    | PolicyFailure
    | TamperProvenance,
    Field(discriminator="kind"),
]


class SurfaceExpectation(FrozenModel):
    """Structural oracle at a named action, not a marker-text judge."""

    action_id: Key
    surface: Literal["model_context", "release", "stored_read", "source_read", "document_list"]
    authorization: Literal["allow", "deny", "neutral"]
    documents: tuple[Key, ...]
    forbidden_documents: tuple[Key, ...]
    allowed_fact_keys: tuple[Key, ...]
    http_status: Literal[200, 401, 403, 404, 503] | None = None
    body: Literal["protected", "redacted", "neutral", "list"]
    llm_calls: Annotated[int, Field(ge=0)] | None = None
    persisted: Literal["available", "neutral", "unchanged"]


class Expected(FrozenModel):
    """Required per-surface observations for the guarded arm."""

    checks: Annotated[tuple[SurfaceExpectation, ...], Field(min_length=1)]


class Scenario(FrozenModel):
    """One synthetic case with ordered actions and independently specified outcomes."""

    id: Key
    schema_version: Literal[1]
    class_name: ScenarioClass
    principal_key: Principal
    initial_grants: tuple[Grant, ...]
    user_input: Annotated[str, Field(min_length=1)]
    actions: tuple[Action, ...]
    expected: Expected
