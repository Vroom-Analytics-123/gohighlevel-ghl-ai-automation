"""Rule-based lead scoring: transparent, explainable, never guesses.

Every point has a human-readable reason, so a business owner can read the
score and understand exactly why a lead landed hot, warm, or cold.

The never-guess rule: a lead with no reachable channel (no email AND no phone)
gets :data:`Verdict.INSUFFICIENT_DATA` with ``score=None`` -- we cannot act on
a lead we cannot reach, so we do not fabricate a score for one.

Tie-breaking is explicit, not accidental:
* A score exactly on a band boundary belongs to the HIGHER band
  (70 is HOT, 40 is WARM).
* Safety signals outrank scores: an ``opt-out``/``do-not-contact`` tag forces
  COLD no matter the score.
"""

import re
from dataclasses import dataclass, field
from enum import Enum

from ghl_automation.webhook import NormalizedContact, WebhookEvent

URGENCY_KEYWORDS = frozenset(
    {"urgent", "asap", "today", "this week", "immediately", "right away"}
)
OPT_OUT_TAGS = frozenset({"opt-out", "do-not-contact", "dnc", "unsubscribed"})
TIMELINE_SIGNALS = frozenset({"appointment_scheduled", "message_received"})


class Verdict(Enum):
    """Lead-qualification verdicts."""

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class ScoringConfig:
    """Tunable weights and band thresholds. All points are additive."""

    hot_threshold: int = 70
    warm_threshold: int = 40
    email_weight: int = 10
    phone_weight: int = 15
    appointment_weight: int = 25
    form_weight: int = 10
    message_weight: int = 10
    urgency_weight: int = 10
    referral_weight: int = 10
    budget_weight: int = 15
    budget_floor: int = 1000


@dataclass
class QualificationResult:
    """The score, the verdict, and why -- in plain language."""

    score: int | None
    verdict: Verdict
    reasons: list = field(default_factory=list)
    missing_fields: list = field(default_factory=list)


def _parse_budget(value: object) -> int | None:
    """Pull a number out of a free-text budget field ("$2,500" -> 2500)."""
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def _missing_fields(contact: NormalizedContact, event: WebhookEvent) -> list:
    """Name every input the scorer would have liked but did not get."""
    missing = []
    if not contact.email:
        missing.append("email")
    if not contact.phone:
        missing.append("phone")
    if (
        event.event_type not in TIMELINE_SIGNALS
        and not event.custom_fields.get("timeline")
    ):
        missing.append("timeline")
    if _parse_budget(event.custom_fields.get("budget")) is None:
        missing.append("budget")
    if not contact.source and not contact.tags:
        missing.append("source")
    return missing


def qualify(
    event: WebhookEvent, config: ScoringConfig | None = None
) -> QualificationResult:
    """Score one normalized webhook event and return the verdict.

    Deterministic: the same event always produces the same result.
    """
    config = config or ScoringConfig()
    contact = event.contact
    score = 0
    reasons: list[str] = []

    def add(weight: int, reason: str) -> None:
        nonlocal score
        score += weight
        reasons.append(f"+{weight} — {reason}")

    # --- the never-guess rule: no channel, no score ---
    if not contact.email and not contact.phone:
        return QualificationResult(
            score=None,
            verdict=Verdict.INSUFFICIENT_DATA,
            reasons=["no email or phone on file — cannot reach, so no score given"],
            missing_fields=_missing_fields(contact, event),
        )

    if contact.email:
        add(config.email_weight, "reachable by email")
    if contact.phone:
        add(config.phone_weight, "reachable by phone")

    if event.event_type == "appointment_scheduled":
        add(config.appointment_weight, "booked an appointment")
    elif event.event_type == "form_submitted":
        add(config.form_weight, "submitted a form")
    elif event.event_type == "message_received":
        add(config.message_weight, "replied to a message")
        text = (event.message_text or "").lower()
        if any(keyword in text for keyword in URGENCY_KEYWORDS):
            add(config.urgency_weight, "urgency signal in message")

    if "referral" in {tag.lower() for tag in contact.tags}:
        add(config.referral_weight, "referred")

    budget = _parse_budget(event.custom_fields.get("budget"))
    if budget is not None and budget >= config.budget_floor:
        add(config.budget_weight, f"budget stated ({budget})")

    # --- tie-breaking: boundaries belong to the higher band ---
    if score >= config.hot_threshold:
        verdict = Verdict.HOT
    elif score >= config.warm_threshold:
        verdict = Verdict.WARM
    else:
        verdict = Verdict.COLD

    # --- safety beats score: opt-out always wins ---
    if {tag.lower() for tag in contact.tags} & OPT_OUT_TAGS:
        verdict = Verdict.COLD
        reasons.append("suppressed: opt-out tag — never contact")

    return QualificationResult(
        score=score,
        verdict=verdict,
        reasons=reasons,
        missing_fields=_missing_fields(contact, event),
    )
