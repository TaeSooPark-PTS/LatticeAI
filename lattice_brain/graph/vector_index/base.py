"""The vector-index contract: what it means to score embeddings (v11.1.0).

Before this package the only vector "index" was the scan loop inside
``retrieval_vector.vector_search``: fetch every embedding row, decode it,
score it, sort. That is a perfectly good *exact* index and it stays the
default — but it was welded to SQL row handling, so there was nowhere to put
a second strategy without forking the search.

This module defines the seam. A backend owns exactly one question — *given a
query vector, which stored ids score highest* — and nothing about SQLite,
chunk metadata, workspaces, or citations. The vectors are ``Sequence[float]``
(the repo's embedders return ``list[float]``; numpy is not a dependency).

Honesty contract: every backend reports whether it is ``approx`` (scores are
estimates) and whether it is ``exhaustive`` (every stored vector was
compared). Those two bits travel with the search result so a caller never has
to guess whether "no match" means "not in your brain" or "the index did not
look".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

#: ``(item_id, score)`` — the one shape every backend returns.
ScoredId = Tuple[str, float]
#: ``(item_id, vector, metadata)`` — one item as handed to :meth:`rebuild`.
IndexItem = Tuple[str, Sequence[float], Mapping[str, Any]]
#: Pluggable similarity so a backend can borrow the embedder's own function.
Similarity = Callable[[Sequence[float], Sequence[float]], float]

#: "no score floor" — ``0.0`` is a meaningful floor, so it cannot be the
#: sentinel (the score-0-is-falsy trap this codebase keeps re-learning).
NO_FLOOR = float("-inf")


def dot_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Dot product, which is cosine similarity for unit-length vectors.

    Mirrors :meth:`lattice_brain.embeddings.LocalEmbeddingModel.similarity`,
    including its refusal to compare different dimensions: truncating to the
    shorter vector produces a plausible-looking number that means nothing.
    """
    left_v, right_v = list(left), list(right)
    if len(left_v) != len(right_v):
        raise ValueError(
            f"embedding dimension mismatch: {len(left_v)} vs {len(right_v)}; "
            "the vector index was built with a different model"
        )
    return float(sum(a * b for a, b in zip(left_v, right_v, strict=True)))


def score_floor(filter: Optional[Mapping[str, Any]]) -> float:
    """Resolve ``filter["min_score"]`` (absent/unparseable → no floor)."""
    if not filter:
        return NO_FLOOR
    raw = filter.get("min_score")
    if raw is None:
        return NO_FLOOR
    try:
        return float(raw)
    except (TypeError, ValueError):
        return NO_FLOOR


def take_top(scored: List[ScoredId], top_k: int) -> List[ScoredId]:
    """Sort by score descending (stable → insertion order breaks ties), cut."""
    scored.sort(key=lambda pair: -pair[1])
    return scored[: max(0, int(top_k))]


@dataclass(frozen=True)
class IndexStats:
    """What a backend is willing to claim about itself."""

    backend: str
    size: int
    dim: int
    approx: bool
    exhaustive: bool
    available: bool = True
    detail: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "backend": self.backend,
            "size": self.size,
            "dim": self.dim,
            "approx": self.approx,
            "exhaustive": self.exhaustive,
            "available": self.available,
            "detail": self.detail,
        }


class VectorIndex(Protocol):
    """Structural contract implemented by every backend in this package.

    Declared, never implemented — same convention as
    :mod:`lattice_brain.graph._kg_contract`: the bodies raise so a stub can
    never be mistaken for a working index.
    """

    @property
    def backend(self) -> str:
        """Stable id of the scoring strategy (goes into ``recall.backend``)."""
        raise NotImplementedError

    @property
    def approx(self) -> bool:
        """True when scores are estimates rather than exact similarities."""
        raise NotImplementedError

    @property
    def exhaustive(self) -> bool:
        """True when every stored vector is compared on every search."""
        raise NotImplementedError

    def add(
        self,
        id: str,
        vector: Sequence[float],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        raise NotImplementedError

    def remove(self, id: str) -> None:
        raise NotImplementedError

    def rebuild(self, items: Iterable[IndexItem]) -> None:
        raise NotImplementedError

    def metadata_for(self, id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def search(
        self,
        query: Sequence[float],
        top_k: int,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> List[ScoredId]:
        raise NotImplementedError

    def stats(self) -> IndexStats:
        raise NotImplementedError


__all__ = [
    "NO_FLOOR",
    "IndexItem",
    "IndexStats",
    "ScoredId",
    "Similarity",
    "VectorIndex",
    "dot_similarity",
    "score_floor",
    "take_top",
]
