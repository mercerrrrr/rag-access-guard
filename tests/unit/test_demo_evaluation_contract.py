import pytest
from pydantic import ValidationError
from tests.unit.demo_evaluation_fixtures import dataset, reviewed_at

from rag_access_guard_api.demo.evaluation import (
    DemoDatasetError,
    require_frozen_gold,
    validate_dataset,
)
from rag_access_guard_api.demo.evaluation_contracts import DemoGoldReview


def test_contract_exists_and_validates_balanced_fixture() -> None:

    value = dataset()
    # Then all 32 holdout answerable cases independently survive validation.
    validate_dataset(value)
    assert (
        sum(c.split == "holdout" and c.expected_behavior == "answerable" for c in value.cases) == 32
    )


def test_semantic_family_overlap_is_rejected() -> None:

    value = dataset()
    cases = list(value.cases)
    cases[0] = cases[0].model_copy(update={"semantic_family": "account-reset"})
    cases[20] = cases[20].model_copy(update={"semantic_family": "account-reset"})
    changed = value.model_copy(update={"cases": tuple(cases)})
    changed = changed.model_copy(update={"dataset_sha256": changed.computed_hash()})
    with pytest.raises(DemoDatasetError, match="semantic_family_overlap"):
        validate_dataset(changed)


def test_draft_gold_is_schema_valid_but_not_execution_ready() -> None:

    with pytest.raises(DemoDatasetError, match="human_gold_required"):
        require_frozen_gold(dataset())


def test_renamed_actor_bindings_keep_balanced_protocol() -> None:

    value = dataset()
    names = {"demo.student": "learner", "demo.teacher": "instructor", "demo.staff": "office"}
    actors = value.actors.model_copy(
        update={"student_login": "learner", "teacher_login": "instructor", "staff_login": "office"}
    )
    changed = value.model_copy(
        update={
            "actors": actors,
            "cases": tuple(
                c.model_copy(update={"principal_login": names[c.principal_login]})
                for c in value.cases
            ),
        }
    )
    changed = changed.model_copy(update={"dataset_sha256": changed.computed_hash()})
    validate_dataset(changed)


def test_gold_review_requires_explicit_method() -> None:

    with pytest.raises(ValidationError):
        _ = DemoGoldReview.model_validate(
            {"reviewer": "Technical source check", "reviewed_at": reviewed_at()}
        )


def test_explicit_technical_gold_freeze_allows_engineering_execution() -> None:

    review = DemoGoldReview(
        reviewer="Technical source check",
        reviewed_at=reviewed_at(),
        review_method="technical",
    )
    value = dataset().model_copy(update={"gold_review": review, "gold_frozen_at": reviewed_at()})
    require_frozen_gold(value)
