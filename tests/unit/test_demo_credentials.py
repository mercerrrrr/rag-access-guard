import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from tests.unit.test_demo_ownership import marker_for
from tests.unit.test_demo_seed_plan import access_fixture

from rag_access_guard_api.demo import private_write
from rag_access_guard_api.demo.access import SeedUser
from rag_access_guard_api.demo.credentials import (
    CredentialJournal,
    CredentialRecovery,
    ensure_credentials,
)
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.private_write import private_directory


def test_credentials_are_durable_before_account_commit(tmp_path: Path) -> None:
    # Given: a fresh private journal target.
    path = tmp_path / "private" / "credentials.json"
    # When: credentials are prepared before any database account insertion.
    journal = ensure_credentials(path, CredentialRecovery(marker_for(), access_fixture()))
    # Then: the generated value is private, durable, and sufficient for ordinary login.
    assert path.is_file()
    assert len(journal.entries) == 1
    assert len(journal.entries[0].password.get_secret_value()) >= 24


def test_repeat_preserves_original_credential_bytes(tmp_path: Path) -> None:
    # Given: the durable original journal and its independent password.
    path = tmp_path / "private" / "credentials.json"
    first = ensure_credentials(path, CredentialRecovery(marker_for(), access_fixture()))
    original = path.read_bytes() if path.exists() else b""
    # When: an interrupted apply resumes.
    second = ensure_credentials(path, CredentialRecovery(marker_for(), access_fixture()))
    # Then: it reuses exactly the durable credential without rewriting the artifact.
    assert path.is_file()
    assert second == first
    assert path.read_bytes() == original


def test_additional_user_gets_recoverable_credential_without_original_rewrite(
    tmp_path: Path,
) -> None:
    # Given: original durable credentials and a newly declared independent account.
    path = tmp_path / "private" / "credentials.json"
    access = access_fixture()
    first = ensure_credentials(path, CredentialRecovery(marker_for(), access))
    original = path.read_bytes()
    expanded = access.model_copy(
        update={
            "users": (
                *access.users,
                SeedUser(login="newreader", display_name="New Reader", is_admin=False),
            )
        }
    )
    # When: the new account's credential is prepared and then recovered.
    second = ensure_credentials(path, CredentialRecovery(marker_for(), expanded))
    recovered = ensure_credentials(path, CredentialRecovery(marker_for(), expanded))
    # Then: both independent secrets survive, and the original artifact is immutable.
    assert {entry.login for entry in second.entries} == {"operator", "newreader"}
    assert second == recovered
    assert second.entries[0].password != second.entries[1].password
    assert first.entries[0] in second.entries
    assert path.read_bytes() == original


def test_existing_broad_parent_is_refused_without_creating_secret(tmp_path: Path) -> None:
    # Given: an existing unrelated directory whose ACL must not be silently tightened.
    if os.name == "posix":
        tmp_path.chmod(0o755)
    original = tmp_path.stat()
    path = tmp_path / "credentials.json"
    # When/Then: secret creation refuses the directory and leaves it unchanged.
    with pytest.raises(DemoOwnershipError, match=r"credential_(acl_unavailable|permissions)"):
        _ = ensure_credentials(path, CredentialRecovery(marker_for(), access_fixture()))
    assert not path.exists()
    assert tmp_path.stat() == original


@pytest.mark.skipif(os.name != "posix", reason="Actual POSIX owner and mode boundary")
def test_existing_owner_only_parent_accepts_private_credentials(tmp_path: Path) -> None:
    # Given: an existing directory explicitly restricted to the current POSIX owner.
    tmp_path.chmod(0o700)
    path = tmp_path / "credentials.json"
    # When: credentials are published through the real permission boundary.
    journal = ensure_credentials(path, CredentialRecovery(marker_for(), access_fixture()))
    # Then: the existing parent remains private and the secret file is owner-only.
    assert len(journal.entries) == 1
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    metadata = path.stat()
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_uid == os.getuid()


def test_exclusive_journal_race_recovers_one_durable_winner(tmp_path: Path) -> None:
    # Given: a protected parent and two independent credential generators.
    path = private_directory(tmp_path / "private") / "credentials.json"
    barrier = Barrier(2)

    def generate() -> CredentialJournal:
        _ = barrier.wait(timeout=10)
        return ensure_credentials(path, CredentialRecovery(marker_for(), access_fixture()))

    # When: exclusive publication races, with no caller allowed to replace the winner.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(generate) for _ in range(2)]
        journals = [future.result(timeout=30) for future in futures]
    # Then: both callers recover the same already durable original.
    assert journals[0] == journals[1]
    assert path.is_file()


@pytest.mark.skipif(os.name != "nt", reason="Actual native Windows ACL subprocess boundary")
@pytest.mark.parametrize("script", ["exit 1", "Start-Sleep -Seconds 20"])
def test_failed_or_timed_out_native_acl_creates_no_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    # Given: a real native ACL child that fails or exceeds its native timeout.
    path = tmp_path / "private" / "credentials.json"
    monkeypatch.setattr(private_write, "_WINDOWS_ACL", script)
    # When/Then: the failure is sanitized before any recoverable credential bytes exist.
    with pytest.raises(DemoOwnershipError, match="credential_acl_unavailable") as error:
        _ = ensure_credentials(path, CredentialRecovery(marker_for(), access_fixture()))
    assert not path.exists()
    assert script not in str(error.value)
    assert str(path) not in str(error.value)


def test_lost_completed_addition_is_not_regenerated(tmp_path: Path) -> None:
    # Given: a completed additional account whose immutable recovery sidecar is lost.
    path = tmp_path / "private" / "credentials.json"
    access = access_fixture()
    _ = ensure_credentials(path, CredentialRecovery(marker_for(), access))
    expanded = access.model_copy(
        update={
            "users": (
                *access.users,
                SeedUser(login="newreader", display_name="Reader", is_admin=False),
            )
        }
    )
    _ = ensure_credentials(path, CredentialRecovery(marker_for(), expanded))
    addition = next(path.parent.glob("credentials.user-*.json"))
    addition.unlink()
    original = path.read_bytes()
    # When/Then: resume refuses replacement credentials for the completed server identity.
    with pytest.raises(DemoOwnershipError, match="credential_missing"):
        _ = ensure_credentials(
            path, CredentialRecovery(marker_for(), expanded, frozenset({"operator", "newreader"}))
        )
    assert not addition.exists()
    assert path.read_bytes() == original
