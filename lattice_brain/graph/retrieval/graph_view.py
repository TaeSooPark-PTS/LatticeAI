"""The graph view and the lexical search over it.

``graph()`` renders the visible node/edge picture a UI draws; ``search()`` is
the keyword channel that ``hybrid_search`` fuses with the vector channel.
Split out of ``retrieval.py`` (v11.3.0) with both methods moved verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged.
if TYPE_CHECKING:
    from .._kg_contract import KnowledgeGraphCore as _Core
else:
    _Core = object


class _GraphViewMixin(_Core):
    """Graph rendering + lexical search. Composed into the public mixin."""

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
        "Audio",  # 녹음 / 음성 메모 (11.1.0)
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
                continue  # pragma: no cover — unreachable: the edge query selects endpoints from the same node window
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
                        "Audio",
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
