"""Vector search: backend selection, the candidate cap, and scoring.

The module-level knobs live **here**, next to the only code that reads them —
``VECTOR_SCAN_BATCH`` in particular, whose patch target is therefore
``lattice_brain.graph.retrieval_vector.search``. Moved verbatim out of
``retrieval_vector.py`` (v11.3.0 decomposition).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401
from ..vector_index import (
    DEFAULT_VECTOR_INDEX,
    HNSW_BACKEND,
    VECTOR_INDEX_ENV,
    BackendSelection,
    HnswIndex,
    IndexItem,
    VectorIndex,
    build_index,
    resolve_vector_index,
)

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged.
if TYPE_CHECKING:
    from .._kg_contract import KnowledgeGraphCore as _Core
else:
    _Core = object


# ── brute-force recall cap (review 2026-08 P1 #2) ────────────────────────────
# There is no ANN index in the default build: sqlite-vec is an optional
# dependency and, when it is absent, `index_status()["storage"]` honestly
# reports ``vector_search_backend: "bruteforce-cosine"``. Brute force scores
# every candidate in Python, so *some* cap is unavoidable on a large graph.
#
# What is not acceptable is a SILENT cap. The pre-10.7 code took the 10 000
# most recently indexed rows — ordered by ``indexed_at``, i.e. by recency, not
# by similarity — and returned them as if they were the whole index, so recall
# on a 200 000-row brain quietly became "the newest 5%". The cap is now
# explicit, configurable, and reported back to the caller in ``recall``.
#
# ``LATTICEAI_VECTOR_MAX_CANDIDATES`` overrides the default; ``0`` means "no
# cap — scan the whole index" (exact recall, paid for in latency).
VECTOR_MAX_CANDIDATES_ENV = "LATTICEAI_VECTOR_MAX_CANDIDATES"
DEFAULT_VECTOR_MAX_CANDIDATES = 10_000
#: Upper bound for a configured cap; ``0``/``None`` still means uncapped.
VECTOR_MAX_CANDIDATES_CEILING = 500_000

# ── scan batching (v11.1.0) ──────────────────────────────────────────────────
# The exact scan hands its candidates to a VectorIndex, which by definition
# holds what it is given. Handing it the whole result set would make peak
# memory O(rows × dim) floats, so the scan feeds it in fixed batches instead:
# exhaustive backends score every batch independently, so the union is
# identical to one big pass, at O(batch × dim) resident cost.
VECTOR_SCAN_BATCH = 512


def _configured_vector_max_candidates() -> Optional[int]:
    """Resolve the candidate cap from the environment (None = uncapped).

    Never raises: an unparseable value falls back to the documented default
    rather than breaking every search.
    """
    raw = os.getenv(VECTOR_MAX_CANDIDATES_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_VECTOR_MAX_CANDIDATES
    try:
        value = int(raw.strip())
    except ValueError:
        logging.warning(
            "%s=%r is not an integer — using the default cap of %d",
            VECTOR_MAX_CANDIDATES_ENV, raw, DEFAULT_VECTOR_MAX_CANDIDATES,
        )
        return DEFAULT_VECTOR_MAX_CANDIDATES
    if value <= 0:
        return None  # explicit opt-in to an exhaustive scan
    return min(value, VECTOR_MAX_CANDIDATES_CEILING)


class _VectorSearchMixin(_Core):
    """Vector search + scoring. Composed into the public mixin."""

    def _vector_index_selection(self) -> BackendSelection:
        """The configured in-process index backend (``LATTICEAI_VECTOR_INDEX``).

        Resolved per call rather than cached: the env var is the whole control
        surface, and a cached selection would make a config change look like
        it had no effect. The only expensive part — importing ``hnswlib`` —
        is already cached by ``sys.modules``.
        """
        return resolve_vector_index()

    def _vector_search_backend(self) -> str:
        """Which backend actually scores the vectors.

        An explicitly selected in-process index (quantized / hnsw) wins,
        because it is the thing that will do the scoring. Otherwise this is
        the storage layer's answer: sqlite-vec exposes an ANN index; without
        it this store scores rows in Python (``bruteforce-cosine``). Never
        raises — a capability probe failure means "we cannot claim ANN",
        which is the brute-force answer.
        """
        selection = self._vector_index_selection()
        if selection.name != DEFAULT_VECTOR_INDEX:
            return selection.backend
        try:
            capabilities = self.storage_engine.capabilities().as_dict()
        except Exception:  # noqa: BLE001 — a probe failure is not an ANN index
            return "bruteforce-cosine"
        backend = (capabilities or {}).get("vector_backend")
        return str(backend) if backend else "bruteforce-cosine"

    @staticmethod
    def _recall_report(
        *,
        backend: str,
        cap: Optional[int],
        candidates_total: int,
        candidates_scanned: int,
        approx_detail: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The honest answer to "did this search see the whole index?".

        ``approx_detail`` covers the second way recall can be incomplete: an
        ANN backend *visits* the whole index but is not guaranteed to return
        its true top-k. "Scanned N of N" with no detail would read as an exact
        answer, so the approximate backends supply their caveat here.
        """
        truncated = candidates_scanned < candidates_total
        detail: Optional[str] = None
        if truncated:
            detail = (
                f"partial recall: scored the {candidates_scanned} most recently "
                f"indexed vectors of {candidates_total}. The cut is by index "
                f"recency, not similarity, so older matches were never compared. "
                f"Raise {VECTOR_MAX_CANDIDATES_ENV} (0 = scan everything), or "
                f"switch to an index that covers the whole set: "
                f"{VECTOR_INDEX_ENV}=hnsw (needs the optional hnsw extra) or "
                f"install sqlite-vec."
            )
        elif approx_detail:
            detail = approx_detail
        return {
            "backend": backend,
            "max_candidates": cap,
            "candidates_total": candidates_total,
            "candidates_scanned": candidates_scanned,
            "truncated": truncated,
            "detail": detail,
        }

    def _vector_candidate_cap(
        self, requested: Optional[int], *, limit: int
    ) -> Optional[int]:
        """Resolve the effective candidate cap (None = scan everything).

        ``requested is None`` uses the configured/default cap; an explicit
        ``<= 0`` is the caller asking for an exhaustive scan. Note the
        ``is None`` test: ``0`` is a meaningful value here, so truthiness
        would silently turn "no cap" into "the default cap".
        """
        if requested is None:
            cap = _configured_vector_max_candidates()
        elif int(requested) <= 0:
            cap = None
        else:
            cap = min(int(requested), VECTOR_MAX_CANDIDATES_CEILING)
        if cap is None:
            return None
        # Never scan fewer rows than the caller intends to receive.
        return max(limit, cap)

    # One row shape feeds every vector match, so both the exact scan and the
    # ANN lookup project exactly the same columns; only the WHERE/ORDER tail
    # differs. Bound values are always parameters — the interpolation below is
    # a placeholder list, never data.
    _VECTOR_ROW_SELECT = """
                    SELECT
                      ve.item_id, ve.item_type, ve.source_node, ve.embedding,
                      ve.embedding_dim, ve.embedding_model, ve.metadata_json AS vector_metadata,
                      n.type AS node_type, n.title AS node_title, n.summary AS node_summary,
                      n.metadata_json AS node_metadata, n.updated_at AS node_updated_at,
                      c.text AS chunk_text, c.source_node AS parent_node_id,
                      c.metadata_json AS chunk_metadata,
                      pn.type AS parent_type, pn.title AS parent_title,
                      pn.summary AS parent_summary, pn.metadata_json AS parent_metadata,
                      pn.updated_at AS parent_updated_at
                    FROM vector_embeddings ve
                    LEFT JOIN nodes n ON n.id=ve.source_node
                    LEFT JOIN chunks c ON c.id=ve.item_id
                    LEFT JOIN nodes pn ON pn.id=c.source_node
                    WHERE ve.embedding_model=? AND ve.embedding_dim=?
                    """

    @staticmethod
    def _vector_match(row: sqlite3.Row, score: float) -> Dict[str, Any]:
        """One scored embedding row → one search match (pure projection)."""
        is_chunk = row["item_type"] == "chunk"
        summary = (
            row["chunk_text"] if is_chunk and row["chunk_text"] else row["node_summary"]
        )
        parent_metadata = _safe_loads(row["parent_metadata"])
        node_metadata = _safe_loads(row["node_metadata"])
        # Citation precision (review 2026-07-27 P1 #4): a chunk hit used to
        # cite only its parent document, so a 200-page PDF answered with
        # "from report.pdf". The chunk's own provenance (section heading,
        # page, offset) now rides along, and `locator` is the one-line
        # human form — absent when the chunk carries no such metadata.
        chunk_metadata = _safe_loads(row["chunk_metadata"]) if is_chunk else {}
        locator = citation_locator(chunk_metadata)
        return {
            "id": row["item_id"],
            "node_id": row["parent_node_id"]
            if is_chunk and row["parent_node_id"]
            else row["source_node"],
            "item_type": row["item_type"],
            "type": "Chunk" if is_chunk else row["node_type"],
            "title": row["parent_title"]
            if is_chunk and row["parent_title"]
            else row["node_title"],
            "summary": _clean_text(summary or "")[:1000],
            "score": round(float(score), 6),
            "metadata": {
                **(parent_metadata if is_chunk else node_metadata),
                "vector": _safe_loads(row["vector_metadata"]),
                "parent_node_id": row["parent_node_id"],
                "parent_type": row["parent_type"],
                **({"chunk": chunk_metadata} if chunk_metadata else {}),
                **({"locator": locator} if locator else {}),
            },
            "updated_at": row["parent_updated_at"]
            if is_chunk and row["parent_updated_at"]
            else row["node_updated_at"],
        }

    @staticmethod
    def _flush_scan_batch(
        index: VectorIndex,
        batch: List[IndexItem],
        query_vector: List[float],
        min_score: float,
        scores: Dict[str, float],
    ) -> None:
        """Score one batch into ``scores`` and empty it."""
        if not batch:
            return
        index.rebuild(batch)
        scores.update(
            index.search(query_vector, len(batch), filter={"min_score": min_score})
        )
        batch.clear()

    def _score_vector_rows(
        self,
        rows: List[sqlite3.Row],
        query_vector: List[float],
        selection: BackendSelection,
        *,
        min_score: float,
    ) -> Dict[str, float]:
        """``item_id -> score`` for every row that clears ``min_score``."""
        index = build_index(
            selection,
            dim=int(self._embedding_model.dim),
            similarity=self._embedding_model.similarity,
        )
        scores: Dict[str, float] = {}
        batch: List[IndexItem] = []
        for row in rows:
            batch.append(
                (
                    str(row["item_id"]),
                    self._embedding_model.decode(
                        row["embedding"], row["embedding_dim"]
                    ),
                    {"item_type": row["item_type"]},
                )
            )
            if len(batch) >= VECTOR_SCAN_BATCH:
                self._flush_scan_batch(index, batch, query_vector, min_score, scores)
        self._flush_scan_batch(index, batch, query_vector, min_score, scores)
        return scores

    def _vector_search_scan(
        self,
        query: str,
        query_vector: List[float],
        selection: BackendSelection,
        *,
        limit: int,
        min_score: float,
        backend: str,
        cap: Optional[int],
    ) -> Dict[str, Any]:
        """Exhaustive scan of (at most ``cap``) rows — the historical path."""
        sql = self._VECTOR_ROW_SELECT + " ORDER BY ve.indexed_at DESC"
        params: List[Any] = [
            self._embedding_model.model_id,
            self._embedding_model.dim,
        ]
        if cap is not None:
            sql += " LIMIT ?"
            params.append(cap)
        with self._connect() as conn:
            # Counted in the same transaction as the scan so "scanned N of M"
            # cannot describe two different index states.
            candidates_total = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM vector_embeddings "
                    "WHERE embedding_model=? AND embedding_dim=?",
                    (self._embedding_model.model_id, self._embedding_model.dim),
                ).fetchone()["c"]
            )
            rows = conn.execute(sql, tuple(params)).fetchall()
        recall = self._recall_report(
            backend=backend,
            cap=cap,
            candidates_total=candidates_total,
            candidates_scanned=len(rows),
            approx_detail=(
                "approximate backend: every candidate was compared, but the "
                "scores are estimates, so near-ties can reorder"
                if selection.approx
                else None
            ),
        )
        scores = self._score_vector_rows(
            rows, query_vector, selection, min_score=min_score
        )
        # Rows are walked in index order (not score order) so the sort below
        # sees exactly the input ordering the pre-11.1.0 inline loop produced:
        # a stable sort makes that the tie-break of last resort.
        scored = [
            self._vector_match(row, scores[str(row["item_id"])])
            for row in rows
            if str(row["item_id"]) in scores
        ]
        scored.sort(
            key=lambda item: (item["score"], item.get("updated_at") or ""), reverse=True
        )
        return {
            "query": query,
            "embedding_model": self._embedding_model.model_id,
            "embedding_dim": self._embedding_model.dim,
            "matches": scored[:limit],
            "recall": recall,
            "index": selection.as_dict(),
        }

    def _iter_vector_index_items(
        self, conn: sqlite3.Connection, model_id: str, dim: int
    ) -> Iterator[IndexItem]:
        """Every embedding for ``model_id``/``dim`` as index items."""
        for row in conn.execute(
            "SELECT item_id, embedding, embedding_dim FROM vector_embeddings "
            "WHERE embedding_model=? AND embedding_dim=? ORDER BY item_id ASC",
            (model_id, dim),
        ):
            yield (
                str(row["item_id"]),
                self._embedding_model.decode(row["embedding"], row["embedding_dim"]),
                {},
            )

    def _vector_rows_by_id(
        self, conn: sqlite3.Connection, item_ids: List[str]
    ) -> List[sqlite3.Row]:
        """Full match rows for the ids an ANN lookup returned."""
        if not item_ids:
            return []
        placeholders = ",".join("?" * len(item_ids))
        return conn.execute(
            self._VECTOR_ROW_SELECT + f" AND ve.item_id IN ({placeholders})",
            (self._embedding_model.model_id, self._embedding_model.dim, *item_ids),
        ).fetchall()

    def _hnsw_index(
        self,
        conn: sqlite3.Connection,
        fingerprint: str,
        model_id: str,
        dim: int,
    ) -> HnswIndex:
        """The live ANN graph for ``fingerprint`` — cache, sidecar, or rebuild.

        Held on the store for the process's lifetime, because reading a
        50 000-vector graph off disk costs roughly as much as the search it
        enables: paying it per query gave back most of the speedup (105 ms
        instead of 15 ms at 50k). The fingerprint — model, dimension, row
        count, newest ``indexed_at`` — is what makes the cache safe: any write
        to ``vector_embeddings`` changes it, and a changed fingerprint is
        never served from the cache or from the sidecar.
        """
        cached = getattr(self, "_hnsw_cached", None)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        index = HnswIndex(dim=dim)
        if not index.load(self.db_path, fingerprint=fingerprint):
            index.rebuild(self._iter_vector_index_items(conn, model_id, dim))
            index.save(self.db_path, fingerprint=fingerprint)
        self._hnsw_cached = (fingerprint, index)
        return index

    def _vector_search_ann(
        self,
        query: str,
        query_vector: List[float],
        selection: BackendSelection,
        *,
        limit: int,
        min_score: float,
        backend: str,
    ) -> Optional[Dict[str, Any]]:
        """Approximate top-k via the persisted HNSW sidecar.

        Two phases instead of one: ask the graph for ids, then read only those
        rows. That is where the speed comes from — the exact scan pays to
        decode every embedding on every query, and this pays it once per
        index generation.

        The sidecar is keyed by ``model:dim:rows:newest`` so any write to
        ``vector_embeddings`` invalidates it and the next search rebuilds.
        Returns ``None`` when the index is empty, which the caller answers
        with the ordinary (equally empty, but honestly reported) scan.
        """
        model_id = self._embedding_model.model_id
        dim = int(self._embedding_model.dim)
        with self._connect() as conn:
            head = conn.execute(
                "SELECT COUNT(*) AS c, MAX(indexed_at) AS newest FROM vector_embeddings "
                "WHERE embedding_model=? AND embedding_dim=?",
                (model_id, dim),
            ).fetchone()
            candidates_total = int(head["c"])
            if candidates_total == 0:
                return None
            fingerprint = f"{model_id}:{dim}:{candidates_total}:{head['newest']}"
            index = self._hnsw_index(conn, fingerprint, model_id, dim)
            pairs = index.search(query_vector, limit, filter={"min_score": min_score})
            rows = {
                str(row["item_id"]): row
                for row in self._vector_rows_by_id(
                    conn, [item_id for item_id, _ in pairs]
                )
            }
        scored = [
            self._vector_match(rows[item_id], score)
            for item_id, score in pairs
            if item_id in rows
        ]
        scored.sort(
            key=lambda item: (item["score"], item.get("updated_at") or ""), reverse=True
        )
        return {
            "query": query,
            "embedding_model": model_id,
            "embedding_dim": dim,
            "matches": scored[:limit],
            "recall": self._recall_report(
                backend=backend,
                cap=None,
                candidates_total=candidates_total,
                candidates_scanned=candidates_total,
                approx_detail=(
                    "approximate nearest-neighbour search: the whole index is "
                    "reachable but the true top-k is not guaranteed — compare "
                    "with scripts/bench_vector_index.py"
                ),
            ),
            "index": {**selection.as_dict(), "sidecar": index.loaded_from_sidecar},
        }

    def vector_search(
        self,
        query: str,
        *,
        limit: int = 30,
        min_score: float = 0.0,
        max_candidates: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Cosine search over the vector index (exact by default).

        ``max_candidates`` bounds how many indexed rows are scored; ``None``
        (the default) resolves it from ``LATTICEAI_VECTOR_MAX_CANDIDATES``
        (default 10 000), and ``0`` or a negative value scans the whole index.
        When the cap bites, the rows kept are the most recently indexed ones —
        recency, not similarity — so the result is *partial recall*. That is
        reported in the additive ``recall`` block
        (``{backend, max_candidates, candidates_total, candidates_scanned,
        truncated, detail}``) instead of being hidden, and callers/UIs are
        expected to surface ``recall.truncated``.

        v11.1.0: the scoring itself now lives in
        :mod:`lattice_brain.graph.vector_index`. ``LATTICEAI_VECTOR_INDEX``
        picks the backend — ``brute`` (default, exact, byte-compatible with
        every previous release), ``quantized`` (int8, exhaustive, approximate
        scores) or ``hnsw`` (approximate nearest neighbour, needs the optional
        ``hnsw`` extra). The resolved backend and any fallback reason ride
        along in the additive ``index`` block, whose ``approx`` flag is the
        one bit a caller needs to know whether "not found" is a fact or an
        estimate. The empty-query early return is deliberately unchanged: no
        query means no index was consulted, so there is nothing to report.
        """
        query = str(query or "").strip()
        limit = max(1, min(int(limit or 30), 100))
        min_score = float(min_score or 0.0)
        cap = self._vector_candidate_cap(max_candidates, limit=limit)
        backend = self._vector_search_backend()
        if not query:
            return {
                "query": query,
                "matches": [],
                "recall": {
                    "backend": backend,
                    "max_candidates": cap,
                    "candidates_total": 0,
                    "candidates_scanned": 0,
                    "truncated": False,
                    "detail": None,
                },
            }
        selection = self._vector_index_selection()
        query_vector = self._embedding_model.embed(query)
        if selection.name == HNSW_BACKEND:
            try:
                approximate = self._vector_search_ann(
                    query,
                    query_vector,
                    selection,
                    limit=limit,
                    min_score=min_score,
                    backend=backend,
                )
            except Exception as exc:  # noqa: BLE001 — a broken ANN must not lose the answer
                logging.warning("hnsw vector search failed: %s", exc)
                selection = dataclasses.replace(
                    resolve_vector_index(DEFAULT_VECTOR_INDEX),
                    requested=HNSW_BACKEND,
                    detail=f"hnsw search failed ({exc}); used the exact scan instead",
                )
                backend = selection.backend
            else:
                if approximate is not None:
                    return approximate
        return self._vector_search_scan(
            query,
            query_vector,
            selection,
            limit=limit,
            min_score=min_score,
            backend=backend,
            cap=cap,
        )
