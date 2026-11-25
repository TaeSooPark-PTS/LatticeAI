from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass
class SearchService:
    embedder: Any = None

    def embeddings_status(
        self,
        *,
        resolved: Optional[Mapping[str, Any]] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        resolved = dict(resolved or {})
        provider = getattr(self.embedder, "provider", None)

        meta: Dict[str, Any] = {}
        if provider is not None and hasattr(provider, "metadata"):
            try:
                meta = dict(provider.metadata())
            except (
                RuntimeError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
            ):
                meta = {}
        else:
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
            except (
                RuntimeError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
            ) as exc:
                health = {"status": "unavailable", "detail": str(exc)}

        fell_back = bool(resolved.get("fell_back"))
        grade = str(meta.get("grade") or ("fallback" if fell_back else "production"))
        if fell_back or health.get("status") == "unavailable":
            state = "unavailable" if fell_back else "fallback"
        else:
            state = "fallback" if grade == "fallback" else "production"

        return {
            "provider": meta.get("provider"),
            "requested_provider": resolved.get("requested_provider")
            or meta.get("provider"),
            "active_provider": resolved.get("active_provider") or meta.get("provider"),
            "model": meta.get("model"),
            "model_id": meta.get("model_id"),
            "dimensions": meta.get("dim"),
            "grade": grade,
            "state": state,
            "fell_back": fell_back,
            "health": health,
            "semantic": grade != "fallback",
            "detected": dict(resolved.get("detected") or {}),
            "identity": f"{meta.get('model_id')}:{meta.get('dim')}",
            "detail": resolved.get("detail", ""),
            "last_indexed_at": None,
            "index": {},
            "available_providers": list(resolved.get("available_providers") or []),
        }


__all__ = ["SearchService"]
