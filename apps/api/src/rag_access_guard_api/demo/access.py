"""Typed external authorization definition; logical role keys preserve API code grammar."""

from typing import Annotated, Literal, Self, assert_never

from pydantic import Field, model_validator

from rag_access_guard_api.schemas.demo_corpus import CorpusId, CorpusValue

type Login = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.@-]{0,253}$")]
type ServiceCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
type Label = Annotated[str, Field(min_length=1, max_length=200)]


class SeedUser(CorpusValue):
    """One user identity; passwords are generated outside this definition."""

    login: Login
    display_name: Label
    is_admin: bool


class SeedRole(CorpusValue):
    """Explicit logical identity mapped to the unchanged native role grammar."""

    code: CorpusId
    service_code: ServiceCode
    display_name: Label


class SeedMembership(CorpusValue):
    """One independently completed authorization step."""

    user_login: Login
    role_code: CorpusId


class SeedGrant(CorpusValue):
    """An explicit user or role subject and a corpus logical document key."""

    subject_kind: Literal["user", "role"]
    subject_key: Login | CorpusId
    document_key: CorpusId


class AccessDefinition(CorpusValue):
    """The private matrix, with exactly one bootstrap administrator."""

    schema_version: Literal[1]
    users: Annotated[tuple[SeedUser, ...], Field(min_length=1, max_length=100)]
    roles: Annotated[tuple[SeedRole, ...], Field(max_length=100)]
    memberships: Annotated[tuple[SeedMembership, ...], Field(max_length=10000)]
    grants: Annotated[tuple[SeedGrant, ...], Field(max_length=10000)]

    @model_validator(mode="after")
    def coherent_matrix(self) -> Self:
        """Reject collisions, undefined subjects, and ambiguous bootstrap authority."""
        users = {user.login for user in self.users}
        roles = {role.code for role in self.roles}
        memberships = {(item.user_login, item.role_code) for item in self.memberships}
        grants = {(item.subject_kind, item.subject_key, item.document_key) for item in self.grants}
        if (
            len(users) != len(self.users)
            or len(roles) != len(self.roles)
            or len({role.service_code for role in self.roles}) != len(self.roles)
            or sum(user.is_admin for user in self.users) != 1
            or len(memberships) != len(self.memberships)
            or len(grants) != len(self.grants)
            or any(user not in users or role not in roles for user, role in memberships)
            or any(not item.display_name.strip() for item in (*self.users, *self.roles))
        ):
            message = "invalid_access_definition"
            raise ValueError(message)
        for grant in self.grants:
            match grant.subject_kind:
                case "user":
                    known_subjects = users
                case "role":
                    known_subjects = roles
                case _:
                    assert_never(grant.subject_kind)
            if grant.subject_key not in known_subjects:
                message = "invalid_access_definition"
                raise ValueError(message)
        return self
