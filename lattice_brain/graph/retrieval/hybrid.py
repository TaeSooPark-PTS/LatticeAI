"""The hybrid retrieval pipeline: keyword + vector + graph expansion.

One ranking algorithm, kept in one file on purpose — the standing reason
recorded in ``pyproject.toml`` for this file's complexity ignores is that
splitting ``hybrid_search`` across files would scatter the pipeline without
making it clearer. Moved verbatim out of ``retrieval.py`` (v11.3.0).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

# C901: `hybrid_search` is one ranking algorithm at complexity 50. The standing
# reason recorded for it in pyproject.toml is that splitting the pipeline across
# files would scatter it without making it clearer; the ignore rides with the
# file now that the file is the pipeline.
# ruff: noqa: C901,F403,F405
from .._kg_common import *  # noqa: F403,F401
from ..fusion import (
    DEFAULT_EXPANSION_CAP,
    DEFAULT_EXPANSION_SEEDS,
    expand_with_neighbors,
    graph_expansion_enabled,
    rrf_fuse,
)
from .signals import multimodal_signal

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged. The alias here
# reaches one step further: this half calls `self.search`, which the sibling
# half in .graph_view owns and the composed mixin puts on the same instance.
# Naming that sibling as the typing base states the assumption instead of
# re-declaring its signature where it could drift.
if TYPE_CHECKING:
    from .graph_view import _GraphViewMixin as _Core
else:
    _Core = object


class _HybridSearchMixin(_Core):
    """Fused keyword/vector/graph retrieval. Composed into the public mixin."""

    def hybrid_search(
        self,
        query: str,
        *,
        top_k: int = 20,
        alpha: Optional[float] = None,
        workspace_id: Optional[str] = None,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
        lexical_limit: Optional[int] = None,
        vector_limit: Optional[int] = None,
        min_vector_score: float = 0.0,
        image_vector: Optional[Sequence[float]] = None,
        image_fusion_weight: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Unified lexical + vector retrieval with alpha-weighted linear fusion.

        Runs the SQLite lexical :meth:`search` and the embedding-backed
        ``vector_search`` (sibling mixin via the store MRO), normalizes both
        score spaces to ``[0, 1]``, fuses them as
        ``alpha * vector + (1 - alpha) * lexical`` (the same shape as
        ``lattice_brain.quality.HybridFusion`` — reimplemented here without
        importing that module), and dedupes by ``node_id`` (chunk hits roll up
        to their parent node).

        Degrades gracefully: when the vector side is unavailable (mixin not
        composed, embedder/index failure) the result falls back to
        lexical-only ranking and reports ``mode: "lexical_only"`` with a
        ``detail`` explaining why. Each match carries per-source ``scores``
        and a ``fusion`` field (``lexical`` / ``vector`` / ``both``).

        ``workspace_id`` is a convenience for single-workspace callers; the
        richer ``allowed_workspaces`` set wins when both are provided.

        ``alpha=None`` (the default) resolves the vector share from the
        single retrieval policy (:mod:`lattice_brain.graph.retrieval_policy`,
        which wraps the query-class fusion table): fact 0.6 (the historical
        default) / code 0.35 / person 0.45 / recency 0.5, config-overridable
        via ``LATTICEAI_FUSION_WEIGHTS``. The policy also supplies a
        deterministic rule-based query rewrite (echoed additively under
        ``"policy"``; the response ``"query"`` stays the original) and, for
        the ``recency`` class only, an age-decay half-life that dampens each
        fused score into the ``[0.5, 1.0]`` band (``scores.age_decay``).
        Passing an explicit ``alpha`` pins it exactly as before and disables
        rewrite + decay.

        ``image_vector`` (v11.1.0) is the *late fusion* seam for the separate
        image space: the caller supplies a query vector from the same vision
        model that embedded the pictures, its own index is ranked
        independently, and only then are the two rankings blended
        (``image_fusion_weight``, default 0.5). A text query never produces
        one — it reaches images through their OCR text and captions — which is
        exactly why the image channel has to enter at the end rather than
        pretending to share the text index.
        """
        query = str(query or "").strip()
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 20
        top_k = max(1, min(top_k, 100))
        query_class: Optional[str] = None
        search_query = query
        rewrite_rules: List[str] = []
        recency_half_life_days: Optional[float] = None
        # "alpha" is the historical linear fusion; the policy may select RRF
        # per query class. An explicitly pinned ``alpha`` argument means the
        # caller is asking for linear fusion by name, so it stays linear.
        fusion_strategy = "alpha"
        if alpha is None:
            try:
                from ..retrieval_policy import resolve_policy

                policy = resolve_policy(query)
                query_class = policy["query_class"]
                alpha = float(policy["alpha"])
                fusion_strategy = str(policy.get("fusion_strategy") or "alpha")
                rewrite_rules = list(policy.get("rewrite_rules") or [])
                rewritten = str(policy.get("search_query") or "")
                if rewritten and rewritten != query:
                    search_query = rewritten
                half_life = policy.get("recency_half_life_days")
                if half_life is not None:
                    recency_half_life_days = float(half_life)
            except Exception:  # noqa: BLE001 — policy resolution must never break search
                alpha = 0.6
        try:
            alpha = float(alpha)
        except (TypeError, ValueError):
            alpha = 0.6
        alpha = max(0.0, min(alpha, 1.0))
        if allowed_workspaces is None and workspace_id:
            allowed_workspaces = {str(workspace_id)}

        if not query:
            return {
                "query": query,
                "mode": "hybrid",
                "alpha": alpha,
                "query_class": query_class,
                "top_k": top_k,
                "sources": {"lexical": 0, "vector": 0},
                "matches": [],
                "policy": {"search_query": search_query, "rewrite_rules": rewrite_rules},
                "fusion_strategy": fusion_strategy,
                "detail": None,
            }

        lex_fetch = max(1, min(int(lexical_limit or max(top_k * 2, 20)), 100))
        vec_fetch = max(1, min(int(vector_limit or max(top_k * 2, 20)), 100))

        lexical_matches = self.search(
            search_query,
            lex_fetch,
            allowed_workspaces=allowed_workspaces,
            include_legacy_global=include_legacy_global,
        ).get("matches", [])

        mode = "hybrid"
        detail: Optional[str] = None
        vector_matches: List[Dict[str, Any]] = []
        vector_recall: Optional[Dict[str, Any]] = None
        # The vector channel's own honesty block, echoed additively so a
        # caller can tell an exact "not found" from an approximate one.
        vector_meta: Dict[str, Any] = {
            "backend": None,
            "approx": None,
            "exhaustive": None,
            "truncated": None,
            "embedded_rows": None,
            "degraded": None,
        }
        vector_fn = getattr(self, "vector_search", None)
        if not callable(vector_fn):
            mode = "lexical_only"
            detail = "vector search is not available on this store"
        else:
            try:
                vector_payload = (
                    vector_fn(search_query, limit=vec_fetch, min_score=min_vector_score)
                    or {}
                )
                vector_matches = list(vector_payload.get("matches", []))
                # Partial recall must reach the caller: the vector channel can
                # only score a capped slice of a large index (see
                # retrieval_vector.vector_search), and a fused answer built on
                # a truncated scan is not the same claim as a complete one.
                recall = vector_payload.get("recall")
                if isinstance(recall, dict):
                    vector_meta["backend"] = recall.get("backend")
                    vector_meta["truncated"] = bool(recall.get("truncated"))
                    vector_meta["embedded_rows"] = recall.get("candidates_total")
                    if recall.get("truncated"):
                        vector_recall = dict(recall)
                index_block = vector_payload.get("index")
                if isinstance(index_block, dict):
                    vector_meta["approx"] = bool(index_block.get("approx"))
                    vector_meta["exhaustive"] = bool(index_block.get("exhaustive"))
            except Exception as exc:  # noqa: BLE001 — degrade, never fail the search
                mode = "lexical_only"
                detail = f"vector index unavailable: {exc}"
                vector_matches = []
        # An embedder swap makes the vector channel silently return zero rows
        # (vector_search filters on the CURRENT model/dim). Surface the honest
        # cause additively without changing the mode string.
        vector_degraded: Optional[str] = None
        if mode == "hybrid" and not vector_matches:
            try:
                fingerprint_fn = getattr(self, "embedder_fingerprint_status", None)
                if callable(fingerprint_fn) and fingerprint_fn().get("stale_embedder"):
                    vector_degraded = "stale_embedder"
            except Exception:  # noqa: BLE001 — fingerprint status must never break search
                vector_degraded = None
        if vector_matches and allowed_workspaces is not None:
            vector_matches = self.filter_scoped_nodes(
                vector_matches,
                allowed_workspaces,
                id_key="node_id",
                include_legacy_global=include_legacy_global,
            )

        def _parent_node_id(match: Dict[str, Any]) -> str:
            # Chunk-level hits dedupe to their parent content node.
            if match.get("type") == "Chunk":
                meta = match.get("metadata") or {}
                parent = meta.get("source_node") or meta.get("parent_source_node")
                if parent:
                    return str(parent)
            return str(match.get("node_id") or match.get("id") or "")

        entries: Dict[str, Dict[str, Any]] = {}

        def _entry_for(node_id: str, match: Dict[str, Any]) -> Dict[str, Any]:
            entry = entries.get(node_id)
            if entry is None:
                entry = {
                    "node_id": node_id,
                    "id": match.get("id") or node_id,
                    "type": match.get("type"),
                    "title": match.get("title"),
                    "summary": match.get("summary"),
                    "metadata": match.get("metadata") or {},
                    "updated_at": match.get("updated_at"),
                    "scores": {"lexical": 0.0, "vector": 0.0},
                    "_lexical": False,
                    "_vector": False,
                }
                entries[node_id] = entry
            return entry

        # Per-channel id order (best first) — the only input RRF needs, and
        # the one thing a normalized score cannot reconstruct.
        lexical_order: List[str] = []
        vector_order: List[str] = []

        for rank, match in enumerate(lexical_matches, start=1):
            node_id = _parent_node_id(match)
            if not node_id:
                continue
            entry = _entry_for(node_id, match)
            entry["scores"]["lexical"] = max(
                entry["scores"]["lexical"], round(1.0 / rank, 6)
            )
            entry["_lexical"] = True
            lexical_order.append(node_id)

        # Max-normalize cosine scores into [0, 1] (guard the score-0 falsy trap
        # by comparing explicitly, never with truthiness).
        max_vec = 0.0
        for match in vector_matches:
            raw = match.get("score")
            if raw is not None and float(raw) > max_vec:
                max_vec = float(raw)
        for match in vector_matches:
            node_id = _parent_node_id(match)
            if not node_id:
                continue
            raw = float(match.get("score") or 0.0)
            vec_norm = max(0.0, raw) / max_vec if max_vec > 0 else 0.0
            entry = _entry_for(node_id, match)
            entry["scores"]["vector"] = max(entry["scores"]["vector"], round(vec_norm, 6))
            entry["_vector"] = True
            vector_order.append(node_id)
            # Prefer a real snippet when the lexical row had no summary.
            if not entry.get("summary") and match.get("summary"):
                entry["summary"] = match.get("summary")

        # Graph traversal candidate expansion (opt-in, capped, counted): pull
        # the one-hop neighbours of the strongest hits into the candidate pool
        # so an answer that is adjacent to the match — not in it — is
        # reachable at all. Off by default; see fusion.GRAPH_EXPANSION_ENV.
        expansion_report: Dict[str, Any] = {
            "enabled": False,
            "seeds": 0,
            "added": 0,
            "cap": DEFAULT_EXPANSION_CAP,
            "truncated": False,
            "failed_seeds": 0,
        }
        if entries and graph_expansion_enabled():
            seeds = sorted(
                (
                    (node_id, float(entry["scores"]["vector"]))
                    for node_id, entry in entries.items()
                ),
                key=lambda pair: -pair[1],
            )[:DEFAULT_EXPANSION_SEEDS]
            expanded, expansion_report = expand_with_neighbors(
                seeds,
                self.neighbors,
                exclude=list(entries),
                cap=DEFAULT_EXPANSION_CAP,
            )
            for candidate in expanded:
                node = candidate["node"]
                entry = _entry_for(str(node.get("id")), dict(node))
                entry["scores"]["graph"] = candidate["score"]
                entry["metadata"] = {
                    **(entry.get("metadata") or {}),
                    "expanded_from": candidate["seed"],
                }
                entry["_graph"] = True

        rrf_normalized: Dict[str, float] = {}
        if fusion_strategy == "rrf":
            raw_rrf = rrf_fuse(
                {
                    "lexical": list(dict.fromkeys(lexical_order)),
                    "vector": list(dict.fromkeys(vector_order)),
                }
            )
            peak = max(raw_rrf.values(), default=0.0)
            if peak > 0:
                # Rescale to [0, 1] so the score column keeps the same meaning
                # across strategies; RRF's raw values live around 1/60.
                rrf_normalized = {key: value / peak for key, value in raw_rrf.items()}

        matches: List[Dict[str, Any]] = []
        for entry in entries.values():
            lex_score = float(entry["scores"]["lexical"])
            vec_score = float(entry["scores"]["vector"])
            if mode == "lexical_only":
                fused = lex_score
            elif fusion_strategy == "rrf":
                fused = float(rrf_normalized.get(entry["node_id"], 0.0))
                entry["scores"]["rrf"] = round(fused, 6)
            else:
                fused = alpha * vec_score + (1.0 - alpha) * lex_score
            from_lexical = bool(entry.pop("_lexical", False))
            from_vector = bool(entry.pop("_vector", False))
            if entry.pop("_graph", False):
                # A one-hop neighbour of a hit: related to the answer, never
                # itself a match, so it carries only its damped seed score.
                fused = float(entry["scores"]["graph"])
                entry["fusion"] = "graph"
            elif from_lexical and from_vector:
                entry["fusion"] = "both"
            elif from_vector:
                entry["fusion"] = "vector"
            else:
                entry["fusion"] = "lexical"
            entry["score"] = round(fused, 6)
            matches.append(entry)

        # Recency-class age decay (retrieval_policy): dampen each fused score
        # into the [0.5, 1.0] band so old-but-relevant items sink without ever
        # being zeroed. Other classes skip this block byte-identically.
        if recency_half_life_days is not None:
            decay_now = datetime.now()
            for match in matches:
                stamp = match.get("updated_at")
                if _parse_iso(stamp):
                    multiplier = 0.5 + 0.5 * _recency_score(
                        stamp, now=decay_now, half_life_days=recency_half_life_days
                    )
                else:
                    # Unknown age is not evidence of staleness — never dampen.
                    multiplier = 1.0
                match["scores"]["age_decay"] = round(multiplier, 6)
                match["score"] = round(float(match["score"]) * multiplier, 6)

        # Late fusion of the image space (v11.1.0). Runs after the text
        # channels have produced a ranking and before the cut, so image
        # evidence can lift a picture into the answer without ever having been
        # compared against a text vector.
        image_fusion: Optional[Dict[str, Any]] = None
        if image_vector is not None:
            image_fusion = self._fuse_image_channel(
                matches, image_vector, top_k=top_k, weight=image_fusion_weight
            )

        matches.sort(key=lambda item: (-item["score"], item["node_id"]))
        # Optional cross-encoder rerank (v9.9.5). Off by default; when the
        # env kill-switch is set and the model loads, pair scores reorder the
        # fused list. Failures degrade to identity and never break search.
        rerank_meta: Dict[str, Any]
        try:
            from ..rerank import rerank_matches

            # Rerank a slightly wider window, then cut to top_k.
            window = matches[: max(top_k * 2, top_k)]
            reranked = rerank_matches(search_query, window, top_k=top_k)
            matches = list(reranked.get("matches") or matches[:top_k])
            rerank_meta = {
                "mode": reranked.get("mode") or "identity",
                "model": reranked.get("model"),
                "detail": reranked.get("detail"),
            }
        except Exception as exc:  # noqa: BLE001 — rerank must never break search
            matches = matches[:top_k]
            rerank_meta = {"mode": "identity", "model": None, "detail": str(exc)}
        for rank, match in enumerate(matches, start=1):
            match["rank"] = rank
        result = {
            "query": query,
            "mode": mode,
            "alpha": alpha,
            "query_class": query_class,
            "top_k": top_k,
            "sources": {"lexical": len(lexical_matches), "vector": len(vector_matches)},
            "matches": matches,
            "policy": {"search_query": search_query, "rewrite_rules": rewrite_rules},
            "fusion_strategy": fusion_strategy,
            "graph_expansion": expansion_report,
            "rerank": rerank_meta,
            "detail": detail,
        }
        if vector_degraded is not None:
            result["vector_degraded"] = vector_degraded
        if vector_recall is not None:
            result["vector_recall"] = vector_recall
            if vector_degraded is None:
                result["vector_degraded"] = "partial_recall"
        vector_meta["degraded"] = result.get("vector_degraded")
        result["vector"] = vector_meta
        multimodal = multimodal_signal(matches)
        if multimodal is not None or image_fusion is not None:
            result["multimodal"] = {
                **(multimodal or {"images": 0, "types": []}),
                **({"image_fusion": image_fusion} if image_fusion is not None else {}),
            }
        return result

    def _fuse_image_channel(
        self,
        matches: List[Dict[str, Any]],
        image_vector: Sequence[float],
        *,
        top_k: int,
        weight: Optional[float],
    ) -> Dict[str, Any]:
        """Rank the image index separately, then blend it into ``matches``.

        Any failure degrades to "the image channel contributed nothing" with
        the reason attached — an image index that cannot be read is not a
        reason to lose the text answer.
        """
        from ..image_vectors import (
            DEFAULT_IMAGE_FUSION_WEIGHT,
            fuse_image_scores,
            image_similarity_search,
        )

        share = DEFAULT_IMAGE_FUSION_WEIGHT if weight is None else float(weight)
        report: Dict[str, Any] = {
            "weight": round(max(0.0, min(1.0, share)), 4),
            "candidates": 0,
            "fused": 0,
            "detail": None,
        }
        try:
            found = image_similarity_search(
                self, image_vector, top_k=max(1, int(top_k) * 2)
            )
        except Exception as exc:  # noqa: BLE001 — never fail the text answer
            report["detail"] = f"image index unavailable: {exc}"
            return report
        report["candidates"] = int(found.get("candidates") or 0)
        report["detail"] = found.get("detail")
        scores = {
            str(row.get("node_id")): float(row.get("score") or 0.0)
            for row in found.get("matches") or []
        }
        report["fused"] = fuse_image_scores(matches, scores, weight=share)
        return report
