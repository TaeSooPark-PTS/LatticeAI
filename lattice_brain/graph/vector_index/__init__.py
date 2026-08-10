"""Pluggable vector-index backends + the durable pending-embed queue.

Public surface (v11.1.0, Track 1):

* :class:`VectorIndex` — the Protocol every backend satisfies.
* :class:`BruteForceIndex` — exact, exhaustive, the default.
* :class:`QuantizedIndex` — int8 storage, exhaustive, approximate scores.
* :class:`HnswIndex` — approximate nearest neighbour, optional ``hnswlib``.
* :func:`resolve_vector_index` / :func:`build_index` — ``LATTICEAI_VECTOR_INDEX``
  resolution with an honest fallback reason.
* :class:`VectorEmbedQueue` — SQLite-backed background embedding backlog.
"""

from __future__ import annotations

from .base import (
    IndexItem,
    IndexStats,
    ScoredId,
    Similarity,
    VectorIndex,
    dot_similarity,
    score_floor,
    take_top,
)
from .brute_force import BRUTE_FORCE_BACKEND, BruteForceIndex
from .hnsw import (
    HNSW_BACKEND,
    HNSW_META_SUFFIX,
    HNSW_SUFFIX,
    HnswIndex,
    hnswlib_available,
    load_hnswlib,
    sidecar_paths,
)
from .jobs import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TICK_LIMIT,
    VECTOR_JOB_STATUSES,
    VectorEmbedQueue,
    VectorJobStore,
)
from .quantized import QUANTIZED_BACKEND, QuantizedIndex, quantize
from .selector import (
    DEFAULT_VECTOR_INDEX,
    VECTOR_INDEX_CHOICES,
    VECTOR_INDEX_ENV,
    BackendSelection,
    build_index,
    resolve_vector_index,
)

__all__ = [
    "BRUTE_FORCE_BACKEND",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_TICK_LIMIT",
    "DEFAULT_VECTOR_INDEX",
    "HNSW_BACKEND",
    "HNSW_META_SUFFIX",
    "HNSW_SUFFIX",
    "QUANTIZED_BACKEND",
    "VECTOR_INDEX_CHOICES",
    "VECTOR_INDEX_ENV",
    "VECTOR_JOB_STATUSES",
    "BackendSelection",
    "BruteForceIndex",
    "HnswIndex",
    "IndexItem",
    "IndexStats",
    "QuantizedIndex",
    "ScoredId",
    "Similarity",
    "VectorEmbedQueue",
    "VectorIndex",
    "VectorJobStore",
    "build_index",
    "dot_similarity",
    "hnswlib_available",
    "load_hnswlib",
    "quantize",
    "resolve_vector_index",
    "score_floor",
    "sidecar_paths",
    "take_top",
]
