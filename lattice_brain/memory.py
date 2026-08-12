"""Memory System — typed, durable memory records on the brain substrate.

Decision and Experience records become first-class graph nodes through the
unified ingestion pipeline (provenance + hooks), instead of markdown dumps
with swallowed errors. Episodic memory is the conversation store; semantic
memory is the workspace MEMORY_KINDS records — this module adds the typed
record kinds the schema always had but never populated.

Only REAL events become memories: simulation runs are rejected at this
boundary (the run record's own mode field is checked — fabricated artifacts
must never enter the brain as experience).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .ingestion import IngestionItem


class BrainMemory:
    """Writes Decision / Experience records through the ingestion pipeline."""

    def __init__(self, ingestion_pipeline: Any):
        self._pipeline = ingestion_pipeline

    def available(self) -> bool:
        return self._pipeline is not None and self._pipeline.available()

    def record_experience(
        self,
        title: str,
        detail: str = "",
        *,
        run: Optional[Dict[str, Any]] = None,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a completed run/action as an Experience node.

        ``run`` is the persisted run record; simulated runs are refused —
        a simulation is replay scaffolding, not something that happened.
        """
        if run is not None and run.get("mode", "simulation") == "simulation":
            return {
                "status": "rejected",
                "detail": "simulation runs are not experiences and never enter the brain",
            }
        if not str(title or "").strip():
            raise ValueError("an experience needs a title")
        run_meta = {}
        if run is not None:
            run_meta = {
                "run_id": run.get("id"),
                "agent_id": run.get("agent_id"),
                "run_status": run.get("status"),
                "mode": run.get("mode"),
                "retries": run.get("retries"),
            }
        result = self._pipeline.ingest(
            IngestionItem(
                source_type="experience",
                title=title.strip(),
                text=detail,
                owner=user_email,
                workspace_id=workspace_id,
                metadata={**run_meta, **(metadata or {})},
            ),
            user_email=user_email,
        )
        return result.as_dict()


__all__ = ["BrainMemory"]
