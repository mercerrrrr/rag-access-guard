"""Durable private credential recovery before the first account commit."""

import json
import secrets
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import SecretStr

from rag_access_guard_api.demo.access import AccessDefinition
from rag_access_guard_api.demo.contracts import DemoMarker
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.files import load_private_json
from rag_access_guard_api.demo.paths import private_path
from rag_access_guard_api.demo.private_write import (
    exclusive_private_write,
    owner_only,
    private_directory,
)
from rag_access_guard_api.schemas.demo_corpus import CorpusValue


class CredentialEntry(CorpusValue):
    """Passwords leave memory only for the deliberately private journal."""

    login: str
    password: SecretStr


class CredentialJournal(CorpusValue):
    """Immutable original credentials bound to a single owned instance."""

    marker: DemoMarker
    entries: tuple[CredentialEntry, ...]


@dataclass(frozen=True, slots=True)
class CredentialRecovery:
    """Bind requested identities to durable completed-account recovery obligations."""

    marker: DemoMarker
    access: AccessDefinition
    completed_users: frozenset[str] = frozenset()


def ensure_credentials(path: Path, recovery: CredentialRecovery) -> CredentialJournal:
    """Exclusively persist independently generated credentials, or resume the original."""
    marker, access, completed_users = recovery.marker, recovery.access, recovery.completed_users
    path = private_path(path)
    _ = private_directory(path.parent)
    if path.exists():
        owner_only(path)
        journal = load_private_json(path, CredentialJournal)
        if journal.marker != marker or len({entry.login for entry in journal.entries}) != len(
            journal.entries
        ):
            raise DemoOwnershipError(code="credential_identity_mismatch")
        return extend_credentials(path, journal, recovery)
    if completed_users:
        raise DemoOwnershipError(code="credential_missing")
    entries = tuple(
        CredentialEntry(login=user.login, password=SecretStr(secrets.token_urlsafe(24)))
        for user in access.users
    )
    journal = CredentialJournal(marker=marker, entries=entries)
    try:
        exclusive_private_write(path, encoded_journal(journal))
    except DemoOwnershipError:
        if path.exists():
            return ensure_credentials(path, recovery)
        raise
    return journal


def encoded_journal(journal: CredentialJournal) -> bytes:
    """Cleartext is encoded solely for a protected exclusive-create journal artifact."""
    return json.dumps(
        {
            "marker": journal.marker.model_dump(mode="json"),
            "entries": [
                {"login": entry.login, "password": entry.password.get_secret_value()}
                for entry in journal.entries
            ],
        },
        separators=(",", ":"),
    ).encode()


def extend_credentials(
    path: Path, journal: CredentialJournal, recovery: CredentialRecovery
) -> CredentialJournal:
    """New users receive immutable per-login additions without replacing the original."""
    entries = list(journal.entries)
    for user in recovery.access.users:
        if any(entry.login == user.login for entry in entries):
            continue
        digest = sha256(user.login.encode()).hexdigest()
        addition = path.with_name(f"{path.stem}.user-{digest}.json")
        if not addition.exists():
            if user.login in recovery.completed_users:
                raise DemoOwnershipError(code="credential_missing")
            value = CredentialJournal(
                marker=journal.marker,
                entries=(
                    CredentialEntry(
                        login=user.login, password=SecretStr(secrets.token_urlsafe(24))
                    ),
                ),
            )
            try:
                exclusive_private_write(addition, encoded_journal(value))
            except DemoOwnershipError:
                if not addition.exists():
                    raise
        owner_only(addition)
        saved = load_private_json(addition, CredentialJournal)
        if (
            saved.marker != journal.marker
            or len(saved.entries) != 1
            or saved.entries[0].login != user.login
        ):
            raise DemoOwnershipError(code="credential_identity_mismatch")
        entries.extend(saved.entries)
    return journal.model_copy(update={"entries": tuple(entries)})
