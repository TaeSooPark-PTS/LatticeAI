"""Automation "run now" support: dry-run reports + execution log surfacing.

Backlog #6 (review §7.2 F, Wave 2): after installing an automation the user
should be able to run it once immediately — first as a **dry run** that
reports exactly what WOULD happen without side effects, then for real — and
every automation card should show its last execution result.

This module owns the deterministic policy pieces:

* :func:`dry_run_report` — walks the workflow's node graph (same normalizer
  as the engine) and describes each step without invoking any runner. No
  model call, no store write, no side effects.
* :func:`build_last_execution` / :func:`summarize_workflow_run` — the compact
  ``last_execution`` record persisted on the workflow's metadata and surfaced
  by the automation overview + daily briefing.
* :func:`last_execution_view` — read-side merge: the stamped metadata record
  vs. the newest persisted workflow run (so trigger-driven runs surface too).
* :func:`enqueue_failed_execution` — failed live executions become a review
  queue item (source ``workflow_run``, kind ``automation_failure``) so
  failures land in the same inbox as every other automation outcome.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from latticeai.core.timeutil import now_iso as _now

LOGGER = logging.getLogger(__name__)

AUTOMATION_CREATED_FROM = frozenset({
    "automation_suggestion",
    "brain_automation_recipe",
})

_TRIGGER_LABEL = {
    "interval": "wait for the user-enabled schedule",
    "brain_event": "wait for new Brain knowledge",
    "manual": "start when the user asks",
}


def is_automation_workflow(workflow: Dict[str, Any]) -> bool:
    metadata = (workflow or {}).get("metadata") or {}
    return metadata.get("created_from") in AUTOMATION_CREATED_FROM


def dry_run_report(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Describe what a run WOULD do — deterministically, with no side effects.

    Walks the normalized node chain (``trigger`` first, then ``next``
    pointers) and emits one human-readable step per node. Nothing is
    executed: no runner, no model, no store write.
    """
    from lattice_brain.workflow import normalize_definition, validate_definition

    definition = normalize_definition(workflow or {})
    errors = validate_definition(definition)
    metadata = definition.get("metadata") or {}
    steps: List[Dict[str, Any]] = []

    nodes = {node.get("id"): node for node in definition.get("nodes") or []}
    current = next(
        (node for node in definition.get("nodes") or [] if node.get("type") == "trigger"),
        None,
    )
    visited: set = set()
    while current is not None and current.get("id") not in visited:
        visited.add(current.get("id"))
        ntype = str(current.get("type") or "")
        config = current.get("config") or {}
        if ntype == "trigger":
            would = _TRIGGER_LABEL.get(
                str(config.get("trigger") or "manual"),
                "start when triggered",
            )
            would = f"skipped in a manual run ({would})"
        elif ntype == "agent":
            prompt = str(config.get("prompt") or config.get("goal") or "")[:160]
            would = f"ask the local agent to draft: {prompt}" if prompt else (
                "ask the local agent to draft a review item"
            )
        elif ntype == "tool":
            would = f"run tool '{config.get('tool') or current.get('name') or 'tool'}'"
        elif ntype == "skill":
            would = f"run skill '{config.get('skill') or current.get('name') or 'skill'}'"
        elif ntype == "plugin":
            would = f"run plugin '{config.get('plugin') or current.get('name') or 'plugin'}'"
        elif ntype == "condition":
            would = "evaluate a branch condition"
        elif ntype == "output":
            would = "deliver the draft to the review inbox"
        else:
            would = f"run node '{current.get('id')}'"
        steps.append({
            "node": current.get("id"),
            "type": ntype,
            "name": current.get("name") or current.get("id"),
            "would": would,
        })
        next_id = current.get("next")
        current = nodes.get(next_id) if next_id else None

    executable = [s for s in steps if s["type"] not in {"trigger", "output", "condition"}]
    creates = list(metadata.get("creates") or [])
    if errors:
        summary = "invalid workflow: " + "; ".join(errors[:3])
        status = "invalid"
    else:
        summary = (
            f"{len(executable)} step(s) would run locally and produce a reviewable draft"
        )
        if creates:
            summary += " (" + ", ".join(str(c) for c in creates[:3]) + ")"
        summary += "; no external actions, nothing is written until you approve."
        status = "ok"
    return {
        "mode": "dry_run",
        "status": status,
        "steps": steps,
        "summary": summary,
        "side_effects": False,
        "validation_errors": errors,
    }


def build_last_execution(
    *,
    mode: str,
    status: str,
    summary: str,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The compact per-automation execution record (persisted on metadata)."""
    return {
        "mode": mode,                     # dry_run | live
        "status": status,                 # ok | failed | running | partial | ...
        "summary": str(summary or "")[:300],
        "run_id": run_id,
        "finished_at": _now(),
    }


def summarize_workflow_run(run: Dict[str, Any]) -> str:
    """One honest line for a persisted workflow run."""
    status = str(run.get("status") or "unknown")
    timeline = run.get("timeline") or []
    if status == "failed":
        detail = ""
        for entry in reversed(timeline):
            if entry.get("status") in {"failed", "error"}:
                detail = str(
                    entry.get("reason") or entry.get("detail") or entry.get("errors") or ""
                )[:160]
                break
        if not detail:
            detail = str((run.get("outputs") or {}).get("error") or "")[:160]
        return f"failed after {len(timeline)} step(s)" + (f": {detail}" if detail else "")
    if status == "awaiting_approval":
        return "paused — a step needs your approval before it can run"
    if status in {"queued", "running", "cancelling"}:
        return "still running"
    return f"{status} — {len(timeline)} step(s) recorded"


def last_execution_view(
    workflow: Dict[str, Any],
    *,
    store: Any = None,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Read-side ``last_execution``: stamped metadata vs. newest persisted run.

    Run-now stamps ``metadata.last_execution`` directly; trigger/background
    runs only leave workflow-run rows. Whichever is newer wins so the card is
    honest about the latest execution regardless of how it was started.
    """
    metadata = (workflow or {}).get("metadata") or {}
    stamped = metadata.get("last_execution")
    stamped = dict(stamped) if isinstance(stamped, dict) else None

    latest_run: Optional[Dict[str, Any]] = None
    if store is not None and hasattr(store, "list_workflow_runs"):
        try:
            runs = (
                store.list_workflow_runs(
                    workflow_id=workflow.get("id"), limit=1, workspace_id=workspace_id
                )
                or {}
            ).get("runs") or []
            latest_run = runs[0] if runs else None
        except Exception:  # noqa: BLE001 — surfacing must never break the overview
            LOGGER.exception("automation last-execution run read failed")
            latest_run = None

    derived: Optional[Dict[str, Any]] = None
    if latest_run is not None:
        derived = {
            "mode": "live",
            "status": str(latest_run.get("status") or "unknown"),
            "summary": summarize_workflow_run(latest_run),
            "run_id": latest_run.get("id"),
            "finished_at": str(
                latest_run.get("completed_at")
                or latest_run.get("updated_at")
                or latest_run.get("created_at")
                or ""
            ),
        }

    if stamped is None:
        return derived
    if derived is None:
        return stamped
    return derived if str(derived.get("finished_at") or "") > str(
        stamped.get("finished_at") or ""
    ) else stamped


def enqueue_failed_execution(
    review_queue: Any,
    *,
    workflow: Dict[str, Any],
    run_id: Optional[str],
    error: str,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Failed live execution → review queue item (never raises)."""
    if review_queue is None:
        return None
    name = str(workflow.get("name") or "Automation")
    try:
        return review_queue.create(
            title=f"Automation failed: {name}"[:160],
            summary=str(error or "")[:500],
            source="workflow_run",
            kind="automation_failure",
            payload={
                "workflow_id": workflow.get("id"),
                "run_id": run_id,
                "error": str(error or "")[:500],
            },
            provenance={
                "workflow_id": workflow.get("id"),
                "run_id": run_id,
                "origin": "automation_run_now",
            },
            user_email=user_email,
            workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001 — review enqueue is best-effort surfacing
        LOGGER.exception("automation failure review enqueue failed")
        return None


__all__ = [
    "AUTOMATION_CREATED_FROM",
    "is_automation_workflow",
    "dry_run_report",
    "build_last_execution",
    "summarize_workflow_run",
    "last_execution_view",
    "enqueue_failed_execution",
]
