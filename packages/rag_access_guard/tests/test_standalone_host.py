import asyncio

from scripts.demo_guard_host import DEMO_MARKER, DEMO_QUESTION, run_demo_cycle


def test_cycle_hides_saved_answer_after_revoke_and_blocks_pending_release() -> None:
    report = asyncio.run(run_demo_cycle())
    assert report.initial_read.state == "available"
    assert report.initial_read.answer is not None
    assert DEMO_MARKER in report.initial_read.answer
    assert report.initial_read.sources
    assert report.final_read.state == "unavailable"
    assert report.final_read.user_input == DEMO_QUESTION
    assert report.final_read.answer is None
    assert report.final_read.sources == ()
    assert report.final_read.message == "Ответ недоступен: права на один из источников изменились."
    assert report.late_release.allowed is False
    assert report.late_release.reason == "stale_revision"
    assert report.saved_count == 1
    assert report.model_calls == 2
    assert report.retry_count == 1
