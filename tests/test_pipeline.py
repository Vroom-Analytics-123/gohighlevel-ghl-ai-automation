"""End-to-end pipeline tests: webhook intake -> qualification -> CRM
write-back, including the CRM-down recovery path and the poison-payload path.
RED-first: these fail until ``ghl_automation.pipeline`` exists.
"""

import json
from datetime import date, timedelta

import pytest

from ghl_automation.crm_client import SimulatedCrm
from ghl_automation.pipeline import Pipeline
from ghl_automation.qualifier import Verdict


def hot_lead_payload():
    return {
        "type": "appointment_scheduled",
        "contact": {
            "id": "ct_123",
            "firstName": "Dana",
            "lastName": "Reyes",
            "email": "dana@example.com",
            "phone": "+14155550132",
            "tags": ["referral"],
        },
        "opportunity": {"id": "opp_9", "pipelineId": "pipe_1"},
        "customFields": {"budget": "5000"},
    }


def test_hot_lead_moves_stage_and_creates_follow_up_task():
    pipeline = Pipeline(crm=SimulatedCrm())

    report = pipeline.process(json.dumps(hot_lead_payload()))

    assert report["status"] == "processed"
    assert report["verdict"] == Verdict.HOT
    assert pipeline.crm.opportunities["opp_9"]["stage_id"] == "stage_qualified"
    tasks = pipeline.crm.tasks
    assert len(tasks) == 1
    assert "Dana Reyes" in tasks[0]["title"]


def test_warm_lead_creates_a_task_but_does_not_move_stage():
    pipeline = Pipeline(crm=SimulatedCrm())
    payload = hot_lead_payload()
    payload["type"] = "form_submitted"
    del payload["customFields"]

    report = pipeline.process(payload)

    assert report["verdict"] == Verdict.WARM
    assert pipeline.crm.tasks  # follow-up task created
    assert "opp_9" not in pipeline.crm.opportunities  # stage untouched


def test_cold_lead_moves_to_nurture_stage_without_task_noise():
    pipeline = Pipeline(crm=SimulatedCrm())
    payload = {
        "type": "contact_created",
        "contact": {"id": "ct_456", "email": "b@c.com", "phone": "+14155550133"},
        "opportunity": {"id": "opp_10"},
    }

    report = pipeline.process(payload)

    assert report["verdict"] == Verdict.COLD
    assert pipeline.crm.opportunities["opp_10"]["stage_id"] == "stage_nurture"
    assert pipeline.crm.tasks == []


def test_insufficient_data_creates_a_manual_review_task():
    pipeline = Pipeline(crm=SimulatedCrm())
    payload = {
        "type": "form_submitted",
        "contact": {"id": "ct_789", "firstName": "No", "lastName": "Contact"},
    }

    report = pipeline.process(payload)

    assert report["verdict"] == Verdict.INSUFFICIENT_DATA
    assert len(pipeline.crm.tasks) == 1
    assert "manual review" in pipeline.crm.tasks[0]["title"].lower()


def test_crm_down_queues_writes_instead_of_dropping_them():
    pipeline = Pipeline(crm=SimulatedCrm(fail_mode="always"))

    report = pipeline.process(hot_lead_payload())

    assert report["status"] == "processed"
    queued = [w for w in report["writes"] if w["status"] == "queued"]
    assert len(queued) == 2  # stage update + follow-up task
    assert pipeline.queue.pending_count == 2


def test_queued_writes_complete_once_the_crm_recovers():
    pipeline = Pipeline(crm=SimulatedCrm(fail_mode="always"))
    pipeline.process(hot_lead_payload())

    pipeline.crm.fail_mode = None  # backend recovers
    results = pipeline.drain_queue()

    assert results["succeeded"] == 2
    assert pipeline.crm.opportunities["opp_9"]["stage_id"] == "stage_qualified"
    assert len(pipeline.crm.tasks) == 1


def test_malformed_payload_is_rejected_and_never_queued():
    pipeline = Pipeline(crm=SimulatedCrm())

    report = pipeline.process("{not valid json")

    assert report["status"] == "rejected"
    assert report["verdict"] is None
    assert pipeline.queue.pending_count == 0
    assert pipeline.crm.tasks == []


def test_unknown_event_type_is_rejected_and_never_queued():
    pipeline = Pipeline(crm=SimulatedCrm())

    report = pipeline.process(json.dumps({"type": "mystery_event"}))

    assert report["status"] == "rejected"
    assert pipeline.queue.pending_count == 0


def test_report_carries_reasons_for_transparency():
    pipeline = Pipeline(crm=SimulatedCrm())

    report = pipeline.process(hot_lead_payload())

    assert report["score"] is not None
    assert report["reasons"]
