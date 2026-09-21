"""CRM write-back: the exact GoHighLevel API payload shapes for the pipeline
stage update and task creation, plus a simulated backend for tests.

Two failure classes -- the distinction drives the retry queue:

* :class:`TransientError` -- the backend is flaky or down; retry later.
* :class:`PermanentError` -- the request itself is poison; dead-letter it,
  never retry forever.

``SimulatedCrm`` implements the same :class:`CrmAdapter` interface with
in-memory data (zero network, zero credentials). Failure modes:

* ``fail_mode=None``       -- healthy backend
* ``fail_mode="flaky_once"`` -- first call raises, then recovers (retry path)
* ``fail_mode="always"``   -- every call raises (queued-retry path)
* ``fail_mode="permanent"`` -- every call raises ``PermanentError``
  (dead-letter path)
"""

import itertools
import time
from abc import ABC, abstractmethod


class CrmError(Exception):
    """Base class for every failure the CRM backend may report."""


class TransientError(CrmError):
    """Retryable failure: the backend is down or flaky."""


class PermanentError(CrmError):
    """Non-retryable failure: the request itself can never succeed."""


class CrmAdapter(ABC):
    """Pipeline-stage updates + task creation against the CRM.

    PRODUCTION SEAM: subclass this against the real GoHighLevel API
    (``PUT /opportunities/{id}``, ``POST /contacts/{id}/tasks``) with an API
    key from the environment -- never hardcoded, never in tool logic.
    The payload builders below give you the exact request shapes to POST.
    """

    @abstractmethod
    def update_opportunity_stage(
        self, opportunity_id: str, stage_id: str, pipeline_id: str | None = None
    ) -> dict:
        """Move an opportunity to a pipeline stage. Raise, never silently skip."""

    @abstractmethod
    def create_task(
        self,
        contact_id: str,
        title: str,
        body: str,
        due_date: str,
        assigned_to: str | None = None,
    ) -> dict:
        """Create a follow-up task on a contact. Raise, never silently skip."""


def build_stage_update_payload(
    opportunity_id: str, stage_id: str, pipeline_id: str | None = None
) -> dict:
    """Build the exact request for a GHL pipeline-stage update.

    Returns ``{"method", "path", "body"}`` -- the body is what you PUT to
    ``/opportunities/{opportunityId}``.
    """
    body: dict = {"pipelineStageId": stage_id, "status": "open"}
    if pipeline_id:
        body["pipelineId"] = pipeline_id
    return {
        "method": "PUT",
        "path": f"/opportunities/{opportunity_id}",
        "body": body,
    }


def build_task_payload(
    contact_id: str,
    title: str,
    body: str,
    due_date: str,
    assigned_to: str | None = None,
) -> dict:
    """Build the exact request for a GHL task creation.

    Returns ``{"method", "path", "body"}`` -- the body is what you POST to
    ``/contacts/{contactId}/tasks``.
    """
    task_body: dict = {"title": title, "body": body, "dueDate": due_date}
    if assigned_to:
        task_body["assignedTo"] = assigned_to
    return {
        "method": "POST",
        "path": f"/contacts/{contact_id}/tasks",
        "body": task_body,
    }


class SimulatedCrm(CrmAdapter):
    """In-memory CRM: ``opportunities`` maps id -> record, ``tasks`` is a list."""

    _task_ids = itertools.count(1)

    def __init__(self, opportunities=None, latency=0.0, fail_mode=None):
        self.opportunities = {
            key: dict(record) for key, record in (opportunities or {}).items()
        }
        self.tasks: list = []
        self.calls: list = []  # every attempted call, for test inspection
        self.latency = latency
        self.fail_mode = fail_mode
        self._calls = 0

    def _behave(self):
        """Simulate latency, then apply the configured failure mode."""
        if self.latency:
            time.sleep(self.latency)
        self._calls += 1
        if self.fail_mode == "always":
            raise TransientError("simulated backend failure")
        if self.fail_mode == "flaky_once" and self._calls == 1:
            raise TransientError("simulated transient failure")
        if self.fail_mode == "permanent":
            raise PermanentError("simulated permanent failure")

    def update_opportunity_stage(
        self, opportunity_id: str, stage_id: str, pipeline_id: str | None = None
    ) -> dict:
        self._behave()
        record = self.opportunities.setdefault(opportunity_id, {})
        record["stage_id"] = stage_id
        if pipeline_id:
            record["pipeline_id"] = pipeline_id
        self.calls.append(
            {"kind": "stage_update", "opportunity_id": opportunity_id, "stage_id": stage_id}
        )
        return {"opportunity_id": opportunity_id, "stage_id": stage_id}

    def create_task(
        self,
        contact_id: str,
        title: str,
        body: str,
        due_date: str,
        assigned_to: str | None = None,
    ) -> dict:
        self._behave()
        task_id = f"T-{next(self._task_ids):04d}"
        task = {
            "task_id": task_id,
            "contact_id": contact_id,
            "title": title,
            "body": body,
            "due_date": due_date,
            "assigned_to": assigned_to,
        }
        self.tasks.append(task)
        self.calls.append({"kind": "task", "contact_id": contact_id, "task_id": task_id})
        return {"task_id": task_id}
