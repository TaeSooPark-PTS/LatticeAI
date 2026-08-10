"""Exact cosine scan — the historical ``vector_search`` scoring loop, moved.

This backend is deliberately boring and deliberately the default. It compares
the query against every vector it holds, so its answer is the ground truth the
other backends are measured against (``scripts/bench_vector_index.py`` reports
recall@k relative to this one).

Behaviour parity with the pre-11.1.0 inline loop is the whole point of the
move: same similarity function (injected, so it is the embedder's own), same
``score < min_score`` drop, same stable ordering. ``retrieval_vector`` feeds
it in bounded batches so a scan never holds every decoded vector at once.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .base import (
    IndexItem,
    IndexStats,
    ScoredId,
    Similarity,
    dot_similarity,
    score_floor,
    take_top,
)

BRUTE_FORCE_BACKEND = "bruteforce-cosine"


class BruteForceIndex:
    """Exhaustive, exact, in-memory cosine index."""

    def __init__(
        self,
        *,
        dim: int = 0,
        similarity: Optional[Similarity] = None,
    ) -> None:
        self._dim = int(dim)
        self._similarity: Similarity = (
            dot_similarity if similarity is None else similarity
        )
        self._vectors: Dict[str, List[float]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}

    @property
    def backend(self) -> str:
        return BRUTE_FORCE_BACKEND

    @property
    def approx(self) -> bool:
        return False

    @property
    def exhaustive(self) -> bool:
        return True

    def add(
        self,
        id: str,
        vector: Sequence[float],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        values = [float(value) for value in vector]
        if not self._dim:
            self._dim = len(values)
        key = str(id)
        self._vectors[key] = values
        self._metadata[key] = dict(metadata or {})

    def remove(self, id: str) -> None:
        key = str(id)
        self._vectors.pop(key, None)
        self._metadata.pop(key, None)

    def rebuild(self, items: Iterable[IndexItem]) -> None:
        self._vectors.clear()
        self._metadata.clear()
        for item_id, vector, metadata in items:
            self.add(item_id, vector, metadata)

    def metadata_for(self, id: str) -> Dict[str, Any]:
        """Metadata stored alongside ``id`` (empty dict when unknown)."""
        return dict(self._metadata.get(str(id), {}))

    def search(
        self,
        query: Sequence[float],
        top_k: int,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> List[ScoredId]:
        floor = score_floor(filter)
        query_vector = list(query)
        scored: List[ScoredId] = []
        for item_id, vector in self._vectors.items():
            score = float(self._similarity(query_vector, vector))
            if score < floor:
                continue
            scored.append((item_id, score))
        return take_top(scored, top_k)

    def stats(self) -> IndexStats:
        return IndexStats(
            backend=self.backend,
            size=len(self._vectors),
            dim=self._dim,
            approx=False,
            exhaustive=True,
            detail=None,
        )


__all__ = ["BRUTE_FORCE_BACKEND", "BruteForceIndex"]
