"""Tests for the retry queue: transient failures are retried with backoff,
persistent outages end in the dead-letter list, and poison payloads are
dead-lettered immediately -- never retried forever.
RED-first: these fail until ``ghl_automation.retry_queue`` exists.
"""

import pytest

from ghl_automation.crm_client import SimulatedCrm
from ghl_automation.retry_queue import RetryQueue


def stage_update_op(**kwargs):
    op = {
        "kind": "stage_update",
        "opportunity_id": "opp_9",
        "stage_id": "stage_qualified",
        "request": {
            "method": "PUT",
            "path": "/opportunities/opp_9",
            "body": {"pipelineStageId": "stage_qualified", "status": "open"},
        },
    }
    op.update(kwargs)
    return op


def test_enqueue_and_successful_drain():
    queue = RetryQueue()
    crm = SimulatedCrm()

    queue.enqueue(stage_update_op())
    results = queue.drain(crm)

    assert results["succeeded"] == 1
    assert queue.pending_count == 0
    assert crm.opportunities["opp_9"]["stage_id"] == "stage_qualified"


def test_transient_failure_stays_queued_then_recovers():
    queue = RetryQueue()
    crm = SimulatedCrm(fail_mode="flaky_once")

    queue.enqueue(stage_update_op())
    first = queue.drain(crm)
    assert first["succeeded"] == 0
    assert queue.pending_count == 1

    second = queue.drain(crm)
    assert second["succeeded"] == 1
    assert queue.pending_count == 0


def test_backoff_doubles_with_each_attempt():
    queue = RetryQueue(base_delay_s=60)

    assert queue.delay_for_attempts(1) == 60
    assert queue.delay_for_attempts(2) == 120
    assert queue.delay_for_attempts(3) == 240
    assert queue.delay_for_attempts(4) == 480


def test_persistent_outage_dead_letters_after_max_attempts():
    queue = RetryQueue(max_attempts=3)
    crm = SimulatedCrm(fail_mode="always")

    queue.enqueue(stage_update_op())

    for _ in range(3):
        queue.drain(crm)

    assert queue.pending_count == 0
    assert len(queue.dead_letters) == 1
    assert queue.dead_letters[0]["attempts"] == 3


def test_permanent_error_goes_straight_to_dead_letter():
    queue = RetryQueue(max_attempts=5)
    crm = SimulatedCrm(fail_mode="permanent")

    queue.enqueue(stage_update_op())
    results = queue.drain(crm)

    assert results["dead_lettered"] == 1
    assert queue.dead_letters[0]["attempts"] == 1  # never retried
    assert queue.pending_count == 0


def test_fifo_order_is_preserved_on_drain():
    queue = RetryQueue()
    crm = SimulatedCrm()

    queue.enqueue(stage_update_op(opportunity_id="opp_1"))
    queue.enqueue(stage_update_op(opportunity_id="opp_2"))

    queue.drain(crm)

    assert [call["opportunity_id"] for call in crm.calls] == ["opp_1", "opp_2"]


def test_nothing_is_silently_dropped():
    queue = RetryQueue(max_attempts=2)
    crm = SimulatedCrm(fail_mode="always")

    queue.enqueue(stage_update_op())
    queue.drain(crm)
    queue.drain(crm)

    total = queue.pending_count + len(queue.dead_letters)
    assert total == 1  # every op is either pending or dead-lettered
