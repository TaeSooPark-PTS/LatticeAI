"""Brain review queue (5.6.0) — the suggestion inbox.

Automation/trigger runs drop drafts here; the user approves, dismisses, snoozes,
or unsnoozes them. This service owns the *policy* (legal status transitions,
snooze expiry semantics, run_now back-linking); the store owns persistence.

Design decisions (agreed in #develop-with-openclaw):

* **run_now ≠ approve.** "Run now" is a preview/regenerate action: it executes
  the underlying workflow but does **not** change ``status``. The fresh run id
  is back-linked into ``payload.last_run_id`` / ``provenance.run_id`` and
  ``updated_at`` is bumped. Accepting the result is a separate ``approve``.
* **Snooze expiry is read-time only.** A snoozed item whose ``snoozed_until``
  has passed is surfaced with ``effective_status == "pending"``; the stored
  ``status`` is left untouched (no scheduler mutation in 5.6.0).
* **Invalid transitions raise** :class:`InvalidReviewTransition` → HTTP 409.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional

from latticeai.core.io_utils import parse_iso as _parse_iso

# status: terminal vs. open. Open items can still be acted on.
OPEN_STATUSES = {"pending", "snoozed"}
TERMINAL_STATUSES = {"approved", "dismissed"}
ALL_STATUSES = OPEN_STATUSES | TERMINAL_STATUSES

REVIEW_SOURCES = frozenset({
    "workflow_run", "trigger", "kg_change_digest", "chat_followup",
    "agent_followup", "change_proposal",
})

# Which source statuses each action is allowed from.
_ALLOWED_FROM: Dict[str, set] = {
    "approve": {"pending", "snoozed"},
    "dismiss": {"pending", "snoozed"},
    "snooze": {"pending", "snoozed"},
    # unsnooze only makes sense from a *stored* snoozed state (not pending).
    "unsnooze": {"snoozed"},
    # run_now is a preview, not a transition — only while still open.
    "run_now": {"pending", "snoozed"},
}


def review_queue_opted_in(workflow: Dict[str, Any]) -> bool:
    """True when the workflow's trigger node opts into the review inbox."""
    for node in workflow.get("nodes") or []:
        if node.get("type") != "trigger":
            continue
        if (node.get("config") or {}).get("review_queue") is True:
            return True
    metadata = workflow.get("metadata") or {}
    # Compatibility for recipe drafts installed before review_queue became an
    # explicit trigger flag. They are local-only and already consent-gated.
    if (
        metadata.get("created_from") == "brain_automation_recipe"
        and metadata.get("external_actions") is False
    ):
        return True
    return False


class InvalidReviewTransition(Exception):
    """Raised when an action is not legal from the item's current status."""

    def __init__(self, action: str, status: str) -> None:
        self.action = action
        self.status = status
        super().__init__(f"cannot {action!r} a review item in status {status!r}")


class ReviewQueueService:
    """Policy layer over the store's workspace-scoped ``review_items``."""

    def __init__(self, *, store: Any, clock: Callable[[], datetime] = datetime.now) -> None:
        self._store = store
        self._clock = clock

    # ── creation (also the review_sink entry point) ──────────────────────
    def create(
        self,
        *,
        title: str,
        summary: str = "",
        source: str = "workflow_run",
        kind: str = "suggestion",
        payload: Optional[Dict[str, Any]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if source not in REVIEW_SOURCES:
            raise ValueError(f"source must be one of {sorted(REVIEW_SOURCES)}")
        item = self._store.create_review_item(
            title=title,
            summary=summary,
            source=source,
            kind=kind,
            payload=payload,
            provenance=provenance,
            user_email=user_email,
            workspace_id=workspace_id,
        )
        return self._view(item)

    def list(
        self, *, workspace_id: Optional[str] = None, user_email: Optional[str] = None,
        status: Optional[str] = None, source: Optional[str] = None,
    ) -> Dict[str, Any]:
        items = [
            self._view(it)
            for it in self._store.list_review_items(
                workspace_id=workspace_id, user_email=user_email, source=source,
            )
        ]
        if status:
            # Filter on the *effective* status so an expired snooze reads as pending.
            items = [it for it in items if it["effective_status"] == status]
        return {"items": items}

    def get(self, item_id: str, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        return self._view(self._store.get_review_item(item_id, workspace_id=workspace_id))

    # ── transitions ──────────────────────────────────────────────────────
    def approve(self, item_id: str, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        item = self._store.get_review_item(item_id, workspace_id=workspace_id)
        self._guard("approve", item)
        payload = dict(item.get("payload") or {})
        provenance = dict(item.get("provenance") or {})
        promoted = self._promote_agent_followup(item, workspace_id=workspace_id)
        if promoted:
            payload["promoted_workflow_id"] = promoted.get("id")
            provenance["workflow_id"] = promoted.get("id")
            provenance["promotion"] = "workflow_draft"
        patch: Dict[str, Any] = {"status": "approved", "payload": payload, "provenance": provenance}
        if item.get("snoozed_until") is not None:
            patch["snoozed_until"] = None
        updated = self._store.update_review_item(item_id, workspace_id=workspace_id, **patch)
        return self._view(updated)

    def dismiss(
        self, item_id: str, *, workspace_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dismiss an item; an optional ``reason`` is kept in provenance.

        The reason matters most for ``change_proposal`` items — the review
        timeline doubles as the change audit log, so "why was this rejected"
        should survive next to the staged diff.
        """
        if not reason:
            return self._transition(item_id, "dismiss", "dismissed", workspace_id=workspace_id)
        item = self._store.get_review_item(item_id, workspace_id=workspace_id)
        self._guard("dismiss", item)
        provenance = dict(item.get("provenance") or {})
        provenance["dismiss_reason"] = str(reason)[:500]
        patch: Dict[str, Any] = {"status": "dismissed", "provenance": provenance}
        if item.get("snoozed_until") is not None:
            patch["snoozed_until"] = None
        updated = self._store.update_review_item(item_id, workspace_id=workspace_id, **patch)
        return self._view(updated)

    def counts(
        self, *, workspace_id: Optional[str] = None, user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Badge-friendly effective-status counts (pending includes expired snoozes)."""
        items = [
            self._view(it)
            for it in self._store.list_review_items(
                workspace_id=workspace_id, user_email=user_email,
            )
        ]
        pending = [it for it in items if it["effective_status"] == "pending"]
        snoozed = [it for it in items if it["effective_status"] == "snoozed"]
        by_source: Dict[str, int] = {}
        for it in pending:
            source = str(it.get("source") or "workflow_run")
            by_source[source] = by_source.get(source, 0) + 1
        return {
            "pending": len(pending),
            "snoozed": len(snoozed),
            "pending_by_source": by_source,
        }

    def snooze(
        self, item_id: str, *, until: str, workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        item = self._store.get_review_item(item_id, workspace_id=workspace_id)
        self._guard("snooze", item)
        updated = self._store.update_review_item(
            item_id, workspace_id=workspace_id, status="snoozed", snoozed_until=until,
        )
        return self._view(updated)

    def unsnooze(self, item_id: str, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """Return a snoozed item to the pending queue.

        Only legal from a *stored* ``status == "snoozed"`` (an expired snooze is
        still stored as snoozed, so it remains unsnoozable). Clears the timer and
        sets ``status = "pending"``; any other source status raises 409.
        """
        item = self._store.get_review_item(item_id, workspace_id=workspace_id)
        self._guard("unsnooze", item)
        updated = self._store.update_review_item(
            item_id, workspace_id=workspace_id, status="pending", snoozed_until=None,
        )
        return self._view(updated)

    # ── run_now: preview/regenerate, NOT a status change ─────────────────
    def run_now(
        self,
        item_id: str,
        *,
        runner: Callable[[Dict[str, Any]], Any],
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute the item's underlying workflow and back-link the new run.

        ``status`` is intentionally left untouched — only ``payload.last_run_id``,
        ``provenance.run_id`` and ``updated_at`` move. ``runner`` receives the raw
        stored item and must return a run id (str) or a dict carrying one.
        """
        item = self._store.get_review_item(item_id, workspace_id=workspace_id)
        self._guard("run_now", item)
        run_id = _extract_run_id(runner(item))
        payload = dict(item.get("payload") or {})
        provenance = dict(item.get("provenance") or {})
        if run_id is not None:
            payload["last_run_id"] = run_id
            provenance["run_id"] = run_id
        updated = self._store.update_review_item(
            item_id, workspace_id=workspace_id, payload=payload, provenance=provenance,
        )
        return self._view(updated)

    # ── internals ────────────────────────────────────────────────────────
    def _transition(
        self, item_id: str, action: str, new_status: str, *, workspace_id: Optional[str],
    ) -> Dict[str, Any]:
        item = self._store.get_review_item(item_id, workspace_id=workspace_id)
        self._guard(action, item)
        patch: Dict[str, Any] = {"status": new_status}
        # Leaving the snooze state clears the timer so it can't resurface later.
        if item.get("snoozed_until") is not None:
            patch["snoozed_until"] = None
        updated = self._store.update_review_item(item_id, workspace_id=workspace_id, **patch)
        return self._view(updated)

    def _guard(self, action: str, item: Dict[str, Any]) -> None:
        status = str(item.get("status") or "pending")
        if status not in _ALLOWED_FROM.get(action, set()):
            raise InvalidReviewTransition(action, status)

    def _effective_status(self, item: Dict[str, Any]) -> str:
        """Read-time view: an expired snooze reads as pending (no mutation)."""
        if str(item.get("status")) != "snoozed":
            return str(item.get("status") or "pending")
        until = _parse_iso(item.get("snoozed_until"))
        if until is not None and until <= self._clock():
            return "pending"
        return "snoozed"

    def _view(self, item: Dict[str, Any]) -> Dict[str, Any]:
        view = dict(item)
        view["effective_status"] = self._effective_status(item)
        return view

    def _promote_agent_followup(self, item: Dict[str, Any], *, workspace_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if item.get("source") != "agent_followup" or not hasattr(self._store, "create_workflow"):
            return None
        payload = dict(item.get("payload") or {})
        followup = str(payload.get("followup") or item.get("title") or "").strip()
        if not followup:
            return None
        goal = str(payload.get("goal") or item.get("summary") or followup).strip()
        nodes = [
            {
                "id": "trigger",
                "type": "trigger",
                "config": {"trigger": "manual", "review_queue": False},
                "next": "agent",
            },
            {
                "id": "agent",
                "type": "agent",
                "config": {
                    "goal": followup,
                    "roles": ["planner", "executor", "reviewer"],
                    "source": "agent_followup",
                },
                "next": "output",
            },
            {
                "id": "output",
                "type": "output",
                "config": {"format": "review_followup"},
                "next": None,
            },
        ]
        return self._store.create_workflow(
            name=f"Follow-up: {str(item.get('title') or followup)[:96]}",
            steps=[{"action": "agent", "goal": followup}],
            nodes=nodes,
            metadata={
                "source": "review_center",
                "review_item_id": item.get("id"),
                "agent_followup": followup,
                "goal": goal,
                "draft": True,
            },
            user_email=item.get("user_email"),
            workspace_id=workspace_id or item.get("workspace_id"),
        )


def enqueue_from_automation(
    sink: "ReviewQueueService",
    *,
    workflow: Dict[str, Any],
    source: str,
    run_result: Any,
    trigger_info: Optional[Dict[str, Any]] = None,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Opt-in path: enqueue a review item when the workflow trigger requests it."""
    if source not in REVIEW_SOURCES:
        raise ValueError(f"source must be one of {sorted(REVIEW_SOURCES)}")
    if not review_queue_opted_in(workflow):
        return None
    run_id = _extract_run_id(run_result)
    wf_id = workflow.get("id")
    provenance: Dict[str, Any] = {"workflow_id": wf_id}
    if run_id is not None:
        provenance["run_id"] = run_id
    if trigger_info:
        provenance["trigger_id"] = str(trigger_info.get("type") or "")
        provenance["source_detail"] = str(trigger_info.get("type") or "")
    return sink.create(
        title=str(workflow.get("name") or "Automation suggestion"),
        summary="",
        source=source,
        kind="suggestion",
        payload={"workflow_id": wf_id},
        provenance=provenance,
        user_email=user_email,
        workspace_id=workspace_id,
    )


def _extract_run_id(result: Any) -> Optional[str]:
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if result.get("run_id"):
            return str(result["run_id"])
        if result.get("workflow_run_id"):
            return str(result["workflow_run_id"])
        if result.get("agent_run_id"):
            return str(result["agent_run_id"])
        run = result.get("run")
        if isinstance(run, dict) and run.get("id"):
            return str(run["id"])
        if result.get("id"):
            return str(result["id"])
    return None


__all__ = [
    "ReviewQueueService",
    "InvalidReviewTransition",
    "REVIEW_SOURCES",
    "review_queue_opted_in",
    "enqueue_from_automation",
]
