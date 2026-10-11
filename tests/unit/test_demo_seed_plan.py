from pathlib import Path
from uuid import UUID

import pytest
from tests.helpers.demo_corpus_factory import corpus_fixture

from rag_access_guard_api.demo.access import AccessDefinition, SeedGrant, SeedRole, SeedUser
from rag_access_guard_api.demo.plan import SeedBinding, SeedLedger, SeedState, plan_seed
from rag_access_guard_api.services.demo_corpus import load_manifest


def access_fixture() -> AccessDefinition:
    return AccessDefinition(
        schema_version=1,
        users=(SeedUser(login="operator", display_name="Operator", is_admin=True),),
        roles=(SeedRole(code="readers", service_code="readers", display_name="Readers"),),
        memberships=(),
        grants=(SeedGrant(subject_kind="user", subject_key="operator", document_key="item-0"),),
    )


def test_new_keys_are_planned_in_stable_order(tmp_path: Path) -> None:
    # Given: a new external corpus and access definition with no completed steps.
    manifest = load_manifest(corpus_fixture(tmp_path / "corpus"))
    # When: a pure plan compares desired and current state.
    plan = plan_seed(manifest, SeedLedger(), SeedState(access_fixture()))
    # Then: every desired logical object appears once in stable order.
    assert len(plan.planned_new_objects) == 27
    assert plan.planned_new_objects == tuple(sorted(plan.planned_new_objects))
    assert "user:operator" in plan.planned_new_objects
    assert "grant:user.operator:item-0" in plan.planned_new_objects


def test_revoked_completed_grant_is_not_recreated(tmp_path: Path) -> None:
    # Given: a completed grant whose actual row was removed by the operator.
    manifest = load_manifest(corpus_fixture(tmp_path / "corpus"))
    binding = SeedBinding("grant:user.operator:item-0", UUID(int=1))
    # When: a repeat is planned with the completed step but no live grant.
    plan = plan_seed(manifest, SeedLedger((binding,)), SeedState(access_fixture()))
    # Then: it records the operator change without planning grant repair.
    assert binding.key not in plan.planned_new_objects
    assert plan.preserved_operator_changes == (binding.key,)


def test_missing_tracked_user_is_drift(tmp_path: Path) -> None:
    # Given: an operator removed a completed account.
    manifest = load_manifest(corpus_fixture(tmp_path / "corpus"))
    binding = SeedBinding("user:operator", UUID(int=1))
    # When: the pure planner sees the missing server identity.
    plan = plan_seed(manifest, SeedLedger((binding,)), SeedState(access_fixture()))
    # Then: it refuses to restore the account.
    assert plan.drift == (binding.key,)
    assert binding.key not in plan.planned_new_objects


def test_user_and_role_subjects_with_same_key_remain_independent(tmp_path: Path) -> None:
    # Given: two different allow paths whose subjects share a logical spelling.
    manifest = load_manifest(corpus_fixture(tmp_path / "corpus"))
    access = AccessDefinition(
        schema_version=1,
        users=(SeedUser(login="operator", display_name="Operator", is_admin=True),),
        roles=(SeedRole(code="operator", service_code="operator_role", display_name="Role"),),
        memberships=(),
        grants=(
            SeedGrant(subject_kind="user", subject_key="operator", document_key="item-0"),
            SeedGrant(subject_kind="role", subject_key="operator", document_key="item-0"),
        ),
    )
    # When: the planner constructs both logical grant steps.
    plan = plan_seed(manifest, SeedLedger(), SeedState(access))
    # Then: direct and role grants have distinct completed-step identities.
    assert "grant:user.operator:item-0" in plan.planned_new_objects
    assert "grant:role.operator:item-0" in plan.planned_new_objects


def test_changed_upload_reports_hash_conflict(tmp_path: Path) -> None:
    # Given: a completed ready version with a different upload content hash.
    manifest = load_manifest(corpus_fixture(tmp_path / "corpus"))
    binding = SeedBinding("document:item-0", UUID(int=1), UUID(int=2), "a" * 64, "b" * 64)
    # When: the desired document is compared with the same actual binding.
    plan = plan_seed(manifest, SeedLedger((binding,)), SeedState(access_fixture(), (binding,)))
    # Then: only safe key and digest metadata describes the content update.
    assert len(plan.content_changes) == 1
    assert plan.content_changes[0].key == "item-0"
    assert plan.content_changes[0].old_upload_sha256 == "a" * 64
    assert "document:item-0" not in plan.planned_new_objects


@pytest.mark.parametrize("login", ["reader@example.test", "r" * 254])
def test_every_accepted_login_is_addressable_by_a_user_grant(login: str) -> None:
    # Given: a valid native login beyond the narrower corpus logical-key grammar.
    user = SeedUser(login=login, display_name="Reader", is_admin=True)
    # When: the declared user is referenced by an explicit user grant.
    access = AccessDefinition(
        schema_version=1,
        users=(user,),
        roles=(),
        memberships=(),
        grants=(SeedGrant(subject_kind="user", subject_key=login, document_key="item-0"),),
    )
    # Then: exact kind coherence preserves the full native login, with no normalization.
    assert access.grants[0].subject_key == user.login
