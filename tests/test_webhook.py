"""Tests for webhook intake: valid payloads, missing fields, malformed JSON,
unknown event types, and normalization. RED-first: these fail until
``ghl_automation.webhook`` exists.
"""

import json

import pytest

from ghl_automation.webhook import (
    MalformedPayloadError,
    NormalizedContact,
    UnknownEventError,
    ValidationError,
    WebhookEvent,
    parse_webhook,
)


def contact_created_payload(**overrides):
    payload = {
        "type": "contact_created",
        "contact": {
            "id": "ct_123",
            "firstName": "  Dana  ",
            "lastName": "Reyes",
            "email": "  DANA@Example.COM ",
            "phone": "+1 415-555-0132",
            "tags": ["website", "Website", "referral"],
            "source": "website form",
        },
    }
    payload.update(overrides)
    return payload


def test_valid_contact_created_parses():
    event = parse_webhook(contact_created_payload())

    assert isinstance(event, WebhookEvent)
    assert event.event_type == "contact_created"
    assert isinstance(event.contact, NormalizedContact)
    assert event.contact.contact_id == "ct_123"


def test_json_string_input_is_accepted():
    event = parse_webhook(json.dumps(contact_created_payload()))
    assert event.event_type == "contact_created"


def test_bytes_input_is_accepted():
    event = parse_webhook(json.dumps(contact_created_payload()).encode("utf-8"))
    assert event.contact.email == "dana@example.com"


def test_name_and_email_are_normalized():
    event = parse_webhook(contact_created_payload())

    assert event.contact.first_name == "Dana"
    assert event.contact.last_name == "Reyes"
    assert event.contact.full_name == "Dana Reyes"
    assert event.contact.email == "dana@example.com"  # trimmed + lowercased


def test_tags_are_deduplicated_case_insensitively():
    event = parse_webhook(contact_created_payload())

    assert sorted(event.contact.tags) == ["referral", "website"]


def test_malformed_json_raises_malformed_payload_error():
    with pytest.raises(MalformedPayloadError):
        parse_webhook("{this is not json")


def test_non_object_json_raises_malformed_payload_error():
    with pytest.raises(MalformedPayloadError):
        parse_webhook(json.dumps([1, 2, 3]))


def test_missing_type_raises_validation_error():
    payload = contact_created_payload()
    del payload["type"]

    with pytest.raises(ValidationError):
        parse_webhook(payload)


def test_empty_type_raises_validation_error():
    payload = contact_created_payload(type="   ")

    with pytest.raises(ValidationError):
        parse_webhook(payload)


def test_unknown_event_type_raises_unknown_event_error():
    payload = contact_created_payload(type="lead_sacrificed_to_gods")

    with pytest.raises(UnknownEventError):
        parse_webhook(payload)


def test_unknown_event_error_is_a_validation_error():
    assert issubclass(UnknownEventError, ValidationError)


def test_missing_contact_raises_validation_error():
    payload = contact_created_payload()
    del payload["contact"]

    with pytest.raises(ValidationError):
        parse_webhook(payload)


def test_contact_without_id_raises_validation_error():
    payload = contact_created_payload()
    del payload["contact"]["id"]

    with pytest.raises(ValidationError):
        parse_webhook(payload)


def test_all_supported_event_types_parse():
    for event_type in [
        "contact_created",
        "contact_updated",
        "opportunity_created",
        "opportunity_stage_updated",
        "appointment_scheduled",
        "form_submitted",
        "message_received",
        "survey_submitted",
    ]:
        event = parse_webhook(contact_created_payload(type=event_type))
        assert event.event_type == event_type


def test_opportunity_details_are_captured():
    payload = contact_created_payload(type="opportunity_created")
    payload["opportunity"] = {
        "id": "opp_9",
        "pipelineId": "pipe_1",
        "pipelineStageId": "stage_new",
    }

    event = parse_webhook(payload)

    assert event.opportunity is not None
    assert event.opportunity["id"] == "opp_9"


def test_message_text_is_captured_for_scoring():
    payload = contact_created_payload(type="message_received")
    payload["message"] = {"text": "Need help ASAP, can we talk today?"}

    event = parse_webhook(payload)

    assert event.message_text == "Need help ASAP, can we talk today?"


def test_form_payload_with_custom_fields_is_captured():
    payload = contact_created_payload(type="form_submitted")
    payload["customFields"] = {"budget": "2500", "timeline": "this month"}

    event = parse_webhook(payload)

    assert event.custom_fields == {"budget": "2500", "timeline": "this month"}
