"""Parse, validate, and normalize inbound GoHighLevel webhook payloads.

Every public entry point raises a specific, catchable error -- the pipeline
never guesses at what an ambiguous payload meant:

* :class:`MalformedPayloadError` -- body is not valid JSON, or not a JSON object.
* :class:`ValidationError` -- JSON is fine but required fields are missing/empty.
* :class:`UnknownEventError` -- a well-formed event type we do not handle
  (a subclass of :class:`ValidationError`, so one ``except`` catches both).
"""

import json
from dataclasses import dataclass, field

SUPPORTED_EVENTS = frozenset(
    {
        "contact_created",
        "contact_updated",
        "opportunity_created",
        "opportunity_stage_updated",
        "appointment_scheduled",
        "form_submitted",
        "message_received",
        "survey_submitted",
    }
)


class MalformedPayloadError(Exception):
    """The webhook body could not be decoded as a JSON object."""


class ValidationError(Exception):
    """The JSON is valid but a required field is missing, empty, or wrong-typed."""


class UnknownEventError(ValidationError):
    """The event ``type`` is not one we know how to handle.

    Unknown events are rejected, never silently processed as something else.
    """


@dataclass
class NormalizedContact:
    """A contact with trimmed, lowercased, deduplicated fields.

    ``email``/``phone`` are ``None`` when absent -- the qualifier treats that
    as "insufficient data", never as an excuse to invent a score.
    """

    contact_id: str
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    email: str | None = None
    phone: str | None = None
    tags: list = field(default_factory=list)
    source: str | None = None


@dataclass
class WebhookEvent:
    """A validated, normalized webhook event ready for qualification."""

    event_type: str
    contact: NormalizedContact
    opportunity: dict | None = None
    message_text: str | None = None
    custom_fields: dict = field(default_factory=dict)


def _clean(value: object) -> str | None:
    """Strip whitespace; return ``None`` for missing/empty values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_tags(raw: object) -> list:
    """Deduplicate tags case-insensitively, keeping first-seen order."""
    seen: set[str] = set()
    tags: list[str] = []
    for tag in raw if isinstance(raw, list) else []:
        cleaned = _clean(tag)
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            tags.append(cleaned)
    return tags


def parse_webhook(raw: str | bytes | dict) -> WebhookEvent:
    """Parse and validate one inbound GHL webhook payload.

    Accepts a raw JSON body (``str``/``bytes``) or an already-decoded ``dict``
    (handy for frameworks that pre-parse the body, and for tests).

    Returns a :class:`WebhookEvent`; raises :class:`MalformedPayloadError`,
    :class:`ValidationError`, or :class:`UnknownEventError`.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MalformedPayloadError(f"body is not valid UTF-8: {exc}") from exc
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MalformedPayloadError(f"body is not valid JSON: {exc}") from exc
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise MalformedPayloadError(
            f"expected a JSON object, got {type(raw).__name__}"
        )
    if not isinstance(payload, dict):
        raise MalformedPayloadError("webhook body must be a JSON object")

    event_type = _clean(payload.get("type"))
    if not event_type:
        raise ValidationError("missing required field: 'type'")
    if event_type not in SUPPORTED_EVENTS:
        raise UnknownEventError(f"unsupported event type: {event_type!r}")

    contact_raw = payload.get("contact")
    if not isinstance(contact_raw, dict) or not _clean(contact_raw.get("id")):
        raise ValidationError("missing required field: 'contact.id'")

    first_name = _clean(contact_raw.get("firstName")) or ""
    last_name = _clean(contact_raw.get("lastName")) or ""
    email = _clean(contact_raw.get("email"))
    if email:
        email = email.lower()

    contact = NormalizedContact(
        contact_id=_clean(contact_raw.get("id")),
        first_name=first_name,
        last_name=last_name,
        full_name=" ".join(part for part in (first_name, last_name) if part),
        email=email,
        phone=_clean(contact_raw.get("phone")),
        tags=_normalize_tags(contact_raw.get("tags")),
        source=_clean(contact_raw.get("source")),
    )

    opportunity = payload.get("opportunity")
    if not isinstance(opportunity, dict):
        opportunity = None

    message = payload.get("message")
    message_text = _clean(message.get("text")) if isinstance(message, dict) else None

    custom_fields = payload.get("customFields")
    if not isinstance(custom_fields, dict):
        custom_fields = {}

    return WebhookEvent(
        event_type=event_type,
        contact=contact,
        opportunity=opportunity,
        message_text=message_text,
        custom_fields=custom_fields,
    )
