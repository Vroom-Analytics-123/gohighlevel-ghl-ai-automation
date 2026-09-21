"""Retry queue: CRM writes that fail transiently wait their turn instead of
being silently dropped.

* :class:`TransientError` -> the op stays queued, attempts increment, and the
  retry waits ``delay_for_attempts(attempts)`` seconds (exponential backoff).
  After ``max_attempts`` failures it moves to ``dead_letters`` -- still
  visible, still inspectable, never silently dropped.
* :class:`PermanentError` (or an unknown op kind -- a poison payload) ->
  straight to ``dead_letters`` on the first attempt. Never retried forever.
"""

import itertools

from ghl_automation.crm_client import PermanentError, TransientError


class RetryQueue:
    """FIFO queue of CRM write operations with backoff + dead-lettering."""

    _op_ids = itertools.count(1)

    def __init__(self, max_attempts: int = 5, base_delay_s: int = 60):
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self._pending: list = []
        self.dead_letters: list = []

    @property
    def pending_count(self) -> int:
        """How many ops are still waiting for a successful write."""
        return len(self._pending)

    def delay_for_attempts(self, attempts: int) -> int:
        """Exponential backoff: base, 2x base, 4x base, ..."""
        return self.base_delay_s * (2 ** (attempts - 1))

    def enqueue(self, op: dict) -> str:
        """Queue one write op. Returns the op id."""
        op = dict(op)
        op["op_id"] = f"OP-{next(self._op_ids):04d}"
        op["attempts"] = 0
        self._pending.append(op)
        return op["op_id"]

    def _apply(self, crm, op: dict) -> None:
        """Perform one op against the CRM adapter. Raises on failure."""
        kind = op.get("kind")
        if kind == "stage_update":
            crm.update_opportunity_stage(
                op["opportunity_id"], op["stage_id"], op.get("pipeline_id")
            )
        elif kind == "task":
            crm.create_task(
                op["contact_id"],
                op["title"],
                op["body"],
                op["due_date"],
                op.get("assigned_to"),
            )
        else:
            # Unknown op kind: poison. Dead-letter it, never retry forever.
            raise PermanentError(f"unknown op kind: {kind!r}")

    def drain(self, crm) -> dict:
        """Attempt every queued op once, in FIFO order.

        Returns ``{"succeeded", "retried", "dead_lettered"}`` counts.
        """
        succeeded = retried = dead_lettered = 0
        still_pending: list = []
        for op in self._pending:
            try:
                self._apply(crm, op)
                succeeded += 1
            except PermanentError as exc:
                op["attempts"] += 1
                op["dead_reason"] = str(exc)
                self.dead_letters.append(op)
                dead_lettered += 1
            except TransientError:
                op["attempts"] += 1
                if op["attempts"] >= self.max_attempts:
                    op["dead_reason"] = (
                        f"transient failure after {op['attempts']} attempts"
                    )
                    self.dead_letters.append(op)
                    dead_lettered += 1
                else:
                    op["delay_before_retry_s"] = self.delay_for_attempts(op["attempts"])
                    still_pending.append(op)
                    retried += 1
        self._pending = still_pending
        return {
            "succeeded": succeeded,
            "retried": retried,
            "dead_lettered": dead_lettered,
        }
