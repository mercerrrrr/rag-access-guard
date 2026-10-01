from pathlib import Path

from experiments.security_scenarios import load_scenarios


def test_suite_contains_distinct_channel_and_second_hop_controls() -> None:
    cases = {case.id: case for case in load_scenarios(Path("experiments/scenarios.json"))}
    required = {"paste-user-only", "paste-system-only", "paste-both-channels", "paste-second-hop"}
    assert required <= cases.keys()
    for case_id in required:
        case = cases[case_id]
        assert any(
            check.surface == "model_context" and check.llm_calls == 1
            for check in case.expected.checks
        )
    second = cases["paste-second-hop"]
    assert sum(action.kind == "ask" for action in second.actions) == 2
    assert not any(action.kind.startswith("revoke") for action in second.actions)
