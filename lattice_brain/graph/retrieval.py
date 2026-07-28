from __future__ import annotations

# ruff: noqa: F403,F405
from ._kg_common import *  # noqa: F403,F401

# --- Compat seam (v9.9.5 decomposition) -------------------------------------
# The non-search read surface (list_documents / workspaces_of /
# filter_scoped_nodes / neighbors / get_node / relationship_search /
# traverse / stats) moved byte-identically to .retrieval_reads as
# KnowledgeGraphReadsMixin. Re-exported here so any legacy
# ``from lattice_brain.graph.retrieval import ...`` site keeps resolving.
from .retrieval_reads import KnowledgeGraphReadsMixin  # noqa: F401


def context_quality_signal(
    mode: str,
    nodes: int,
    *,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Honest RAG context-quality signal (v9.8.0, additive contract).

    Shape consumed by the chat metadata channel:
    ``{"mode": "hybrid"|"lexical_only"|"none", "nodes": int, "limited": bool,
    "reason": str|None}``. ``nodes == 0`` always collapses ``mode`` to
    ``"none"``; ``limited`` is true whenever the context is thin (0–1 nodes)
    or the vector side fell back to lexical-only retrieval. ``reason`` is a
    short human-readable Korean phrase, only present when limited.
    """
    nodes = max(0, int(nodes or 0))
    mode = str(mode or "none")
    if nodes == 0:
        mode = "none"
    if mode not in ("hybrid", "lexical_only", "none"):
        mode = "lexical_only"
    limited = nodes <= 1 or mode != "hybrid"
    if reason is None and limited:
        if nodes == 0:
            reason = "그래프에서 관련 지식을 찾지 못했습니다"
        elif mode == "lexical_only":
            reason = "벡터 검색을 사용할 수 없어 키워드 검색 결과만 사용했습니다"
        else:
            reason = "그래프 기반 컨텍스트가 제한적입니다"
    if not limited:
        reason = None
    return {"mode": mode, "nodes": nodes, "limited": limited, "reason": reason}


class KnowledgeGraphRetrievalMixin:
    _GRAPH_VISIBLE_TYPES = (
        "Computer",  # 내 컴퓨터
        "Drive",  # 드라이브 / 볼륨
        "Folder",  # 폴더
        "File",  # 일반 파일
        "Chat",  # 대화 세션
        "Document",  # 파일 (PDF·PPT·Word·Excel·이미지)
        "CodeFile",  # 코드 파일
        "Spreadsheet",  # 엑셀/CSV
        "SlideDeck",  # 프레젠테이션
        "Image",  # 이미지
        "ImageText",  # OCR 텍스트
        "Concept",  # 개념 / 아이디어 / 기술 용어
        "Person",  # 사람
        "Error",  # 오류 / 버그
        "Code",  # 코드 / 함수
        "Feature",  # 소프트웨어 기능
        "Task",  # 할 일
        "Decision",  # 결정 사항
        # v3.6.0 Knowledge Graph First — 1급 엔티티를 그래프에 노출
        "Source",  # 수집 출처 (파일/URL/브라우저 탭/git)
        "Repository",  # git 저장소
        "Meeting",  # 회의
        "Organization",  # 조직
        "Workflow",  # 워크플로우
        "Agent",  # 에이전트
    )

    def graph(
        self,
        limit: int = 300,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 300), 2000))
        visible = ",".join(f"'{t}'" for t in self._GRAPH_VISIBLE_TYPES)
        nt, et = self._read_tables()
        with self._connect() as conn:
            nodes = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in conn.execute(
                    f"SELECT id, type, title, summary, metadata_json, updated_at FROM {nt} WHERE type IN ({visible}) ORDER BY updated_at DESC, id ASC LIMIT ?",
                    (limit,),
                )
            ]
            node_ids = {node["id"] for node in nodes}
            edges: List[Dict[str, Any]] = []
            if node_ids:
                edge_rows = conn.execute(
                    f"""
                        SELECT id, from_node, to_node, type, weight, metadata_json
                        FROM {et}
                        WHERE from_node IN (
                            SELECT id FROM {nt} WHERE type IN ({visible})
                            ORDER BY updated_at DESC, id ASC LIMIT ?
                        )
                        AND to_node IN (
                            SELECT id FROM {nt} WHERE type IN ({visible})
                            ORDER BY updated_at DESC, id ASC LIMIT ?
                        )
                        ORDER BY weight DESC, created_at DESC, id ASC
                        """,
                    (limit, limit),
                ).fetchall()
                edges = [
                    {
                        "id": row["id"],
                        "from": row["from_node"],
                        "to": row["to_node"],
                        "type": row["type"],
                        "weight": row["weight"],
                        "metadata": _safe_loads(row["metadata_json"]),
                    }
                    for row in edge_rows
                ]

        if allowed_workspaces is not None:
            nodes = self.filter_scoped_nodes(
                nodes,
                allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
            kept_ids = {node["id"] for node in nodes}
            edges = [e for e in edges if e["from"] in kept_ids and e["to"] in kept_ids]

        degree_map: Dict[str, int] = {}
        now = datetime.now()
        node_by_id = {node["id"]: node for node in nodes}
        topic_metrics: Dict[str, Dict[str, Any]] = {}

        for edge in edges:
            degree_map[edge["from"]] = degree_map.get(edge["from"], 0) + 1
            degree_map[edge["to"]] = degree_map.get(edge["to"], 0) + 1
            from_node = node_by_id.get(edge["from"])
            to_node = node_by_id.get(edge["to"])
            if not from_node or not to_node:
                continue
            for topic_node, other_node in ((from_node, to_node), (to_node, from_node)):
                if topic_node["type"] != "Topic":
                    continue
                metrics = topic_metrics.setdefault(
                    topic_node["id"],
                    {
                        "mention_count": 0.0,
                        "conversation_ids": set(),
                    },
                )
                if edge["type"] in {"mentions", "discusses"}:
                    metrics["mention_count"] += max(
                        0.5, float(edge.get("weight") or 1.0)
                    )
                other_meta = other_node.get("metadata") or {}
                conversation_id = other_meta.get("conversation_id")
                if other_node["type"] == "Conversation":
                    conversation_id = other_node["id"]
                if conversation_id:
                    metrics["conversation_ids"].add(str(conversation_id))

        type_max_raw: Dict[str, float] = {}
        for node in nodes:
            degree = degree_map.get(node["id"], 0)
            recency = _recency_score(node.get("updated_at"), now=now)
            metrics = {
                "degree": degree,
                "recency_score": round(recency, 4),
            }
            if node["type"] == "Topic":
                topic_stat = topic_metrics.get(node["id"], {})
                mention_count = float(topic_stat.get("mention_count") or 0.0)
                conversation_count = len(topic_stat.get("conversation_ids") or ())
                raw_importance = (
                    math.log1p(mention_count) * 2.8
                    + math.log1p(conversation_count) * 2.2
                    + recency * 1.4
                    + math.sqrt(max(0, degree)) * 0.45
                )
                metrics.update(
                    {
                        "mention_count": round(mention_count, 2),
                        "conversation_count": conversation_count,
                    }
                )
            else:
                raw_importance = math.log1p(max(0, degree)) * 1.4 + recency * 0.9

            metrics["importance_raw"] = round(raw_importance, 4)
            node["importance"] = round(raw_importance, 4)
            node["_raw_importance"] = raw_importance
            node["metadata"] = {
                **(node.get("metadata") or {}),
                "graph_metrics": metrics,
            }
            type_max_raw[node["type"]] = max(
                type_max_raw.get(node["type"], 0.0), raw_importance
            )

        for node in nodes:
            max_raw = max(type_max_raw.get(node["type"], 0.0), 0.0001)
            importance_norm = min(1.0, (node.get("_raw_importance") or 0.0) / max_raw)
            node["importance_norm"] = round(importance_norm, 4)
            node["metadata"]["graph_metrics"]["importance_norm"] = node[
                "importance_norm"
            ]
            node.pop("_raw_importance", None)
        return {"nodes": nodes, "edges": edges}

    def search(
        self,
        query: str,
        limit: int = 30,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        query = str(query or "").strip()
        q = f"%{query}%"
        limit = max(1, min(int(limit or 30), 100))
        nt, et = self._read_tables()
        with self._connect() as conn:
            rows = []
            if query:
                fts_ids = self._fts_match_ids(conn, query, limit)
                if fts_ids:
                    placeholders = ",".join("?" for _ in fts_ids)
                    by_id = {
                        row["id"]: row
                        for row in conn.execute(
                            f"""
                                SELECT id, type, title, summary, metadata_json, updated_at
                                FROM {nt} WHERE id IN ({placeholders})
                                """,
                            fts_ids,
                        ).fetchall()
                    }
                    # Preserve FTS bm25 rank order.
                    rows = [by_id[i] for i in fts_ids if i in by_id]
                else:
                    rows = conn.execute(
                        f"""
                            SELECT id, type, title, summary, metadata_json, updated_at
                            FROM {nt}
                            WHERE title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?
                            ORDER BY updated_at DESC, id ASC
                            LIMIT ?
                            """,
                        (q, q, q, limit),
                    ).fetchall()

            if len(rows) < limit:
                terms = _topic_candidates(query, limit=8)
                if terms:
                    clauses = []
                    params: List[str] = []
                    for term in terms:
                        clauses.append(
                            "(title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?)"
                        )
                        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
                    extra = conn.execute(
                        f"""
                            SELECT id, type, title, summary, metadata_json, updated_at
                            FROM {nt}
                            WHERE {" OR ".join(clauses)}
                            ORDER BY updated_at DESC, id ASC
                            LIMIT ?
                            """,
                        (*params, limit * 3),
                    ).fetchall()
                    by_id = {row["id"]: row for row in rows}
                    for row in extra:
                        by_id.setdefault(row["id"], row)
                    rows = list(by_id.values())

            terms_for_score = set(_topic_candidates(query, limit=12))

            def score(row: sqlite3.Row) -> tuple:
                haystack = (
                    f"{row['title']} {row['summary']} {row['metadata_json']}".lower()
                )
                hits = sum(1 for term in terms_for_score if term.lower() in haystack)
                type_boost = (
                    1
                    if row["type"]
                    in {
                        "Decision",
                        "Task",
                        "File",
                        "Document",
                        "CodeFile",
                        "Spreadsheet",
                        "SlideDeck",
                        "Image",
                        "ImageText",
                        "Page",
                        "Slide",
                    }
                    else 0
                )
                return (hits, type_boost, row["updated_at"] or "")

            # Deterministic contract: rows with equal relevance order by id ASC
            # (stable sort preserves the pre-sort under reverse=True), matching
            # the legacy LIKE path regardless of FTS bm25 tie ordering.
            rows = sorted(rows, key=lambda r: r["id"])
            rows = sorted(rows, key=score, reverse=True)[:limit]
        matches = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]
        if allowed_workspaces is not None:
            matches = self.filter_scoped_nodes(
                matches,
                allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
        return {"query": query, "matches": matches}

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
        if alpha is None:
            try:
                from .retrieval_policy import resolve_policy

                policy = resolve_policy(query)
                query_class = policy["query_class"]
                alpha = float(policy["alpha"])
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
        vector_fn = getattr(self, "vector_search", None)
        if not callable(vector_fn):
            mode = "lexical_only"
            detail = "vector search is not available on this store"
        else:
            try:
                vector_matches = list(
                    (vector_fn(search_query, limit=vec_fetch, min_score=min_vector_score) or {}).get(
                        "matches", []
                    )
                )
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

        for rank, match in enumerate(lexical_matches, start=1):
            node_id = _parent_node_id(match)
            if not node_id:
                continue
            entry = _entry_for(node_id, match)
            entry["scores"]["lexical"] = max(
                entry["scores"]["lexical"], round(1.0 / rank, 6)
            )
            entry["_lexical"] = True

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
            # Prefer a real snippet when the lexical row had no summary.
            if not entry.get("summary") and match.get("summary"):
                entry["summary"] = match.get("summary")

        matches: List[Dict[str, Any]] = []
        for entry in entries.values():
            lex_score = float(entry["scores"]["lexical"])
            vec_score = float(entry["scores"]["vector"])
            if mode == "lexical_only":
                fused = lex_score
            else:
                fused = alpha * vec_score + (1.0 - alpha) * lex_score
            entry["score"] = round(fused, 6)
            from_lexical = bool(entry.pop("_lexical", False))
            from_vector = bool(entry.pop("_vector", False))
            if from_lexical and from_vector:
                entry["fusion"] = "both"
            elif from_vector:
                entry["fusion"] = "vector"
            else:
                entry["fusion"] = "lexical"
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

        matches.sort(key=lambda item: (-item["score"], item["node_id"]))
        # Optional cross-encoder rerank (v9.9.5). Off by default; when the
        # env kill-switch is set and the model loads, pair scores reorder the
        # fused list. Failures degrade to identity and never break search.
        rerank_meta: Dict[str, Any]
        try:
            from .rerank import rerank_matches

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
            "rerank": rerank_meta,
            "detail": detail,
        }
        if vector_degraded is not None:
            result["vector_degraded"] = vector_degraded
        return result

    def context_for_query(
        self,
        query: str,
        limit: int = 6,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
        use_hybrid: bool = False,
        with_meta: bool = False,
    ):
        """Return compact graph-backed RAG context for chat generation.

        ``use_hybrid=True`` sources the matches from :meth:`hybrid_search`
        (lexical + vector fusion) instead of the lexical-only :meth:`search`.
        Default behavior is unchanged, and any hybrid failure silently falls
        back to the legacy lexical path.

        ``with_meta=True`` (additive, v9.8.0) returns
        ``{"context": str, "quality": {...}}`` instead of the bare string.
        ``quality`` follows :func:`context_quality_signal` and honestly
        reports how the context was retrieved (hybrid vs lexical-only
        fallback vs nothing). The ``context`` value is byte-identical to the
        default ``with_meta=False`` return for the same arguments.
        """
        query = str(query or "").strip()
        if not query:
            if with_meta:
                return {
                    "context": "",
                    "quality": context_quality_signal(
                        "none", 0, reason="질의가 비어 있습니다"
                    ),
                }
            return ""
        matches: List[Dict[str, Any]] = []
        retrieval_mode = "none"
        if use_hybrid:
            try:
                hybrid = self.hybrid_search(
                    query,
                    top_k=limit,
                    allowed_workspaces=allowed_workspaces,
                    include_legacy_global=include_legacy_global,
                )
                matches = hybrid.get("matches", [])
                if matches:
                    retrieval_mode = str(hybrid.get("mode") or "hybrid")
            except Exception:  # noqa: BLE001 — context building must never fail
                matches = []
        if not matches:
            matches = self.search(
                query,
                limit,
                allowed_workspaces=allowed_workspaces,
                include_legacy_global=include_legacy_global,
            ).get("matches", [])
            if matches:
                retrieval_mode = "lexical_only"
        if not matches:
            topics = _topic_candidates(query, limit=4)
            if topics:
                nt, et = self._read_tables()
                with self._connect() as conn:
                    rows = []
                    for topic in topics:
                        rows.extend(
                            conn.execute(
                                f"""
                                SELECT id, type, title, summary, metadata_json
                                FROM {nt}
                                WHERE title LIKE ? OR metadata_json LIKE ?
                                ORDER BY updated_at DESC, id ASC
                                LIMIT 3
                                """,
                                (f"%{topic}%", f"%{topic}%"),
                            ).fetchall()
                        )
                seen = set()
                matches = []
                for row in rows:
                    if row["id"] in seen:
                        continue
                    seen.add(row["id"])
                    matches.append(
                        {
                            "id": row["id"],
                            "type": row["type"],
                            "title": row["title"],
                            "summary": row["summary"],
                            "metadata": _safe_loads(row["metadata_json"]),
                        }
                    )
                    if len(matches) >= limit:
                        break
                if allowed_workspaces is not None:
                    matches = self.filter_scoped_nodes(
                        matches,
                        allowed_workspaces,
                        include_legacy_global=include_legacy_global,
                    )
                if matches:
                    retrieval_mode = "lexical_only"
        lines = []
        for match in matches[:limit]:
            meta = match.get("metadata") or {}
            source = (
                meta.get("relative_path")
                or meta.get("filename")
                or meta.get("conversation_id")
                or meta.get("source")
                or match["id"]
            )
            summary = _clean_text(match.get("summary") or "")[:700]
            lines.append(
                f"- [{match['type']}] {match['title']} | source={source} | {summary}"
            )
        context = "\n".join(lines)
        if not with_meta:
            return context
        return {
            "context": context,
            "quality": context_quality_signal(retrieval_mode, len(matches[:limit])),
        }

    def context_for_query_with_meta(
        self,
        query: str,
        limit: int = 6,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
        use_hybrid: bool = True,
    ) -> Dict[str, Any]:
        """Additive companion to :meth:`context_for_query` (v9.8.0).

        Always returns ``{"context": str, "quality": {...}}`` so chat callers
        can surface an honest retrieval signal without changing the legacy
        string-returning contract. Defaults to hybrid retrieval because meta
        consumers want the vector-fallback signal; pass ``use_hybrid=False``
        for the legacy lexical-only sourcing.
        """
        return self.context_for_query(
            query,
            limit,
            allowed_workspaces=allowed_workspaces,
            include_legacy_global=include_legacy_global,
            use_hybrid=use_hybrid,
            with_meta=True,
        )

    def delete_conversation(self, conversation_id: str) -> Dict[str, Any]:
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            return {"status": "skipped", "removed_nodes": 0}
        conv_id = f"conversation:{_slug(conversation_id)}"
        with self._connect() as conn:
            # Edge rows may carry the legacy lowercase label (pre-v4) or the
            # canonical EdgeType value (v4 write door) — match both.
            direct_ids = [
                row["to_node"]
                for row in conn.execute(
                    "SELECT to_node FROM edges WHERE from_node=? AND type IN ('contains', 'CONTAINS')",
                    (conv_id,),
                )
            ]
            remove_ids = set(direct_ids)
            child_types = [
                "has_chunk",
                "implies",
                "contains_signal",
                "has_page",
                "has_slide",
                "has_sheet",
                "contains_image",
            ]
            child_types += [t.upper() for t in child_types]
            placeholders = ",".join("?" for _ in child_types)
            for source_id in list(direct_ids):
                for row in conn.execute(
                    f"SELECT to_node FROM edges WHERE from_node=? AND type IN ({placeholders})",
                    (source_id, *child_types),
                ):
                    remove_ids.add(row["to_node"])
            remove_ids.add(conv_id)
            for node_id in remove_ids:
                conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
                if KGStoreV2 is not None:
                    conn.execute(
                        "DELETE FROM nodes_v2 WHERE id=?", (node_id,)
                    )  # edges_v2 cascade
            conn.execute(
                """
                    DELETE FROM nodes
                    WHERE type='Topic'
                      AND id NOT IN (SELECT to_node FROM edges)
                      AND id NOT IN (SELECT from_node FROM edges)
                    """
            )
            if KGStoreV2 is not None:
                conn.execute(
                    """
                        DELETE FROM nodes_v2
                        WHERE legacy_type='Topic'
                          AND id NOT IN (SELECT target FROM edges_v2)
                          AND id NOT IN (SELECT source FROM edges_v2)
                        """
                )
        return {
            "status": "ok",
            "conversation_id": conversation_id,
            "removed_nodes": len(remove_ids),
        }

    def clear_all(self) -> Dict[str, Any]:
        with self._connect() as conn:
            counts = {
                "nodes": conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()[
                    "c"
                ],
                "edges": conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()[
                    "c"
                ],
                "chunks": conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()[
                    "c"
                ],
                "knowledge_sources": conn.execute(
                    "SELECT COUNT(*) AS c FROM knowledge_sources"
                ).fetchone()["c"],
                "local_file_index": conn.execute(
                    "SELECT COUNT(*) AS c FROM local_file_index"
                ).fetchone()["c"],
            }
            conn.execute("DELETE FROM local_file_index")
            conn.execute("DELETE FROM knowledge_sources")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM nodes")
            if KGStoreV2 is not None:
                conn.execute("DELETE FROM edges_v2")
                conn.execute("DELETE FROM nodes_v2")
        if self.blob_dir.exists():
            shutil.rmtree(self.blob_dir, ignore_errors=True)
            self.blob_dir.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "removed": counts}
