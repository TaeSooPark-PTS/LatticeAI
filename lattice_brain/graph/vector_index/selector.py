"""Which backend scores this search, and — when it is not the one you asked
for — why.

``LATTICEAI_VECTOR_INDEX`` selects ``brute`` (default) / ``quantized`` /
``hnsw``. Two failure modes have to stay loud, because both otherwise look
exactly like "search is a bit worse today":

* an unknown name (typo, stale config) must not silently mean "the default";
* asking for ``hnsw`` without the optional extra installed must not silently
  mean "you have ANN".

Both resolve to the exact brute-force scan and carry a ``detail`` string
naming the cause, which ``index_status()`` and the search result surface.

The backend is a *choice*, not a switch, so it cannot ride a
:class:`~lattice_brain.gates.FeatureGate` (which answers booleans). It gets the
same shape of seam instead: :func:`bind_vector_index_resolver` installs a
caller-supplied resolver that is consulted ahead of the environment, which is
what lets the settings panel change the backend without a restart. With nothing
bound — the default — the environment variable is still the whole control
surface, exactly as before.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .base import Similarity, VectorIndex
from .brute_force import BRUTE_FORCE_BACKEND, BruteForceIndex
from .hnsw import HNSW_BACKEND, HnswIndex, load_hnswlib
from .quantized import QUANTIZED_BACKEND, QuantizedIndex

VECTOR_INDEX_ENV = "LATTICEAI_VECTOR_INDEX"
DEFAULT_VECTOR_INDEX = "brute"
#: Accepted values of ``LATTICEAI_VECTOR_INDEX``.
VECTOR_INDEX_CHOICES = ("brute", "quantized", "hnsw")

_BACKEND_LABELS = {
    "brute": BRUTE_FORCE_BACKEND,
    "quantized": QUANTIZED_BACKEND,
    "hnsw": HNSW_BACKEND,
}
_APPROX = {"brute": False, "quantized": True, "hnsw": True}
_EXHAUSTIVE = {"brute": True, "quantized": True, "hnsw": False}

#: App-layer resolver, consulted before the environment. ``None`` (the default)
#: leaves this module reading exactly the env var it always read.
_RESOLVER: Optional[Callable[[], Optional[str]]] = None


def bind_vector_index_resolver(
    resolver: Optional[Callable[[], Optional[str]]],
) -> None:
    """Delegate backend selection to a callable (``None`` hands it back to env).

    A resolver that returns ``None`` also falls through to the environment, so
    "the settings service has no opinion" and "there is no settings service"
    reach the same answer instead of two.
    """
    global _RESOLVER
    _RESOLVER = resolver


@dataclass(frozen=True)
class BackendSelection:
    """The resolved backend plus an honest account of any substitution."""

    requested: str
    name: str
    backend: str
    approx: bool
    exhaustive: bool
    detail: Optional[str] = None

    @property
    def honored(self) -> bool:
        """True when the caller got the backend they asked for."""
        return self.requested == self.name

    def as_dict(self) -> Dict[str, Any]:
        return {
            "requested": self.requested,
            "backend": self.backend,
            "name": self.name,
            "approx": self.approx,
            "exhaustive": self.exhaustive,
            "honored": self.honored,
            "detail": self.detail,
        }


def _selection(name: str, *, requested: str, detail: Optional[str]) -> BackendSelection:
    return BackendSelection(
        requested=requested,
        name=name,
        backend=_BACKEND_LABELS[name],
        approx=_APPROX[name],
        exhaustive=_EXHAUSTIVE[name],
        detail=detail,
    )


def resolve_vector_index(requested: Optional[str] = None) -> BackendSelection:
    """Resolve the configured backend (never raises, always falls back safe)."""
    raw = requested
    if raw is None and _RESOLVER is not None:
        raw = _RESOLVER()
    if raw is None:
        raw = os.getenv(VECTOR_INDEX_ENV, "")
    name = str(raw or "").strip().lower() or DEFAULT_VECTOR_INDEX
    if name not in VECTOR_INDEX_CHOICES:
        return _selection(
            DEFAULT_VECTOR_INDEX,
            requested=name,
            detail=(
                f"unknown vector index backend {name!r}; expected one of "
                f"{', '.join(VECTOR_INDEX_CHOICES)} — using the exact "
                "brute-force scan"
            ),
        )
    if name == "hnsw":
        module, reason = load_hnswlib()
        if module is None:
            return _selection(
                DEFAULT_VECTOR_INDEX,
                requested=name,
                detail=f"{reason} — using the exact brute-force scan",
            )
    return _selection(name, requested=name, detail=None)


def build_index(
    selection: BackendSelection,
    *,
    dim: int = 0,
    similarity: Optional[Similarity] = None,
) -> VectorIndex:
    """Instantiate the backend named by ``selection``.

    ``similarity`` is threaded into the exact backend only: quantized and
    HNSW score in their own representation, and pretending otherwise would
    make an injected function look respected when it is not.
    """
    if selection.name == "quantized":
        return QuantizedIndex(dim=dim)
    if selection.name == HNSW_BACKEND:
        return HnswIndex(dim=dim)
    return BruteForceIndex(dim=dim, similarity=similarity)


__all__ = [
    "DEFAULT_VECTOR_INDEX",
    "VECTOR_INDEX_CHOICES",
    "VECTOR_INDEX_ENV",
    "BackendSelection",
    "bind_vector_index_resolver",
    "build_index",
    "resolve_vector_index",
]
