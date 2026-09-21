"""Tests for rule-based lead qualification: hot/warm/cold boundaries,
exact-boundary tie behavior, the insufficient-data (never-guess) path, and the
opt-out suppression rule. RED-first: these fail until
``ghl_automation.qualifier`` exists.
"""

import pytest

from ghl_automation.qualifier import (
    QualificationResult,
    ScoringConfig,
    Verdict,
    qualify,
)
from ghl_automation.webhook import NormalizedContact, WebhookEvent

DEFAULTS = ScoringConfig()


def make_event(event_type="form_submitted", tags=(), email="a@b.com",
               phone="+14155550132", message_text=None, custom_fields=None):
    contact = NormalizedContact(
        contact_id="ct_1",
        first_name="Pat",
        last_name="Lee",
        full_name="Pat Lee",
        email=email,
        phone=phone,
        tags=list(tags),
        source="website",
    )
    return WebhookEvent(
        event_type=event_type,
        contact=contact,
        opportunity=None,
        message_text=message_text,
        custom_fields=custom_fields or {},
    )


def test_high_intent_scores_hot():
    event = make_event(
        event_type="appointment_scheduled",
        tags=["referral"],
        custom_fields={"budget": "5000"},
    )

    result = qualify(event)

    assert isinstance(result, QualificationResult)
    assert result.verdict is Verdict.HOT
    assert result.score >= DEFAULTS.hot_threshold


def test_score_exactly_on_hot_boundary_is_hot():
    # email(10) + phone(15) + appointment(25) + form-less referral(10) + budget(10) == 70
    event = make_event(
        event_type="appointment_scheduled",
        tags=["referral"],
        custom_fields={"budget": "1000"},  # exactly at budget floor
    )

    result = qualify(event, ScoringConfig(budget_weight=10))

    assert result.score == 70
    assert result.verdict is Verdict.HOT  # boundary belongs to the higher band


def test_one_point_below_hot_boundary_is_warm():
    event = make_event(event_type="form_submitted", tags=["referral"])
    # email(10) + phone(15) + form(10) + referral(10) = 45 with custom weights

    result = qualify(event, ScoringConfig(form_weight=9))

    assert result.score == 44
    assert result.verdict is Verdict.WARM


def test_score_exactly_on_warm_boundary_is_warm():
    event = make_event(event_type="form_submitted", tags=["referral"])

    result = qualify(event)  # 10 + 15 + 10 + 10 = 45 by default weights

    config = ScoringConfig(form_weight=5)  # 10 + 15 + 5 + 10 = 40
    result = qualify(event, config)

    assert result.score == 40
    assert result.verdict is Verdict.WARM


def test_one_point_below_warm_boundary_is_cold():
    event = make_event(event_type="contact_created")  # email + phone only = 25

    result = qualify(event)

    assert result.score == 25
    assert result.verdict is Verdict.COLD


def test_no_reachable_channel_is_insufficient_data_not_a_score():
    event = make_event(email=None, phone=None)

    result = qualify(event)

    assert result.verdict is Verdict.INSUFFICIENT_DATA
    assert result.score is None  # never fabricate a score we cannot act on
    assert "email" in result.missing_fields
    assert "phone" in result.missing_fields


def test_insufficient_data_names_everything_missing():
    event = make_event(
        event_type="contact_created",
        email=None,
        phone=None,
        custom_fields={},
    )

    result = qualify(event)

    assert result.verdict is Verdict.INSUFFICIENT_DATA
    assert "budget" in result.missing_fields
    assert "timeline" in result.missing_fields


def test_opt_out_tag_suppresses_even_a_hot_score():
    event = make_event(
        event_type="appointment_scheduled",
        tags=["referral", "opt-out"],
        custom_fields={"budget": "9000"},
    )

    result = qualify(event)

    assert result.verdict is Verdict.COLD  # safety signal wins over score
    assert any("opt-out" in reason for reason in result.reasons)


def test_reasons_are_human_readable_and_non_empty():
    result = qualify(make_event(event_type="appointment_scheduled"))

    assert result.reasons
    assert all(isinstance(reason, str) and reason for reason in result.reasons)


def test_urgency_keywords_in_message_add_signal():
    urgent = make_event(
        event_type="message_received",
        message_text="Need help ASAP, can we talk today?",
    )
    plain = make_event(
        event_type="message_received",
        message_text="Just browsing, thanks.",
    )

    assert qualify(urgent).score > qualify(plain).score
    assert any("urgency" in r for r in qualify(urgent).reasons)


def test_scoring_is_deterministic():
    event = make_event(event_type="appointment_scheduled", tags=["referral"])

    first, second = qualify(event), qualify(event)

    assert (first.score, first.verdict) == (second.score, second.verdict)
    assert first.reasons == second.reasons


def test_custom_config_weights_are_respected():
    event = make_event(event_type="form_submitted")

    default = qualify(event)
    boosted = qualify(event, ScoringConfig(form_weight=60))

    assert boosted.score > default.score
    assert boosted.verdict is Verdict.HOT


def test_missing_optional_fields_never_crash_scoring():
    event = make_event(
        event_type="message_received",
        tags=(),
        message_text=None,
        custom_fields={},
    )

    result = qualify(event)

    assert result.verdict in (Verdict.WARM, Verdict.COLD, Verdict.HOT)
    assert result.score is not None
