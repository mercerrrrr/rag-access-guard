from dataclasses import replace
from json import dumps
from uuid import UUID

import pytest
from experiments.input_boundary import ModelRequestObservation
from experiments.observations import ArmResult, Observation
from experiments.scenario_types import Ask, Expected, Scenario, ScenarioClass, SurfaceExpectation

from experiments import metrics
from rag_access_guard import SourceRef

PUBLIC = SourceRef(document_id=UUID(int=1), document_version_id=UUID(int=2), chunk_id=UUID(int=3))
STAFF = SourceRef(document_id=UUID(int=4), document_version_id=UUID(int=5), chunk_id=UUID(int=6))


def expectation(action: str, *, allowed: bool = True) -> SurfaceExpectation:
    return SurfaceExpectation(
        action_id=action,
        surface="release",
        authorization="allow" if allowed else "neutral",
        documents=("public",) if allowed else (),
        forbidden_documents=() if allowed else ("staff",),
        allowed_fact_keys=("public.code",) if allowed else (),
        http_status=200,
        body="protected" if allowed else "neutral",
        persisted="available" if allowed else "neutral",
    )


def case_with(checks: tuple[SurfaceExpectation, ...]) -> Scenario:
    return Scenario(
        id="known",
        schema_version=1,
        class_name=ScenarioClass.ALLOW,
        principal_key="student",
        initial_grants=(),
        user_input="code?",
        actions=tuple(Ask(id=c.action_id, kind="ask", phase="prepare") for c in checks),
        expected=Expected(checks=checks),
    )


def arm_with(observations: tuple[Observation, ...]) -> ArmResult:
    return ArmResult(
        arm="guarded",
        config_hash="a" * 64,
        corpus_hash="b" * 64,
        renderer_identity="renderer",
        tokenizer_identity="tokenizer",
        model_identity="fake",
        observations=observations,
        documents=(("public", PUBLIC.document_id), ("staff", STAFF.document_id)),
    )


def release(action: str, *, available: bool = True) -> Observation:
    source = {
        "document_id": str(PUBLIC.document_id),
        "document_version_id": str(PUBLIC.document_version_id),
        "chunk_id": str(PUBLIC.chunk_id),
        "title": "",
        "url": "/api/source",
    }
    wire = dumps(
        {
            "thread_revision": 1,
            "replayed": False,
            "turn": {
                "id": str(UUID(int=8)),
                "request_id": str(UUID(int=9)),
                "user_input": "code?",
                "state": "available" if available else "neutral",
                "answer": "SYNTHETIC_PUBLIC_61" if available else None,
                "sources": [source] if available else [],
                "message": None if available else "Нет доступных источников для ответа.",
            },
        }
    )
    return Observation(
        action_id=action,
        surface="release",
        source_refs=(PUBLIC,) if available else (),
        forbidden_refs=(),
        provenance_valid=True,
        elapsed_ms=10,
        http_elapsed_ms=12,
        http_status=200,
        body="SYNTHETIC_PUBLIC_61" if available else "",
        persisted="available" if available else "neutral",
        cache_control="private, no-store",
        response_body=wire,
        titles=("",) if available else (),
    )


def test_false_denial_excludes_expected_no_context() -> None:
    case = case_with(tuple(expectation(f"ask{i}", allowed=i < 3) for i in range(5)))
    arm = arm_with(tuple(release(f"ask{i}", available=i < 2) for i in range(5)))
    result = metrics.summarize_arm(case, arm, {"public.code": "SYNTHETIC_PUBLIC_61"})
    rate = dict(result.rates)["false_denial_rate"]
    assert (rate.numerator, rate.denominator) == (1, 3)
    utility = dict(result.rates)["allowed_utility"]
    assert (utility.numerator, utility.denominator) == (2, 3)


@pytest.mark.parametrize("system_forbidden", [False, True])
def test_user_origin_marker_does_not_erase_structural_violation(*, system_forbidden: bool) -> None:
    check = expectation("ask").model_copy(
        update={
            "surface": "model_context",
            "http_status": None,
            "persisted": "unchanged",
            "forbidden_documents": ("staff",),
            "llm_calls": 1,
        }
    )
    refs = (PUBLIC, STAFF) if system_forbidden else (PUBLIC,)
    request = ModelRequestObservation(
        user_input="SYNTHETIC_STAFF_61",
        system_supplied_context="managed",
        source_refs=refs,
        forbidden_system_refs=(STAFF,) if system_forbidden else (),
        provenance_valid=True,
        user_origin_markers=frozenset({"SYNTHETIC_STAFF_61"}),
    )
    observed = replace(
        release("ask"),
        surface="model_context",
        source_refs=refs,
        request=request,
        body="SYNTHETIC_STAFF_61",
        persisted="unchanged",
    )
    result = metrics.summarize_arm(
        case_with((check,)), arm_with((observed,)), {"staff.code": "SYNTHETIC_STAFF_61"}
    )
    rates = dict(result.rates)
    assert rates["system_context_violation_rate"].numerator == int(system_forbidden)
    assert rates["model_output_marker_rate"].numerator == 0
    assert rates["user_origin_marker_output_rate"].numerator == 1


def test_missing_expected_surface_is_not_a_zero_leak() -> None:
    with pytest.raises(ValueError, match="surface"):
        _ = metrics.summarize_arm(case_with((expectation("ask"),)), arm_with(()), {})


def test_read_header_mismatch_fails_conformance() -> None:
    check = expectation("read", allowed=False).model_copy(
        update={
            "surface": "source_read",
            "authorization": "deny",
            "http_status": 404,
            "body": "redacted",
            "persisted": "unchanged",
        }
    )
    observed = replace(
        release("read", available=False),
        surface="source_read",
        http_status=404,
        persisted="unchanged",
        cache_control="public",
    )
    result = metrics.summarize_arm(case_with((check,)), arm_with((observed,)), {})
    assert dict(result.rates)["surface_conformance_rate"].numerator == 0


def test_document_list_uses_returned_ids_not_titles() -> None:
    check = expectation("read").model_copy(
        update={
            "surface": "document_list",
            "body": "list",
            "persisted": "unchanged",
            "forbidden_documents": ("staff",),
            "allowed_fact_keys": (),
        }
    )
    observed = replace(
        release("read", available=False),
        surface="document_list",
        persisted="unchanged",
        titles=("Public title",),
        response_body=dumps(
            {
                "items": [
                    {
                        "id": str(STAFF.document_id),
                        "title": "Public title",
                        "active_version_id": str(STAFF.document_version_id),
                    }
                ]
            }
        ),
    )
    rates = dict(metrics.summarize_arm(case_with((check,)), arm_with((observed,)), {}).rates)
    assert rates["document_list_violation_rate"].numerator == 1
    assert rates["surface_conformance_rate"].numerator == 0


def test_duplicate_release_evidence_is_rejected() -> None:
    with pytest.raises(ValueError, match="surface"):
        _ = metrics.summarize_arm(
            case_with((expectation("ask"),)),
            arm_with((release("ask"), release("ask"))),
            {"public.code": "SYNTHETIC_PUBLIC_61"},
        )
