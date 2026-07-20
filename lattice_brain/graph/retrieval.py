from __future__ import annotations

# ruff: noqa: F403,F405

from ._kg_common import *  # noqa: F403,F401


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

    def list_documents(self, limit: int = 200) -> Dict[str, Any]:
        """List ingested ``Document`` nodes with their ingest + index state.

        Powers the Files view: every accepted upload and every indexed local
        document becomes a ``Document`` node. A document is reported ``indexed``
        once its retrieval chunks exist (searchable in Chat / Hybrid Search).
        """
        limit = max(1, min(int(limit or 200), 1000))
        nt, _ = self._read_tables()
        documents: List[Dict[str, Any]] = []
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, title, summary, metadata_json, created_at, updated_at "
                f"FROM {nt} WHERE type='Document' ORDER BY updated_at DESC, id ASC LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                meta = _safe_loads(row["metadata_json"]) or {}
                extracted = meta.get("extracted") or {}
                node_id = row["id"]
                chunk_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM chunks WHERE source_node=?",
                    (node_id,),
                ).fetchone()["c"]
                if not chunk_count:
                    # Legacy projections represented chunks as graph nodes and
                    # linked them only through metadata_json. Keep read
                    # compatibility without making the fragile LIKE path the
                    # primary query.
                    chunk_count = conn.execute(
                        f"SELECT COUNT(*) AS c FROM {nt} WHERE type='Chunk' AND metadata_json LIKE ?",
                        (f"%{node_id}%",),
                    ).fetchone()["c"]
                documents.append(
                    {
                        "id": node_id,
                        "filename": meta.get("filename") or row["title"],
                        "ext": meta.get("ext"),
                        "mime_type": meta.get("mime_type"),
                        "bytes": meta.get("bytes"),
                        "sha256": meta.get("sha256"),
                        "uploader": meta.get("uploader"),
                        "chars": extracted.get("chars"),
                        "chunks": int(chunk_count or 0),
                        "indexed": int(chunk_count or 0) > 0,
                        "ingest_state": "indexed"
                        if int(chunk_count or 0) > 0
                        else "ingested",
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                )
        return {
            "documents": documents,
            "total": len(documents),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def workspaces_of(self, node_ids) -> Dict[str, Optional[str]]:
        """Map known node ids to their workspace scope.

        ``None`` is returned only for a row that is explicitly present in the
        authoritative v2 projection with a NULL workspace.  Missing ids remain
        missing, and projection/query failures propagate so callers can fail
        closed instead of mistaking every candidate for legacy-global data.
        """
        ids = [str(i) for i in node_ids if i]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            return {
                row["id"]: row["workspace_id"]
                for row in conn.execute(
                    f"SELECT id, workspace_id FROM nodes_v2 WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
            }

    def filter_scoped_nodes(
        self,
        items,
        allowed_workspaces,
        *,
        id_key: str = "id",
        include_legacy_global: bool = False,
    ):
        """Drop items scoped to a workspace the caller is not a member of.

        ``allowed_workspaces=None`` means no scoping (single-user / no-auth
        mode). In scoped/multi-user mode, unknown ids are private and
        legacy-global rows require the explicit ``include_legacy_global=True``
        compatibility opt-in.
        """
        candidates = list(items)
        if allowed_workspaces is None:
            return candidates
        allowed = {str(workspace_id) for workspace_id in allowed_workspaces if workspace_id}
        scopes = self.workspaces_of([item.get(id_key) for item in candidates])
        visible = []
        for item in candidates:
            node_id = str(item.get(id_key) or "")
            if not node_id or node_id not in scopes:
                # Unknown/unprojected rows are never treated as public.
                continue
            workspace_id = scopes[node_id]
            if workspace_id is None:
                if include_legacy_global:
                    visible.append(item)
            elif str(workspace_id) in allowed:
                visible.append(item)
        return visible

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
        alpha: float = 0.6,
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
        """
        query = str(query or "").strip()
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 20
        top_k = max(1, min(top_k, 100))
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
                "top_k": top_k,
                "sources": {"lexical": 0, "vector": 0},
                "matches": [],
                "detail": None,
            }

        lex_fetch = max(1, min(int(lexical_limit or max(top_k * 2, 20)), 100))
        vec_fetch = max(1, min(int(vector_limit or max(top_k * 2, 20)), 100))

        lexical_matches = self.search(
            query,
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
                    (vector_fn(query, limit=vec_fetch, min_score=min_vector_score) or {}).get(
                        "matches", []
                    )
                )
            except Exception as exc:  # noqa: BLE001 — degrade, never fail the search
                mode = "lexical_only"
                detail = f"vector index unavailable: {exc}"
                vector_matches = []
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

        matches.sort(key=lambda item: (-item["score"], item["node_id"]))
        matches = matches[:top_k]
        for rank, match in enumerate(matches, start=1):
            match["rank"] = rank
        return {
            "query": query,
            "mode": mode,
            "alpha": alpha,
            "top_k": top_k,
            "sources": {"lexical": len(lexical_matches), "vector": len(vector_matches)},
            "matches": matches,
            "detail": detail,
        }

    def context_for_query(
        self,
        query: str,
        limit: int = 6,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
        use_hybrid: bool = False,
    ) -> str:
        """Return compact graph-backed RAG context for chat generation.

        ``use_hybrid=True`` sources the matches from :meth:`hybrid_search`
        (lexical + vector fusion) instead of the lexical-only :meth:`search`.
        Default behavior is unchanged, and any hybrid failure silently falls
        back to the legacy lexical path.
        """
        query = str(query or "").strip()
        if not query:
            return ""
        matches: List[Dict[str, Any]] = []
        if use_hybrid:
            try:
                matches = self.hybrid_search(
                    query,
                    top_k=limit,
                    allowed_workspaces=allowed_workspaces,
                    include_legacy_global=include_legacy_global,
                ).get("matches", [])
            except Exception:  # noqa: BLE001 — context building must never fail
                matches = []
        if not matches:
            matches = self.search(
                query,
                limit,
                allowed_workspaces=allowed_workspaces,
                include_legacy_global=include_legacy_global,
            ).get("matches", [])
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
        return "\n".join(lines)

    def neighbors(
        self,
        node_id: str,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        """Return direct neighbors (1-hop) of a node."""
        if allowed_workspaces is not None and not self.filter_scoped_nodes(
            [{"id": node_id}],
            allowed_workspaces,
            include_legacy_global=include_legacy_global,
        ):
            raise ValueError(f"graph node not found: {node_id}")
        nt, et = self._read_tables()
        with self._connect() as conn:
            edge_rows = conn.execute(
                f"SELECT from_node, to_node, type, weight FROM {et} WHERE from_node=? OR to_node=? ORDER BY id ASC",
                (node_id, node_id),
            ).fetchall()
            neighbor_ids: set = set()
            edges = []
            for row in edge_rows:
                neighbor_ids.add(row["from_node"])
                neighbor_ids.add(row["to_node"])
                edges.append(
                    {
                        "from": row["from_node"],
                        "to": row["to_node"],
                        "type": row["type"],
                        "weight": row["weight"],
                    }
                )
            neighbor_ids.discard(node_id)
            nodes = []
            if neighbor_ids:
                placeholders = ",".join("?" * len(neighbor_ids))
                nodes = [
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "title": row["title"],
                        "summary": row["summary"],
                        "metadata": _safe_loads(row["metadata_json"]),
                    }
                    for row in conn.execute(
                        f"SELECT id, type, title, summary, metadata_json FROM {nt} WHERE id IN ({placeholders}) ORDER BY id ASC",
                        list(neighbor_ids),
                    )
                ]
        if allowed_workspaces is not None:
            nodes = self.filter_scoped_nodes(
                nodes,
                allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
            kept = {node.get("id") for node in nodes}
            edges = [
                edge for edge in edges
                if (edge.get("from") == node_id or edge.get("from") in kept)
                and (edge.get("to") == node_id or edge.get("to") in kept)
            ]
        return {"node_id": node_id, "neighbors": nodes, "edges": edges}

    def get_node(
        self,
        node_id: str,
        *,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        node_id = str(node_id or "").strip()
        if not node_id:
            raise ValueError("node_id required")
        nt, et = self._read_tables()
        with self._connect() as conn:
            row = conn.execute(
                f"""
                    SELECT id, type, title, summary, metadata_json, updated_at
                    FROM {nt}
                    WHERE id=?
                    """,
                (node_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"graph node not found: {node_id}")
            degree = conn.execute(
                f"SELECT COUNT(*) AS c FROM {et} WHERE from_node=? OR to_node=?",
                (node_id, node_id),
            ).fetchone()["c"]
        node = {
            "id": row["id"],
            "type": row["type"],
            "title": row["title"],
            "summary": row["summary"],
            "metadata": _safe_loads(row["metadata_json"]),
            "updated_at": row["updated_at"],
            "degree": degree,
        }
        if allowed_workspaces is not None and not self.filter_scoped_nodes(
            [node],
            allowed_workspaces,
            include_legacy_global=include_legacy_global,
        ):
            raise ValueError(f"graph node not found: {node_id}")
        return node

    def relationship_search(
        self,
        *,
        query: str = "",
        node_id: str = "",
        relationship_type: str = "",
        limit: int = 30,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        query = str(query or "").strip()
        node_id = str(node_id or "").strip()
        relationship_type = str(relationship_type or "").strip()
        limit = max(1, min(int(limit or 30), 200))
        nt, et = self._read_tables()
        where = []
        params: List[Any] = []
        if node_id:
            where.append("(e.from_node=? OR e.to_node=?)")
            params.extend([node_id, node_id])
        if relationship_type:
            where.append("e.type LIKE ?")
            params.append(f"%{relationship_type}%")
        if query:
            where.append(
                "(e.type LIKE ? OR e.metadata_json LIKE ? OR src.title LIKE ? OR dst.title LIKE ? OR src.summary LIKE ? OR dst.summary LIKE ?)"
            )
            params.extend([f"%{query}%"] * 6)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                    SELECT
                      e.id, e.from_node, e.to_node, e.type, e.weight, e.metadata_json, e.created_at,
                      src.type AS source_type, src.title AS source_title, src.summary AS source_summary,
                      src.metadata_json AS source_metadata,
                      dst.type AS target_type, dst.title AS target_title, dst.summary AS target_summary,
                      dst.metadata_json AS target_metadata
                    FROM {et} e
                    JOIN {nt} src ON src.id=e.from_node
                    JOIN {nt} dst ON dst.id=e.to_node
                    {where_sql}
                    ORDER BY e.weight DESC, e.created_at DESC, e.id ASC
                    LIMIT ?
                    """,
                (*params, limit),
            ).fetchall()
        relationships = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "weight": row["weight"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "created_at": row["created_at"],
                    "source": {
                        "id": row["from_node"],
                        "type": row["source_type"],
                        "title": row["source_title"],
                        "summary": row["source_summary"],
                        "metadata": _safe_loads(row["source_metadata"]),
                    },
                    "target": {
                        "id": row["to_node"],
                        "type": row["target_type"],
                        "title": row["target_title"],
                        "summary": row["target_summary"],
                        "metadata": _safe_loads(row["target_metadata"]),
                    },
                }
                for row in rows
            ]
        if allowed_workspaces is not None:
            kept = []
            for rel in relationships:
                endpoints = [
                    {"id": (rel.get("source") or {}).get("id")},
                    {"id": (rel.get("target") or {}).get("id")},
                ]
                if len(
                    self.filter_scoped_nodes(
                        endpoints,
                        allowed_workspaces,
                        include_legacy_global=include_legacy_global,
                    )
                ) == 2:
                    kept.append(rel)
            relationships = kept
        return {
            "query": query,
            "node_id": node_id,
            "relationship_type": relationship_type,
            "relationships": relationships,
        }

    def traverse(
        self,
        node_id: str,
        *,
        depth: int = 1,
        limit: int = 100,
        allowed_workspaces=None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        node_id = str(node_id or "").strip()
        if not node_id:
            raise ValueError("node_id required")
        if allowed_workspaces is not None and not self.filter_scoped_nodes(
            [{"id": node_id}],
            allowed_workspaces,
            include_legacy_global=include_legacy_global,
        ):
            raise ValueError(f"graph node not found: {node_id}")
        depth = max(0, min(int(depth or 1), 4))
        limit = max(1, min(int(limit or 100), 500))
        nt, et = self._read_tables()
        visited = {node_id}
        frontier = {node_id}
        edges_by_id: Dict[str, Dict[str, Any]] = {}
        with self._connect() as conn:
            for _ in range(depth):
                if not frontier or len(visited) >= limit:
                    break
                placeholders = ",".join("?" * len(frontier))
                rows = conn.execute(
                    f"""
                        SELECT id, from_node, to_node, type, weight, metadata_json
                        FROM {et}
                        WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})
                        ORDER BY weight DESC, id ASC
                        LIMIT ?
                        """,
                    (*frontier, *frontier, limit * 3),
                ).fetchall()
                next_frontier = set()
                for row in rows:
                    edges_by_id[row["id"]] = {
                        "id": row["id"],
                        "from": row["from_node"],
                        "to": row["to_node"],
                        "type": row["type"],
                        "weight": row["weight"],
                        "metadata": _safe_loads(row["metadata_json"]),
                    }
                    for candidate in (row["from_node"], row["to_node"]):
                        if candidate not in visited and len(visited) < limit:
                            visited.add(candidate)
                            next_frontier.add(candidate)
                frontier = next_frontier
            placeholders = ",".join("?" * len(visited))
            node_rows = conn.execute(
                f"""
                    SELECT id, type, title, summary, metadata_json, updated_at
                    FROM {nt}
                    WHERE id IN ({placeholders})
                    ORDER BY updated_at DESC, id ASC
                    """,
                list(visited),
            ).fetchall()
        nodes = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in node_rows
            ]
        edges = list(edges_by_id.values())
        if allowed_workspaces is not None:
            nodes = self.filter_scoped_nodes(
                nodes,
                allowed_workspaces,
                include_legacy_global=include_legacy_global,
            )
            kept = {node.get("id") for node in nodes}
            edges = [edge for edge in edges if edge.get("from") in kept and edge.get("to") in kept]
        return {"root": node_id, "depth": depth, "nodes": nodes, "edges": edges}

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

    def stats(self) -> Dict[str, Any]:
        nt, et = self._read_tables()
        with self._connect() as conn:
            node_counts = {
                row["type"]: row["count"]
                for row in conn.execute(
                    f"SELECT type, COUNT(*) AS count FROM {nt} GROUP BY type"
                )
            }
            edge_counts = {
                row["type"]: row["count"]
                for row in conn.execute(
                    f"SELECT type, COUNT(*) AS count FROM {et} GROUP BY type"
                )
            }
            local_sources = conn.execute(
                "SELECT COUNT(*) AS c FROM knowledge_sources"
            ).fetchone()["c"]
            local_file_status = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS count FROM local_file_index GROUP BY status"
                )
            }
        v2 = None
        if KGStoreV2 is not None:
            try:
                v2 = KGStoreV2(self.db_path).stats()
            except Exception as e:
                v2 = {"available": False, "error": str(e)}
        return {
            "db_path": str(self.db_path),
            "schema_version": GRAPH_SCHEMA_VERSION,
            "v2_schema_available": KGStoreV2 is not None,
            "nodes": node_counts,
            "edges": edge_counts,
            "local_sources": local_sources,
            "local_file_status": local_file_status,
            "v2": v2,
        }
