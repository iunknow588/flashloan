from datetime import datetime, timezone

from core.orchestration_context import OrchestrationContext, OrchestrationStatus


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


def test_orchestration_context_handles_retry_and_success():
    context = OrchestrationContext("w1", deadline=NOW.replace(hour=23), max_retries=1)
    assert context.start(now=NOW) is OrchestrationStatus.RUNNING
    assert context.retry(now=NOW) is OrchestrationStatus.RETRYING
    assert context.retry(now=NOW) is OrchestrationStatus.FAILED

    context.status = OrchestrationStatus.RUNNING
    assert context.succeed() is OrchestrationStatus.SUCCEEDED


def test_orchestration_context_fails_closed_for_cancel_and_timeout():
    cancelled = OrchestrationContext("w2", deadline=NOW.replace(hour=23))
    assert cancelled.cancel() is OrchestrationStatus.CANCELLED
    assert cancelled.start(now=NOW) is OrchestrationStatus.CANCELLED

    timed_out = OrchestrationContext("w3", deadline=NOW)
    assert timed_out.start(now=NOW) is OrchestrationStatus.TIMED_OUT
    assert timed_out.retry(now=NOW) is OrchestrationStatus.TIMED_OUT
