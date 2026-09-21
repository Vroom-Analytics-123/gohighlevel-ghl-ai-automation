"""GoHighLevel AI automation: webhook intake -> lead qualification -> CRM write-back."""

from ghl_automation.crm_client import (
    CrmAdapter,
    PermanentError,
    SimulatedCrm,
    TransientError,
    build_stage_update_payload,
    build_task_payload,
)
from ghl_automation.pipeline import Pipeline
from ghl_automation.qualifier import (
    QualificationResult,
    ScoringConfig,
    Verdict,
    qualify,
)
from ghl_automation.retry_queue import RetryQueue
from ghl_automation.webhook import (
    MalformedPayloadError,
    NormalizedContact,
    UnknownEventError,
    ValidationError,
    WebhookEvent,
    parse_webhook,
)

__all__ = [
    "CrmAdapter",
    "MalformedPayloadError",
    "NormalizedContact",
    "PermanentError",
    "Pipeline",
    "QualificationResult",
    "RetryQueue",
    "ScoringConfig",
    "SimulatedCrm",
    "TransientError",
    "UnknownEventError",
    "ValidationError",
    "Verdict",
    "WebhookEvent",
    "build_stage_update_payload",
    "build_task_payload",
    "parse_webhook",
    "qualify",
]
