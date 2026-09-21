"""The pipeline: webhook intake -> qualification -> CRM write-back.

Routing rules (configured at construction, sensible defaults):

* HOT lead    -> move the opportunity to the qualified stage (when the event
                 carries one) AND create an urgent follow-up task (due tomorrow).
* WARM lead   -> create a follow-up task (due in 3 days). Stage untouched.
* COLD lead   -> move the opportunity to the nurture stage. No task noise.
* INSUFFICIENT_DATA -> create a "manual review" task so a human sees it.
  Never guessed, never dropped.

When the CRM is down, writes go to the retry queue instead of being lost:
:meth:`Pipeline.drain_queue` replays them once the backend recovers.

Poison payloads (malformed JSON, missing fields, unknown event types) are
REJECTED outright -- they never enter the retry queue, because retrying a
payload that can never parse is how queues fill up with garbage.
"""

from datetime import date, timedelta

from ghl_automation.crm_client import (
    PermanentError,
    TransientError,
    build_stage_update_payload,
    build_task_payload,
)
from ghl_automation.qualifier import ScoringConfig, Verdict, qualify
from ghl_automation.retry_queue import RetryQueue
from ghl_automation.webhook import (
    MalformedPayloadError,
    ValidationError,
    WebhookEvent,
    parse_webhook,
)


def _due_in(days: int) -> str:
    """ISO date ``days`` from today, for task due dates."""
    return (date.today() + timedelta(days=days)).isoformat()


class Pipeline:
    """Orchestrates intake -> qualify -> write-back with honest failure paths."""

    def __init__(
        self,
        crm,
        queue: RetryQueue | None = None,
        config: ScoringConfig | None = None,
        hot_stage_id: str = "stage_qualified",
        nurture_stage_id: str = "stage_nurture",
        assigned_to: str | None = None,
    ):
        self.crm = crm
        self.queue = queue or RetryQueue()
        self.config = config or ScoringConfig()
        self.hot_stage_id = hot_stage_id
        self.nurture_stage_id = nurture_stage_id
        self.assigned_to = assigned_to

    def process(self, raw: str | bytes | dict) -> dict:
        """Process one inbound webhook payload and return a report dict.

        The report always has ``status`` ("processed" or "rejected"),
        ``verdict``, ``score``, ``reasons``, and ``writes`` (each either
        "done" or "queued").
        """
        try:
            event = parse_webhook(raw)
        except (MalformedPayloadError, ValidationError) as exc:
            # Poison payload: reject. Never queued, never retried.
            return {
                "status": "rejected",
                "event_type": None,
                "verdict": None,
                "score": None,
                "reasons": [f"rejected: {exc}"],
                "writes": [],
            }

        result = qualify(event, self.config)
        writes = [self._write(op) for op in self._plan_writes(event, result)]

        return {
            "status": "processed",
            "event_type": event.event_type,
            "verdict": result.verdict,
            "score": result.score,
            "reasons": result.reasons,
            "writes": writes,
        }

    def drain_queue(self) -> dict:
        """Replay queued writes against the CRM. See :meth:`RetryQueue.drain`."""
        return self.queue.drain(self.crm)

    def _plan_writes(self, event: WebhookEvent, result) -> list:
        """Decide the CRM writes for a qualification result."""
        contact = event.contact
        name = contact.full_name or contact.contact_id
        ops: list = []

        if result.verdict is Verdict.HOT:
            if event.opportunity:
                ops.append(
                    {
                        "kind": "stage_update",
                        "opportunity_id": event.opportunity["id"],
                        "stage_id": self.hot_stage_id,
                        "pipeline_id": event.opportunity.get("pipelineId"),
                    }
                )
            ops.append(
                {
                    "kind": "task",
                    "contact_id": contact.contact_id,
                    "title": f"🔥 Follow up: {name} (HOT lead, score {result.score})",
                    "body": "Score reasons: " + "; ".join(result.reasons),
                    "due_date": _due_in(1),
                    "assigned_to": self.assigned_to,
                }
            )
        elif result.verdict is Verdict.WARM:
            ops.append(
                {
                    "kind": "task",
                    "contact_id": contact.contact_id,
                    "title": f"Follow up: {name} (warm lead, score {result.score})",
                    "body": "Score reasons: " + "; ".join(result.reasons),
                    "due_date": _due_in(3),
                    "assigned_to": self.assigned_to,
                }
            )
        elif result.verdict is Verdict.COLD:
            if event.opportunity:
                ops.append(
                    {
                        "kind": "stage_update",
                        "opportunity_id": event.opportunity["id"],
                        "stage_id": self.nurture_stage_id,
                        "pipeline_id": event.opportunity.get("pipelineId"),
                    }
                )
        else:  # INSUFFICIENT_DATA -- a human must look at this
            ops.append(
                {
                    "kind": "task",
                    "contact_id": contact.contact_id,
                    "title": f"Manual review needed: {name}",
                    "body": (
                        "Not enough data to score this lead. Missing: "
                        + ", ".join(result.missing_fields)
                    ),
                    "due_date": _due_in(1),
                    "assigned_to": self.assigned_to,
                }
            )
        return ops

    def _write(self, op: dict) -> dict:
        """Attempt one CRM write; queue it if the backend is (transiently) down."""
        request = (
            build_stage_update_payload(
                op["opportunity_id"], op["stage_id"], op.get("pipeline_id")
            )
            if op["kind"] == "stage_update"
            else build_task_payload(
                op["contact_id"],
                op["title"],
                op["body"],
                op["due_date"],
                op.get("assigned_to"),
            )
        )
        try:
            self.queue._apply(self.crm, op)
            return {"kind": op["kind"], "status": "done", "request": request}
        except TransientError as exc:
            self.queue.enqueue(op)
            return {
                "kind": op["kind"],
                "status": "queued",
                "request": request,
                "reason": str(exc),
            }
        except PermanentError as exc:
            # Poison write: dead-letter immediately, never retried.
            poison = dict(op, attempts=1, dead_reason=str(exc))
            self.queue.dead_letters.append(poison)
            return {
                "kind": op["kind"],
                "status": "dead_lettered",
                "request": request,
                "reason": str(exc),
            }
