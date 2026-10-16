from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from rag_access_guard_api.demo.evaluation_contracts import (
    DemoEvaluationActors,
    DemoEvaluationCase,
    DemoEvaluationDataset,
    DemoEvidence,
    DemoEvidenceSet,
    DemoRequiredFact,
)


def dataset() -> DemoEvaluationDataset:
    cases: list[DemoEvaluationCase] = []
    for index in range(60):
        calibration = index < 20
        local = index if calibration else index - 20
        student = local % 2 == 0
        answerable = local < (16 if calibration else 32)
        identity = UUID(int=index + 1)
        evidence = DemoEvidence(
            id=f"e{index}",
            document_id=identity,
            version_id=identity,
            chunk_id=identity,
            span_start=0,
            span_end=4,
            supporting_text="факт",
            supporting_sha256=sha256("факт".encode()).hexdigest(),
            fact_ids=("fact",),
        )
        cases.append(
            DemoEvaluationCase(
                id=f"case-{index}",
                semantic_family=f"family-{index}",
                split="calibration" if calibration else "holdout",
                audience="student" if student else "employee",
                principal_login="demo.student"
                if student
                else "demo.teacher"
                if (local // 2) % 2 == 0
                else "demo.staff",
                question=f"Как получить учебный факт номер {index}?",
                expected_behavior="answerable" if answerable else "abstain",
                abstain_reason=None if answerable else "Нет доступного основания",
                required_facts=(DemoRequiredFact(id="fact", text="факт", synthetic=True),)
                if answerable
                else (),
                evidence_sets=(DemoEvidenceSet(id=f"set-{index}", evidence=(evidence,)),)
                if answerable
                else (),
                forbidden_claims=(),
                allowed_version_ids=(identity,),
                is_demo_question=20 <= index < 30,
            )
        )
    value = DemoEvaluationDataset(
        schema_version=1,
        dataset_id="synthetic-test",
        revision="1",
        corpus_sha256="a" * 64,
        actors=DemoEvaluationActors(
            student_login="demo.student", teacher_login="demo.teacher", staff_login="demo.staff"
        ),
        dataset_sha256="0" * 64,
        profile_hash="b" * 64,
        access_matrix_sha256="c" * 64,
        policy_revision=1,
        allowed_snapshot_sha256="d" * 64,
        gold_frozen_at=None,
        gold_review=None,
        cases=tuple(cases),
    )
    return value.model_copy(update={"dataset_sha256": value.computed_hash()})


def reviewed_at() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)
