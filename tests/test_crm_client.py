"""Tests for the CRM client: exact API payload shapes for the pipeline-stage
update and task creation, plus the simulated backend's failure modes.
RED-first: these fail until ``ghl_automation.crm_client`` exists.
"""

import pytest

from ghl_automation.crm_client import (
    CrmAdapter,
    PermanentError,
    SimulatedCrm,
    TransientError,
    build_stage_update_payload,
    build_task_payload,
)


def test_stage_update_payload_has_exact_shape():
    request = build_stage_update_payload(
        opportunity_id="opp_9", stage_id="stage_qualified"
    )

    assert request["method"] == "PUT"
    assert request["path"] == "/opportunities/opp_9"
    assert request["body"] == {
        "pipelineStageId": "stage_qualified",
        "status": "open",
    }


def test_stage_update_payload_includes_pipeline_id_when_given():
    request = build_stage_update_payload(
        opportunity_id="opp_9",
        stage_id="stage_qualified",
        pipeline_id="pipe_1",
    )

    assert request["body"]["pipelineId"] == "pipe_1"


def test_task_payload_has_exact_shape():
    request = build_task_payload(
        contact_id="ct_123",
        title="Follow up: Dana Reyes (HOT lead, score 82)",
        body="Called from website form. Reasons: booked an appointment; reachable by phone.",
        due_date="2026-09-21",
        assigned_to="user_7",
    )

    assert request["method"] == "POST"
    assert request["path"] == "/contacts/ct_123/tasks"
    assert request["body"] == {
        "title": "Follow up: Dana Reyes (HOT lead, score 82)",
        "body": "Called from website form. Reasons: booked an appointment; reachable by phone.",
        "dueDate": "2026-09-21",
        "assignedTo": "user_7",
    }


def test_task_payload_omits_assigned_to_when_none():
    request = build_task_payload(
        contact_id="ct_123",
        title="Manual review",
        body="Not enough data to score.",
        due_date="2026-09-20",
    )

    assert "assignedTo" not in request["body"]


def test_simulated_crm_updates_stage_in_memory():
    crm = SimulatedCrm(opportunities={"opp_9": {"stage_id": "stage_new"}})

    crm.update_opportunity_stage("opp_9", "stage_qualified")

    assert crm.opportunities["opp_9"]["stage_id"] == "stage_qualified"


def test_simulated_crm_records_created_tasks():
    crm = SimulatedCrm()

    crm.create_task("ct_123", "Follow up", "Hot lead.", "2026-09-21")

    assert len(crm.tasks) == 1
    assert crm.tasks[0]["contact_id"] == "ct_123"
    assert crm.tasks[0]["title"] == "Follow up"


def test_flaky_once_recovers_on_second_call():
    crm = SimulatedCrm(fail_mode="flaky_once")

    with pytest.raises(TransientError):
        crm.update_opportunity_stage("opp_9", "stage_qualified")

    crm.update_opportunity_stage("opp_9", "stage_qualified")  # no raise now
    assert crm.opportunities["opp_9"]["stage_id"] == "stage_qualified"


def test_always_down_raises_transient_error():
    crm = SimulatedCrm(fail_mode="always")

    with pytest.raises(TransientError):
        crm.create_task("ct_123", "Follow up", "Hot lead.", "2026-09-21")


def test_permanent_failure_raises_permanent_error():
    crm = SimulatedCrm(fail_mode="permanent")

    with pytest.raises(PermanentError):
        crm.create_task("ct_123", "Follow up", "Hot lead.", "2026-09-21")


def test_simulated_crm_implements_the_adapter_interface():
    assert isinstance(SimulatedCrm(), CrmAdapter)


def test_crm_calls_are_recorded_for_inspection():
    crm = SimulatedCrm()

    crm.update_opportunity_stage("opp_9", "stage_qualified")
    crm.create_task("ct_123", "Follow up", "Hot lead.", "2026-09-21")

    kinds = [call["kind"] for call in crm.calls]
    assert kinds == ["stage_update", "task"]
