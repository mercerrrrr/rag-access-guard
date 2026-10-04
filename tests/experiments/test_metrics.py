import pytest

from experiments import metrics


def test_zero_denominator_is_not_zero_rate() -> None:
    rate = metrics.make_rate(0, 0)
    assert rate.numerator == 0
    assert rate.denominator == 0
    assert rate.value is None
    assert rate.status == "not_applicable"


def test_known_answer_rate_preserves_denominator() -> None:
    rate = metrics.make_rate(1, 3)
    assert rate.value == 1 / 3
    assert rate.status == "measured"


@pytest.mark.parametrize(("numerator", "denominator"), [(-1, 1), (0, -1), (2, 1)])
def test_rate_rejects_impossible_counts(numerator: int, denominator: int) -> None:
    with pytest.raises(ValueError, match="Rate counts"):
        _ = metrics.make_rate(numerator, denominator)
