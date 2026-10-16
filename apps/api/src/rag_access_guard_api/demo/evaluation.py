"""Pure dataset admission, with no connection, inference, or filesystem mutation."""

import re
from collections import Counter
from hashlib import sha256
from typing import Final, assert_never, override

from rag_access_guard_api.demo.evaluation_contracts import (
    DemoEvaluationActors,
    DemoEvaluationCase,
    DemoEvaluationDataset,
    EvaluationSplit,
)

CASE_COUNT: Final = 60
ACTOR_COUNT: Final = 3


class DemoDatasetError(ValueError):
    """Stable non-content-bearing admission failure."""

    def __init__(self, code: str) -> None:
        """Keep safe error codes typed while permitting context-manager traceback mutation."""
        self.code: str = code
        super().__init__(code)

    @override
    def __str__(self) -> str:
        return self.code


def require_frozen_gold(dataset: DemoEvaluationDataset) -> None:
    """Execution needs explicit pre-answer review provenance; validate accepts drafts."""
    review = dataset.gold_review
    if dataset.gold_frozen_at is None or review is None or not review.reviewer.strip():
        msg = "human_gold_required"
        raise DemoDatasetError(msg)
    if review.reviewed_at > dataset.gold_frozen_at:
        msg = "gold_review_after_freeze"
        raise DemoDatasetError(msg)


def require_valid_split(
    dataset: DemoEvaluationDataset, split: EvaluationSplit
) -> tuple[DemoEvaluationCase, ...]:
    """Return only the requested admitted split, never holdout during calibration."""
    validate_dataset(dataset)
    return tuple(case for case in dataset.cases if case.split == split)


def validate_dataset(dataset: DemoEvaluationDataset) -> None:
    """Enforce the exact 60-case protocol and all locally provable gold links."""
    if dataset.computed_hash() != dataset.dataset_sha256:
        msg = "dataset_hash_mismatch"
        raise DemoDatasetError(msg)
    actors = dataset.actors
    logins = (actors.student_login, actors.teacher_login, actors.staff_login)
    if len(set(logins)) != ACTOR_COUNT or any(
        not login.strip() or login != login.strip().lower() for login in logins
    ):
        raise DemoDatasetError(code="invalid_actor_bindings")
    if len(dataset.cases) != CASE_COUNT:
        msg = "case_count"
        raise DemoDatasetError(msg)
    if len({case.id for case in dataset.cases}) != CASE_COUNT:
        msg = "duplicate_case_id"
        raise DemoDatasetError(msg)
    questions = [" ".join(case.question.casefold().split()) for case in dataset.cases]
    if len(set(questions)) != CASE_COUNT:
        msg = "duplicate_question"
        raise DemoDatasetError(msg)
    families: dict[str, set[EvaluationSplit]] = {}
    for case in dataset.cases:
        families.setdefault(case.semantic_family, set()).add(case.split)
        _validate_case(case, actors)
    if any(len(splits) > 1 for splits in families.values()):
        msg = "semantic_family_overlap"
        raise DemoDatasetError(msg)
    _validate_distribution(dataset)


def _validate_distribution(dataset: DemoEvaluationDataset) -> None:
    actors = dataset.actors
    counts = Counter((c.split, c.audience, c.expected_behavior) for c in dataset.cases)
    expected = {
        (split, audience, behavior): count
        for split, answers, abstains in (("calibration", 8, 2), ("holdout", 16, 4))
        for audience in ("student", "employee")
        for behavior, count in (("answerable", answers), ("abstain", abstains))
    }
    if counts != expected:
        msg = "split_balance"
        raise DemoDatasetError(msg)
    principals = Counter(c.principal_login for c in dataset.cases)
    if principals != {actors.student_login: 30, actors.teacher_login: 15, actors.staff_login: 15}:
        msg = "principal_balance"
        raise DemoDatasetError(msg)
    for split, answers, abstains in (("calibration", 4, 1), ("holdout", 8, 2)):
        for login in (actors.teacher_login, actors.staff_login):
            for behavior, count in (("answerable", answers), ("abstain", abstains)):
                if (
                    sum(
                        c.split == split
                        and c.principal_login == login
                        and c.expected_behavior == behavior
                        for c in dataset.cases
                    )
                    != count
                ):
                    raise DemoDatasetError(code="employee_split_balance")
    demo = tuple(case for case in dataset.cases if case.is_demo_question)
    if Counter(c.audience for c in demo) != {"student": 5, "employee": 5} or any(
        c.split != "holdout" or c.expected_behavior != "answerable" for c in demo
    ):
        msg = "demo_selection"
        raise DemoDatasetError(msg)


def _validate_case(case: DemoEvaluationCase, actors: DemoEvaluationActors) -> None:
    if not re.search(r"[\u0410-\u044f\u0401\u0451]", case.question):
        msg = "russian_question_required"
        raise DemoDatasetError(msg)
    if case.principal_login not in {actors.student_login, actors.teacher_login, actors.staff_login}:
        raise DemoDatasetError(code="unknown_actor")
    if (case.audience == "student") != (case.principal_login == actors.student_login):
        msg = "principal_audience_mismatch"
        raise DemoDatasetError(msg)
    facts = {fact.id for fact in case.required_facts}
    if len(facts) != len(case.required_facts):
        msg = "duplicate_fact_id"
        raise DemoDatasetError(msg)
    if len(set(case.allowed_version_ids)) != len(case.allowed_version_ids):
        msg = "duplicate_allowed_version"
        raise DemoDatasetError(msg)
    match case.expected_behavior:
        case "abstain":
            if not case.abstain_reason or facts or case.evidence_sets or case.is_demo_question:
                msg = "abstain_gold"
                raise DemoDatasetError(msg)
        case "answerable":
            if not facts or not case.evidence_sets or not case.allowed_version_ids:
                msg = "answerable_gold"
                raise DemoDatasetError(msg)
            _validate_evidence_sets(case, facts)
        case _:
            assert_never(case.expected_behavior)


def _validate_evidence_sets(case: DemoEvaluationCase, facts: set[str]) -> None:
    evidence_ids: dict[str, str] = {}
    if len({value.id for value in case.evidence_sets}) != len(case.evidence_sets):
        raise DemoDatasetError(code="duplicate_evidence_set")
    for alternative in case.evidence_sets:
        covered: set[str] = set()
        for evidence in alternative.evidence:
            encoded = evidence.model_dump_json()
            if evidence.id in evidence_ids and evidence_ids[evidence.id] != encoded:
                raise DemoDatasetError(code="evidence_id_conflict")
            evidence_ids[evidence.id] = encoded
            if (
                evidence.span_end - evidence.span_start != len(evidence.supporting_text)
                or sha256(evidence.supporting_text.encode()).hexdigest()
                != evidence.supporting_sha256
                or evidence.version_id not in case.allowed_version_ids
                or not set(evidence.fact_ids) <= facts
            ):
                raise DemoDatasetError(code="evidence_integrity")
            covered.update(evidence.fact_ids)
        if covered != facts:
            raise DemoDatasetError(code="incomplete_evidence_set")
