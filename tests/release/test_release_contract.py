from dataclasses import replace

import pytest
from scripts.verify_release import ReleaseProof, validate_release


def complete_proof() -> ReleaseProof:
    return ReleaseProof(
        commit_sha="a" * 40,
        remote_sha="a" * 40,
        ci_sha="a" * 40,
        ci_status="success",
        wheel_install="pass",
        demo="pass",
        reproduction="pass",
    )


def test_release_refuses_missing_ci_proof() -> None:
    proof = replace(complete_proof(), ci_sha=None, ci_status="not_run")
    assert validate_release(proof) == ("missing successful CI for commit",)


def test_release_accepts_complete_matching_proof() -> None:
    assert validate_release(complete_proof()) == ()


@pytest.mark.parametrize("field", ["remote_sha", "ci_sha"])
def test_release_refuses_mismatched_commit(field: str) -> None:
    proof = replace(complete_proof(), **{field: "b" * 40})
    assert validate_release(proof)


@pytest.mark.parametrize("field", ["wheel_install", "demo", "reproduction"])
def test_release_refuses_unverified_surface(field: str) -> None:
    proof = replace(complete_proof(), **{field: "not_run"})
    assert validate_release(proof)


def test_release_refuses_invalid_commit_identity() -> None:
    proof = replace(complete_proof(), commit_sha="not-a-commit")
    assert validate_release(proof)
