"""Automation runtime assembly for review, trigger, agent, and run execution.

This module keeps the automation object graph behind one construction seam.
Router registration remains in ``app_factory`` so route order stays unchanged.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def build_automation_runtime(
    *,
    store: Any,
    platform: Any,
    data_dir: Any,
    workspace_graph: Callable[..., Any],
    append_audit_event: Callable[..., Any],
    hooks: Any,
    tz_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct automation services and attach the run executor boundary."""

    from lattice_brain.runtime.agent_runtime import AgentRuntime
    from latticeai.services.review_queue import ReviewQueueService
    from latticeai.services.run_executor import RunExecutor
    from latticeai.services.triggers import TriggerService

    review_queue = ReviewQueueService(store=store)

    def _run_triggered_workflow(workflow_id: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        trigger = (inputs or {}).get("__trigger__") or {}
        return platform.run_workflow_by_id(
            workflow_id,
            trigger.get("user_email"),
            trigger.get("workspace_id"),
            with_agent=True,
            inputs=inputs,
        )

    trigger_service = TriggerService(
        store=store,
        run_workflow=_run_triggered_workflow,
        data_dir=data_dir,
        review_sink=review_queue,
        tz_name=tz_name,
    )

    def _memory_ingest(**kwargs):
        try:
            # Delegate to store's upsert (enriches memories + KG automatically via internal graph.ingest_event)
            return store.upsert_memory(**kwargs)
        except Exception:
            return None

    agent_runtime = AgentRuntime(
        store=store,
        orchestrator_factory=platform.build_orchestrator,
        workspace_graph=workspace_graph,
        append_audit_event=append_audit_event,
        hooks=hooks,
        memory_ingest=_memory_ingest,
        review_sink=review_queue,
    )
    run_executor = RunExecutor(
        store=store,
        agent_runtime=agent_runtime,
        build_workflow_runners=platform.build_workflow_runners,
        workspace_graph=workspace_graph,
        append_audit_event=append_audit_event,
        hooks=hooks,
        review_sink=review_queue,
    )
    agent_runtime.attach_executor(run_executor)

    return {
        "REVIEW_QUEUE": review_queue,
        "TRIGGER_SERVICE": trigger_service,
        "AGENT_RUNTIME": agent_runtime,
        "RUN_EXECUTOR": run_executor,
    }
