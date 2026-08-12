"""int8-quantized exhaustive index — same recall shape, a fraction of the RAM.

A 384-dimension embedding held as a Python ``list[float]`` costs ~3 KB (each
float is a boxed object plus a pointer); the same vector as an ``array("b")``
of int8 codes costs 384 bytes plus one header. On a 100 000-vector brain that
is the difference between ~300 MB and ~40 MB of resident scoring state, which
is what decides whether a large index can be held at all.

The trade is precision, not coverage: every vector is still compared (the
index is *exhaustive*), but each score is reconstructed from 8-bit codes, so
it lands within roughly ±1% of the exact cosine. Near-ties can therefore swap
places — which is why this backend reports ``approx = True`` and why the
default stays :class:`~.brute_force.BruteForceIndex`.

Symmetric per-vector scaling: each vector is divided by its own peak absolute
value before rounding, so a short vector keeps its full 8-bit resolution
instead of being crushed by the largest vector in the corpus.

Measured caveat (docs/PERFORMANCE.md, 10k vectors): the RAM saving above is a
property of the *data structure*, and the current search path does not cash it
in. ``retrieval_vector`` already feeds the index in bounded batches, so
resident vectors were never the dominant term — the fetched SQLite rows are —
and peak memory moves by ~1 MB while latency roughly doubles. This backend is
therefore honest, exhaustive, and currently not the one to choose; it exists
as the representation a *held* (cross-query) index would need.
"""

from __future__ import annotations

from array import array
from operator import mul
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .base import IndexItem, IndexStats, ScoredId, score_floor, take_top

QUANTIZED_BACKEND = "quantized-int8"
#: int8 keeps ``-128``; the symmetric range stops at ``-127`` so ``+peak`` and
#: ``-peak`` quantize to codes of equal magnitude.
INT8_MAX = 127


def quantize(vector: Sequence[float]) -> Tuple[array, float]:
    """``vector`` → (int8 codes, scale) with ``value ≈ code * scale``.

    An all-zero vector has no peak to scale against; it quantizes to all-zero
    codes with scale ``0.0``, which scores 0 against everything — the same
    answer the exact scan gives for a zero vector.
    """
    values = [float(value) for value in vector]
    peak = max((abs(value) for value in values), default=0.0)
    if peak <= 0.0:
        return array("b", bytes(len(values))), 0.0
    scale = peak / INT8_MAX
    codes = array(
        "b",
        (
            max(-INT8_MAX, min(INT8_MAX, int(round(value / scale))))
            for value in values
        ),
    )
    return codes, scale


class QuantizedIndex:
    """Exhaustive int8 index: full coverage, approximate scores."""

    def __init__(self, *, dim: int = 0) -> None:
        self._dim = int(dim)
        self._codes: Dict[str, array] = {}
        self._scales: Dict[str, float] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}

    @property
    def backend(self) -> str:
        return QUANTIZED_BACKEND

    @property
    def approx(self) -> bool:
        return True

    @property
    def exhaustive(self) -> bool:
        return True

    def add(
        self,
        id: str,
        vector: Sequence[float],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        codes, scale = quantize(vector)
        if not self._dim:
            self._dim = len(codes)
        key = str(id)
        self._codes[key] = codes
        self._scales[key] = scale
        self._metadata[key] = dict(metadata or {})

    def remove(self, id: str) -> None:
        key = str(id)
        self._codes.pop(key, None)
        self._scales.pop(key, None)
        self._metadata.pop(key, None)

    def rebuild(self, items: Iterable[IndexItem]) -> None:
        self._codes.clear()
        self._scales.clear()
        self._metadata.clear()
        for item_id, vector, metadata in items:
            self.add(item_id, vector, metadata)

    def search(
        self,
        query: Sequence[float],
        top_k: int,
        filter: Optional[Mapping[str, Any]] = None,
    ) -> List[ScoredId]:
        floor = score_floor(filter)
        query_codes, query_scale = quantize(query)
        scored: List[ScoredId] = []
        for item_id, codes in self._codes.items():
            if len(codes) != len(query_codes):
                raise ValueError(
                    f"embedding dimension mismatch: {len(query_codes)} vs "
                    f"{len(codes)}; the vector index was built with a "
                    "different model"
                )
            # Integer dot product, then one float multiply for the two scales:
            # all the arithmetic that decides the ranking stays in ints.
            raw = sum(map(mul, query_codes, codes))
            score = float(raw) * query_scale * self._scales[item_id]
            if score < floor:
                continue
            scored.append((item_id, score))
        return take_top(scored, top_k)

    def stats(self) -> IndexStats:
        return IndexStats(
            backend=self.backend,
            size=len(self._codes),
            dim=self._dim,
            approx=True,
            exhaustive=True,
            detail="scores are reconstructed from 8-bit codes (~1% error)",
        )


__all__ = ["INT8_MAX", "QUANTIZED_BACKEND", "QuantizedIndex", "quantize"]
