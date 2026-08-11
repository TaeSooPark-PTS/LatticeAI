"""Query → LLM context: the retrieval surface the chat path calls.

``context_for_query`` picks the channel (hybrid or lexical) and returns the
nodes plus the honest quality signal that says how thin the answer's ground
is. Moved verbatim out of ``retrieval.py`` (v11.3.0).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# ruff: noqa: F403,F405
from .._kg_common import *  # noqa: F403,F401
from .signals import context_quality_signal, multimodal_signal

# Typing-only base (runtime value is `object`, so the store's MRO is
# unchanged). Context assembly calls both retrieval channels through `self` —
# `hybrid_search` from .hybrid and, through it, `search` from .graph_view — so
# the sibling half is the base that states what this one may assume. The store
# contract (`_connect`, `_upsert_node`, …) arrives with it.
if TYPE_CHECKING:
    from .hybrid import _HybridSearchMixin as _Core
else:
    _Core = object


class _ContextMixin(_Core):
    """Context assembly for a query. Composed into the public mixin."""

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
        vector_meta: Optional[Dict[str, Any]] = None
        multimodal_meta: Optional[Dict[str, Any]] = None
        if use_hybrid:
            try:
                hybrid = self.hybrid_search(
                    query,
                    top_k=limit,
                    allowed_workspaces=allowed_workspaces,
                    include_legacy_global=include_legacy_global,
                )
                matches = hybrid.get("matches", [])
                vector_block = hybrid.get("vector") or {}
                # Only a caveat is worth carrying: approximate scoring, a
                # truncated candidate scan, or an already-flagged degradation.
                if (
                    vector_block.get("approx")
                    or vector_block.get("truncated")
                    or vector_block.get("degraded")
                ):
                    vector_meta = dict(vector_block)
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
        # Only the context that actually reached the model counts as
        # multimodal — matches trimmed by ``limit`` are not in the answer.
        multimodal_meta = multimodal_signal(matches[:limit])
        return {
            "context": context,
            "quality": context_quality_signal(
                retrieval_mode,
                len(matches[:limit]),
                vector=vector_meta,
                multimodal=multimodal_meta,
            ),
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
