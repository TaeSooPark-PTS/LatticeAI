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
        """Map node ids to their workspace scope (None = legacy-global)."""
        ids = [str(i) for i in node_ids if i]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            try:
                return {
                    row["id"]: row["workspace_id"]
                    for row in conn.execute(
                        f"SELECT id, workspace_id FROM nodes_v2 WHERE id IN ({placeholders})",
                        ids,
                    ).fetchall()
                }
            except Exception:
                return {}

    def filter_scoped_nodes(self, items, allowed_workspaces, *, id_key: str = "id"):
        """Drop items scoped to a workspace the caller is not a member of.

        ``allowed_workspaces=None`` means no scoping (single-user / no-auth
        mode). Legacy-global rows (no workspace) stay visible to everyone on
        the machine — the documented pre-v4 compatibility behavior.
        """
        if allowed_workspaces is None:
            return list(items)
        allowed = set(allowed_workspaces)
        scopes = self.workspaces_of([item.get(id_key) for item in items])
        return [
            item
            for item in items
            if scopes.get(item.get(id_key)) is None
            or scopes.get(item.get(id_key)) in allowed
        ]

    def graph(self, limit: int = 300, *, allowed_workspaces=None) -> Dict[str, Any]:
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
            nodes = self.filter_scoped_nodes(nodes, allowed_workspaces)
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

    def search(self, query: str, limit: int = 30) -> Dict[str, Any]:
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
        return {
            "query": query,
            "matches": [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ],
        }

    def context_for_query(self, query: str, limit: int = 6) -> str:
        """Return compact graph-backed RAG context for chat generation."""
        query = str(query or "").strip()
        if not query:
            return ""
        matches = self.search(query, limit).get("matches", [])
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

    def neighbors(self, node_id: str) -> Dict[str, Any]:
        """Return direct neighbors (1-hop) of a node."""
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
        return {"node_id": node_id, "neighbors": nodes, "edges": edges}

    def get_node(self, node_id: str) -> Dict[str, Any]:
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
        return {
            "id": row["id"],
            "type": row["type"],
            "title": row["title"],
            "summary": row["summary"],
            "metadata": _safe_loads(row["metadata_json"]),
            "updated_at": row["updated_at"],
            "degree": degree,
        }

    def relationship_search(
        self,
        *,
        query: str = "",
        node_id: str = "",
        relationship_type: str = "",
        limit: int = 30,
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
        return {
            "query": query,
            "node_id": node_id,
            "relationship_type": relationship_type,
            "relationships": [
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
            ],
        }

    def traverse(
        self, node_id: str, *, depth: int = 1, limit: int = 100
    ) -> Dict[str, Any]:
        node_id = str(node_id or "").strip()
        if not node_id:
            raise ValueError("node_id required")
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
        return {
            "root": node_id,
            "depth": depth,
            "nodes": [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in node_rows
            ],
            "edges": list(edges_by_id.values()),
        }

    def _iter_vector_source_items(
        self,
        conn: sqlite3.Connection,
        *,
        include_nodes: bool = True,
        include_chunks: bool = True,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if include_nodes:
            for row in conn.execute(
                """
                    SELECT id, type, title, summary, metadata_json
                    FROM nodes
                    WHERE type <> 'Chunk'
                    ORDER BY updated_at DESC, id ASC
                    """
            ).fetchall():
                metadata = _safe_loads(row["metadata_json"])
                text = self._vector_text_for_node(
                    title=row["title"],
                    summary=row["summary"] or "",
                    metadata=metadata,
                )
                if text:
                    items.append(
                        {
                            "item_id": row["id"],
                            "item_type": "node",
                            "source_node": row["id"],
                            "text": text,
                            "metadata": {"node_type": row["type"], **metadata},
                        }
                    )
        if include_chunks:
            for row in conn.execute(
                """
                    SELECT c.id, c.source_node AS parent_source_node, c.text, c.metadata_json
                    FROM chunks c
                    JOIN nodes n ON n.id=c.id
                    ORDER BY c.created_at DESC, c.id ASC
                    """
            ).fetchall():
                metadata = _safe_loads(row["metadata_json"])
                text = _clean_text(row["text"] or "")
                if text:
                    items.append(
                        {
                            "item_id": row["id"],
                            "item_type": "chunk",
                            "source_node": row["id"],
                            "text": text,
                            "metadata": {
                                **metadata,
                                "parent_source_node": row["parent_source_node"],
                            },
                        }
                    )
        return items

    def rebuild_vector_index(
        self,
        *,
        full: bool = False,
        include_nodes: bool = True,
        include_chunks: bool = True,
    ) -> Dict[str, Any]:
        """Rebuild the derived vector index without mutating graph content."""
        op_id = f"vector-op:{_sha256_text(f'{time.time()}:{os.getpid()}')[:24]}"
        requested_at = _now()
        started = time.perf_counter()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                        INSERT INTO vector_index_operations(
                          id, operation, status, requested_at, started_at, metadata_json
                        )
                        VALUES (?, ?, 'running', ?, ?, ?)
                        """,
                    (
                        op_id,
                        "rebuild_full" if full else "rebuild_incremental",
                        requested_at,
                        requested_at,
                        _json(
                            {
                                "include_nodes": include_nodes,
                                "include_chunks": include_chunks,
                            }
                        ),
                    ),
                )
                if full:
                    filters = []
                    if include_nodes:
                        filters.append("'node'")
                    if include_chunks:
                        filters.append("'chunk'")
                    if filters:
                        conn.execute(
                            f"DELETE FROM vector_embeddings WHERE item_type IN ({','.join(filters)})"
                        )
                items = self._iter_vector_source_items(
                    conn,
                    include_nodes=include_nodes,
                    include_chunks=include_chunks,
                )
                indexed = skipped = 0
                for item in items:
                    changed = self._upsert_vector_item(conn, **item)
                    if changed:
                        indexed += 1
                    else:
                        skipped += 1
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                conn.execute(
                    """
                        UPDATE vector_index_operations
                        SET status='completed', completed_at=?, items_total=?,
                            items_indexed=?, items_skipped=?, metadata_json=?
                        WHERE id=?
                        """,
                    (
                        _now(),
                        len(items),
                        indexed,
                        skipped,
                        _json(
                            {
                                "include_nodes": include_nodes,
                                "include_chunks": include_chunks,
                                "duration_ms": duration_ms,
                                "embedding_model": self._embedding_model.model_id,
                                "embedding_dim": self._embedding_model.dim,
                            }
                        ),
                        op_id,
                    ),
                )
            return {
                "status": "completed",
                "operation_id": op_id,
                "full": bool(full),
                "items_total": len(items),
                "items_indexed": indexed,
                "items_skipped": skipped,
                "duration_ms": duration_ms,
                "embedding_model": self._embedding_model.model_id,
                "embedding_dim": self._embedding_model.dim,
            }
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            with self._connect() as conn:
                conn.execute(
                    """
                        INSERT INTO vector_index_operations(
                          id, operation, status, requested_at, started_at, completed_at,
                          error_message, metadata_json
                        )
                        VALUES (?, ?, 'failed', ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                          status='failed',
                          completed_at=excluded.completed_at,
                          error_message=excluded.error_message,
                          metadata_json=excluded.metadata_json
                        """,
                    (
                        op_id,
                        "rebuild_full" if full else "rebuild_incremental",
                        requested_at,
                        requested_at,
                        _now(),
                        str(exc),
                        _json({"duration_ms": duration_ms}),
                    ),
                )
            raise

    def index_status(self) -> Dict[str, Any]:
        with self._connect() as conn:
            vector_counts = {
                row["item_type"]: row["count"]
                for row in conn.execute(
                    "SELECT item_type, COUNT(*) AS count FROM vector_embeddings GROUP BY item_type"
                )
            }
            source_items = self._iter_vector_source_items(conn)
            vector_rows = {
                row["item_id"]: row
                for row in conn.execute(
                    """
                        SELECT item_id, text_hash, embedding_dim, embedding_model, indexed_at
                        FROM vector_embeddings
                        """
                ).fetchall()
            }
            latest_rows = conn.execute(
                """
                    SELECT id, operation, status, requested_at, started_at, completed_at,
                           items_total, items_indexed, items_skipped, error_message, metadata_json
                    FROM vector_index_operations
                    ORDER BY requested_at DESC, id DESC
                    LIMIT 5
                    """
            ).fetchall()
        missing = stale = ready = 0
        for item in source_items:
            vector_row = vector_rows.get(item["item_id"])
            expected_hash = _sha256_text(_clean_text(item["text"]))
            if not vector_row:
                missing += 1
            elif (
                vector_row["text_hash"] != expected_hash
                or vector_row["embedding_dim"] != self._embedding_model.dim
                or vector_row["embedding_model"] != self._embedding_model.model_id
            ):
                stale += 1
            else:
                ready += 1
        pending = missing + stale
        return {
            "status": "ready" if pending == 0 else "needs_reindex",
            "storage": {
                "db_path": str(self.db_path),
                "backend": "sqlite",
                "embedding_model": self._embedding_model.model_id,
                "embedding_dim": self._embedding_model.dim,
                # Honest capability report: trigram FTS5 keyword index, or
                # LIKE-scan fallback when this SQLite build lacks it.
                "fts_enabled": bool(getattr(self, "_fts_enabled", False)),
            },
            "source_items": len(source_items),
            "indexed_items": sum(vector_counts.values()),
            "ready_items": ready,
            "missing_items": missing,
            "stale_items": stale,
            "pending_items": pending,
            "by_item_type": vector_counts,
            "operations": [
                {
                    "id": row["id"],
                    "operation": row["operation"],
                    "status": row["status"],
                    "requested_at": row["requested_at"],
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "items_total": row["items_total"],
                    "items_indexed": row["items_indexed"],
                    "items_skipped": row["items_skipped"],
                    "error_message": row["error_message"],
                    "metadata": _safe_loads(row["metadata_json"]),
                }
                for row in latest_rows
            ],
        }

    def vector_search(
        self,
        query: str,
        *,
        limit: int = 30,
        min_score: float = 0.0,
        max_candidates: int = 10_000,
    ) -> Dict[str, Any]:
        query = str(query or "").strip()
        limit = max(1, min(int(limit or 30), 100))
        min_score = float(min_score or 0.0)
        if not query:
            return {"query": query, "matches": []}
        query_vector = self._embedding_model.embed(query)
        max_candidates = max(limit, min(int(max_candidates or 10_000), 50_000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                    SELECT
                      ve.item_id, ve.item_type, ve.source_node, ve.embedding,
                      ve.embedding_dim, ve.embedding_model, ve.metadata_json AS vector_metadata,
                      n.type AS node_type, n.title AS node_title, n.summary AS node_summary,
                      n.metadata_json AS node_metadata, n.updated_at AS node_updated_at,
                      c.text AS chunk_text, c.source_node AS parent_node_id,
                      pn.type AS parent_type, pn.title AS parent_title,
                      pn.summary AS parent_summary, pn.metadata_json AS parent_metadata,
                      pn.updated_at AS parent_updated_at
                    FROM vector_embeddings ve
                    LEFT JOIN nodes n ON n.id=ve.source_node
                    LEFT JOIN chunks c ON c.id=ve.item_id
                    LEFT JOIN nodes pn ON pn.id=c.source_node
                    WHERE ve.embedding_model=? AND ve.embedding_dim=?
                    ORDER BY ve.indexed_at DESC
                    LIMIT ?
                    """,
                (
                    self._embedding_model.model_id,
                    self._embedding_model.dim,
                    max_candidates,
                ),
            ).fetchall()
        scored = []
        for row in rows:
            vector = self._embedding_model.decode(
                row["embedding"], row["embedding_dim"]
            )
            score = self._embedding_model.similarity(query_vector, vector)
            if score < min_score:
                continue
            is_chunk = row["item_type"] == "chunk"
            summary = (
                row["chunk_text"]
                if is_chunk and row["chunk_text"]
                else row["node_summary"]
            )
            parent_metadata = _safe_loads(row["parent_metadata"])
            node_metadata = _safe_loads(row["node_metadata"])
            scored.append(
                {
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
                    },
                    "updated_at": row["parent_updated_at"]
                    if is_chunk and row["parent_updated_at"]
                    else row["node_updated_at"],
                }
            )
        scored.sort(
            key=lambda item: (item["score"], item.get("updated_at") or ""), reverse=True
        )
        return {
            "query": query,
            "embedding_model": self._embedding_model.model_id,
            "embedding_dim": self._embedding_model.dim,
            "matches": scored[:limit],
        }

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

    def search_for_document_generation(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Hybrid retrieval optimized for document generation.

        Scoring: 0.5*text_relevance + 0.3*graph_relationship + 0.2*recency
        Returns nodes with rich context for document generation prompts.
        """
        query = str(query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit or 10), 50))
        terms = _topic_candidates(query, limit=12)
        now = datetime.now()
        nt, et = self._read_tables()

        with self._connect() as conn:
            candidate_rows = []
            seen_ids = set()

            if query:
                q = f"%{query}%"
                rows = conn.execute(
                    f"""
                        SELECT id, type, title, summary, metadata_json, updated_at
                        FROM {nt}
                        WHERE (title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?)
                          AND type IN ('Document', 'File', 'CodeFile', 'SlideDeck',
                                       'Spreadsheet', 'Image', 'ImageText', 'Chat',
                                       'Decision', 'Task', 'Concept', 'Feature',
                                       'Page', 'Slide')
                        ORDER BY updated_at DESC, id ASC
                        LIMIT ?
                        """,
                    (q, q, q, limit * 5),
                ).fetchall()
                for row in rows:
                    if row["id"] not in seen_ids:
                        seen_ids.add(row["id"])
                        candidate_rows.append(row)

            for term in terms:
                t = f"%{term}%"
                rows = conn.execute(
                    f"""
                        SELECT id, type, title, summary, metadata_json, updated_at
                        FROM {nt}
                        WHERE (title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?)
                          AND type IN ('Document', 'File', 'CodeFile', 'SlideDeck',
                                       'Spreadsheet', 'Image', 'ImageText', 'Chat',
                                       'Decision', 'Task', 'Concept', 'Feature',
                                       'Page', 'Slide')
                        ORDER BY updated_at DESC, id ASC
                        LIMIT ?
                        """,
                    (t, t, t, limit * 3),
                ).fetchall()
                for row in rows:
                    if row["id"] not in seen_ids:
                        seen_ids.add(row["id"])
                        candidate_rows.append(row)

            scored_results = []
            for row in candidate_rows:
                haystack = (
                    f"{row['title']} {row['summary']} {row['metadata_json']}".lower()
                )

                text_hits = sum(1 for term in terms if term.lower() in haystack)
                text_score = min(1.0, text_hits / max(len(terms), 1))

                edge_count = conn.execute(
                    f"SELECT COUNT(*) AS c FROM {et} WHERE from_node=? OR to_node=?",
                    (row["id"], row["id"]),
                ).fetchone()["c"]
                graph_score = min(1.0, math.log1p(edge_count) / 4.0)

                recency = _recency_score(
                    row["updated_at"], now=now, half_life_days=14.0
                )

                doc_type_boost = (
                    1.2
                    if row["type"]
                    in (
                        "Document",
                        "File",
                        "SlideDeck",
                        "Decision",
                    )
                    else 1.0
                )

                hybrid_score = (
                    0.5 * text_score + 0.3 * graph_score + 0.2 * recency
                ) * doc_type_boost

                meta = _safe_loads(row["metadata_json"])
                neighbor_concepts = []
                neighbor_rows = conn.execute(
                    f"""
                        SELECT n.title, n.type FROM {et} e
                        JOIN {nt} n ON n.id = CASE WHEN e.from_node = ? THEN e.to_node ELSE e.from_node END
                        WHERE (e.from_node = ? OR e.to_node = ?)
                          AND n.type IN ('Concept', 'Feature', 'Decision', 'Task')
                        LIMIT 8
                        """,
                    (row["id"], row["id"], row["id"]),
                ).fetchall()
                for nr in neighbor_rows:
                    neighbor_concepts.append({"title": nr["title"], "type": nr["type"]})

                scored_results.append(
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "title": row["title"],
                        "summary": row["summary"],
                        "metadata": meta,
                        "updated_at": row["updated_at"],
                        "hybrid_score": round(hybrid_score, 4),
                        "scores": {
                            "text": round(text_score, 4),
                            "graph": round(graph_score, 4),
                            "recency": round(recency, 4),
                        },
                        "related_concepts": neighbor_concepts,
                    }
                )

            scored_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
            return scored_results[:limit]

    def multi_hop_context(
        self, node_ids: List[str], max_hops: int = 2
    ) -> Dict[str, Any]:
        """Multi-hop graph traversal from seed nodes for richer context."""
        visited_nodes = set()
        visited_edges = set()
        all_nodes = []
        all_edges = []
        frontier = set(node_ids)
        nt, et = self._read_tables()

        with self._connect() as conn:
            for hop in range(max_hops):
                if not frontier:
                    break
                next_frontier = set()
                for nid in frontier:
                    if nid in visited_nodes:
                        continue
                    visited_nodes.add(nid)
                    row = conn.execute(
                        f"SELECT id, type, title, summary, metadata_json, updated_at FROM {nt} WHERE id=?",
                        (nid,),
                    ).fetchone()
                    if row:
                        all_nodes.append(
                            {
                                "id": row["id"],
                                "type": row["type"],
                                "title": row["title"],
                                "summary": row["summary"],
                                "metadata": _safe_loads(row["metadata_json"]),
                                "hop": hop,
                            }
                        )
                    edge_rows = conn.execute(
                        f"""
                            SELECT id, from_node, to_node, type, weight
                            FROM {et} WHERE from_node=? OR to_node=?
                            ORDER BY id ASC
                            """,
                        (nid, nid),
                    ).fetchall()
                    for er in edge_rows:
                        if er["id"] not in visited_edges:
                            visited_edges.add(er["id"])
                            all_edges.append(
                                {
                                    "from": er["from_node"],
                                    "to": er["to_node"],
                                    "type": er["type"],
                                    "weight": er["weight"],
                                }
                            )
                            other = (
                                er["to_node"]
                                if er["from_node"] == nid
                                else er["from_node"]
                            )
                            if other not in visited_nodes:
                                next_frontier.add(other)
                frontier = next_frontier

        return {"nodes": all_nodes, "edges": all_edges}
