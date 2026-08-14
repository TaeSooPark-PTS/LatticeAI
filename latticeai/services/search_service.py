"""The embedder report behind ``GET /api/embeddings/status``.

``SearchService`` was v3's retrieval orchestrator: keyword, vector, graph and
hybrid search, the fusion weights, the image-space query channel, the index
rebuild. ``lattice-retrieval`` owns all of it now, and the worker keeps the one
question that is a fact about *this* process — which embedding provider
resolved, at what width, and whether it is the one that was asked for.

The v11.6.0 boundary, stated because the payload used to blur it: this reports
the **embedder**, not the index. ``embeddings_status`` called
``graph.index_status()`` to fill an ``index`` block; the worker no longer opens
that store, and index completeness is a native jobs route. The key stays in the
response, empty, because a client that reads it should see "this answer has
nothing to say about the index" rather than a missing field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass
class SearchService:
    """The resolved embedder, shaped for the Models → Embeddings view."""

    #: A ``ResolvedEmbedder`` — the same object ``phase_brain`` hands the
    #: compute seams, so what this reports and what ``POST /worker/embed``
    #: produces can never disagree.
    embedder: Any = None

    def embeddings_status(
        self,
        *,
        resolved: Optional[Mapping[str, Any]] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """Report the active embedding provider for the Models → Embeddings UI.

        ``state`` is one of ``production`` | ``fallback`` | ``unavailable`` so
        the UI never shows a down provider as live. ``refresh`` re-probes the
        provider rather than reading the health recorded at resolution time.
        """
        resolved = dict(resolved or {})
        provider = getattr(self.embedder, "provider", None)

        meta: Dict[str, Any] = {}
        if provider is not None and hasattr(provider, "metadata"):
            try:
                meta = dict(provider.metadata())
            except Exception:
                meta = {}
        else:  # legacy LocalEmbeddingModel
            meta = {
                "provider": "hash",
                "model": getattr(provider, "model_id", "lattice-local-hash-v1"),
                "model_id": getattr(provider, "model_id", "lattice-local-hash-v1"),
                "dim": getattr(provider, "dim", 384),
                "grade": "fallback",
            }

        health = resolved.get("health") or {"status": "unknown", "detail": ""}
        if refresh and provider is not None and hasattr(provider, "health"):
            try:
                health = provider.health()
            except Exception as exc:  # pragma: no cover - defensive
                health = {"status": "unavailable", "detail": str(exc)}

        fell_back = bool(resolved.get("fell_back"))
        grade = str(meta.get("grade") or ("fallback" if fell_back else "production"))
        if fell_back or health.get("status") == "unavailable":
            state = "unavailable" if fell_back else "fallback"
        else:
            state = "fallback" if grade == "fallback" else "production"

        return {
            "provider": meta.get("provider"),
            "requested_provider": resolved.get("requested_provider") or meta.get("provider"),
            "active_provider": resolved.get("active_provider") or meta.get("provider"),
            "model": meta.get("model"),
            "model_id": meta.get("model_id"),
            "dimensions": meta.get("dim"),
            "grade": grade,
            "state": state,
            "fell_back": fell_back,
            "health": health,
            "detail": resolved.get("detail", ""),
            # Index completeness is native (lattice-jobs). Present and empty:
            # a reader learns this answer does not speak for the index, which
            # a missing key would not tell them.
            "last_indexed_at": None,
            "index": {},
            "available_providers": list(resolved.get("available_providers") or []),
        }


__all__ = ["SearchService"]
