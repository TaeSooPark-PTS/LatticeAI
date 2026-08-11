"""How the service reaches the graph layer and the memory tier.

One workspace-scoped graph slice (:meth:`_graph_sample`) feeds the health
report, the insights digest, the garden and both consistency scans, so they all
measure the same knowledge. The slice normalizes the store's ``from``/``to``
edge keys to the ``source``/``target`` the quality layer expects — once, here,
instead of at four call sites.

Both readers degrade rather than raise: an unreadable graph reports
``available: False`` and an unreadable memory tier reads as empty, because a
diagnosis that crashes is worse than one that says it could not measure.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._contract import BrainIntelligenceCore as _Core
from .constants import _GRAPH_SAMPLE_LIMIT, LOGGER


class BrainSamplingMixin(_Core):
    """The shared graph slice and memory read. Mixed into the service."""

    def _proactive(self) -> Any:
        """Lazy graph-layer ProactiveBrain over the injected store (or None)."""
        if not self._enable_graph:
            return None
        if self._proactive_brain is None:
            try:
                from lattice_brain.graph.proactive import ProactiveBrain

                self._proactive_brain = ProactiveBrain(
                    self._kg, sample_limit=_GRAPH_SAMPLE_LIMIT
                )
            except Exception:
                LOGGER.exception("proactive brain initialization failed")
                return None
        return self._proactive_brain

    # ── shared graph sampling ─────────────────────────────────────────────

    def _graph_sample(self, *, workspace_id: Optional[str]) -> Dict[str, Any]:
        """Recent graph slice with scoping applied. Empty when graph is off."""
        if not self._enable_graph:
            return {"nodes": [], "edges": [], "available": False}
        try:
            kwargs: Dict[str, Any] = {}
            if workspace_id is not None:
                kwargs["allowed_workspaces"] = {workspace_id}
            data = self._kg.graph(_GRAPH_SAMPLE_LIMIT, **kwargs)
            edges = []
            for edge in data.get("edges") or []:
                # Store emits "from"/"to"; the quality layer expects
                # "source"/"target". Normalize once here.
                normalized = dict(edge)
                normalized.setdefault("source", edge.get("from"))
                normalized.setdefault("target", edge.get("to"))
                edges.append(normalized)
            return {
                "nodes": list(data.get("nodes") or []),
                "edges": edges,
                "available": True,
            }
        except Exception as exc:
            LOGGER.exception("brain intelligence graph sample failed")
            return {"nodes": [], "edges": [], "available": False, "error": str(exc)}

    def _workspace_memories(
        self, *, user_email: Optional[str], workspace_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        if self._memory is None:
            return []
        try:
            inspected = self._memory.inspect(
                "workspace",
                user_email=user_email,
                workspace_id=workspace_id or "personal",
                limit=500,
            )
            return list(inspected.get("items") or [])
        except Exception:
            LOGGER.exception("brain intelligence memory read failed")
            return []
