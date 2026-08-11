"""Curation over the projected graph: promotions, the review queue, noise.

Two jobs and the queue between them. ``curate`` runs the curator's gated topic
promotion and either writes it or — in review mode — parks it in ``graph_meta``
for a human decision; ``curate_noise`` removes heuristic concept nodes whose
document-frequency stats mark them as noise and normalizes free-string relation
verbs. Both are explicit and observable: everything skipped is reported with a
reason, and ``dry_run=True`` is the default for the destructive one.
"""

# ruff: noqa: F403,F405,S608
from __future__ import annotations

from typing import TYPE_CHECKING

from .._kg_common import *  # noqa: F401

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged.
if TYPE_CHECKING:
    from .._kg_contract import KnowledgeGraphCore as _Core
else:
    _Core = object


# ── promotion review mode (review 2026-07-25 Wave 4) ─────────────────────────
# When enabled, curate() parks would-be Topic promotions in graph_meta for a
# human decision instead of writing them immediately. Explicit review_mode=
# argument wins; otherwise this env opt-in decides; default stays auto-promote.
_PROMOTION_REVIEW_ENV = "LATTICEAI_GRAPH_PROMOTION_REVIEW"
_PENDING_PROMOTIONS_KEY = "pending_promotions"
_PENDING_PROMOTIONS_CAP = 100

# graph_meta stamp written by an applied (dry_run=False) noise-curate run; the
# Command Center hygiene advisory reads it to pace its suggestion (Wave 2.5).
_LAST_NOISE_CURATE_KEY = "last_noise_curate_at"


def _promotion_review_default() -> bool:
    return os.getenv(_PROMOTION_REVIEW_ENV, "").strip().lower() in ("1", "true", "yes")


class KnowledgeGraphCurationMixin(_Core):
    """Curation + promotions. Mixed into ``KnowledgeGraphProjectionMixin``."""

    def curate(
        self,
        *,
        max_documents: int = 200,
        max_new_nodes: int = 8,
        review_mode: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """On-demand graph curation (T4.4 — graph_curator goes live).

        Runs the curator's gated topic-promotion pipeline over recent content
        nodes: candidates are clustered, secret-bearing labels are refused,
        and only multi-source topics above the importance threshold become
        Topic nodes (with MENTIONS edges back to their sources and a real
        importance_score in nodes_v2). Explicit and observable — the result
        reports everything promoted AND everything skipped, with reasons.

        ``review_mode`` (review 2026-07-25 Wave 4): when True, nothing is
        written — the would-be promotions are parked in ``graph_meta`` as
        ``pending_promotions`` for a human decision via
        :meth:`apply_pending_promotions` / :meth:`reject_pending_promotions`.
        Explicit argument wins; ``None`` falls back to the
        ``LATTICEAI_GRAPH_PROMOTION_REVIEW`` env opt-in; default stays the
        historical auto-promote behavior.
        """
        from ..curator import auto_build_graph_overlay

        content_types = (
            "Document",
            "File",
            "CodeFile",
            "Message",
            "AIResponse",
            "Chat",
            "Page",
            "Slide",
            "Spreadsheet",
        )
        nt, _ = self._read_tables()
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in content_types)
            rows = conn.execute(
                f"""
                    SELECT id, type, title, summary FROM {nt}
                    WHERE type IN ({placeholders})
                    ORDER BY updated_at DESC, id ASC LIMIT ?
                    """,
                (*content_types, max(1, min(int(max_documents), 2000))),
            ).fetchall()
            existing_labels = {
                str(row["title"] or "").strip().lower()
                for row in conn.execute(
                    f"SELECT title FROM {nt} WHERE type IN ('Topic', 'Concept')"
                ).fetchall()
            }
        documents = [
            {
                "id": row["id"],
                "text": f"{row['title']} {row['summary'] or ''}",
                "kind": "file"
                if row["type"] in {"Document", "File", "CodeFile", "Spreadsheet"}
                else "chat",
            }
            for row in rows
        ]
        overlay = auto_build_graph_overlay(
            documents,
            existing_node_labels=existing_labels,
            max_new_nodes=max(1, min(int(max_new_nodes), 50)),
        )
        valid_ids = {row["id"] for row in rows}
        review = review_mode if review_mode is not None else _promotion_review_default()
        if review:
            proposed_at = _now()
            proposed = [
                {
                    "id": f"topic:{_slug(promo['label'])}",
                    "label": promo["label"],
                    "importance": promo["importance"],
                    "aliases": promo["aliases"],
                    "sources": [s for s in promo["sources"][:10] if s in valid_ids],
                    "proposed_at": proposed_at,
                }
                for promo in overlay["promotions"]
            ]
            with self._connect() as conn:
                merged = self._merge_pending_promotions(conn, proposed)
            return {
                "status": "pending_review",
                "documents_scanned": len(documents),
                "candidates_total": overlay["candidates_total"],
                "pending": proposed,
                "pending_total": len(merged),
                "skipped": overlay["skipped"][:50],
                "skipped_total": len(overlay["skipped"]),
            }
        promoted: List[Dict[str, Any]] = []
        with self._connect() as conn:
            for promo in overlay["promotions"]:
                promoted.append(
                    self._write_promotion(conn, promo, valid_source_ids=valid_ids)
                )
        return {
            "status": "ok",
            "documents_scanned": len(documents),
            "candidates_total": overlay["candidates_total"],
            "promoted": promoted,
            "skipped": overlay["skipped"][:50],
            "skipped_total": len(overlay["skipped"]),
        }

    def _write_promotion(
        self,
        conn: sqlite3.Connection,
        promo: Dict[str, Any],
        *,
        valid_source_ids: Optional[set] = None,
    ) -> Dict[str, Any]:
        """Write one curator promotion: Topic node + importance + MENTIONS edges.

        Single write path shared by direct ``curate()`` and
        :meth:`apply_pending_promotions`, so a human-approved promotion lands
        exactly like an auto-promoted one. ``valid_source_ids`` restricts the
        linkable sources to this curate run's scanned rows; when ``None``
        (apply-after-review), each stored source is checked for existence so a
        node deleted between propose and apply is skipped, not an error.
        """
        topic_id = str(promo.get("id") or f"topic:{_slug(str(promo['label']))}")
        self._upsert_node(
            conn,
            topic_id,
            "Topic",
            str(promo["label"]),
            metadata={
                "curated": True,
                "importance": promo["importance"],
                "aliases": list(promo.get("aliases") or []),
                "source": "graph_curator",
            },
        )
        conn.execute(
            "UPDATE nodes_v2 SET importance_score=? WHERE id=?",
            (float(promo["importance"]), topic_id),
        )
        linked = 0
        for source_id in list(promo.get("sources") or [])[:10]:
            if valid_source_ids is not None:
                if source_id not in valid_source_ids:
                    continue
            elif not conn.execute(
                "SELECT 1 FROM nodes WHERE id=?", (source_id,)
            ).fetchone():
                continue
            self._upsert_edge(
                conn,
                source_id,
                topic_id,
                "MENTIONS",
                weight=0.6,
                metadata={"source": "graph_curator"},
            )
            linked += 1
        return {
            "node_id": topic_id,
            "label": promo["label"],
            "importance": promo["importance"],
            "linked_sources": linked,
        }

    # ── pending promotion queue (review 2026-07-25 Wave 4) ───────────────────

    def _read_pending_promotions(
        self, conn: sqlite3.Connection
    ) -> List[Dict[str, Any]]:
        try:
            row = conn.execute(
                "SELECT value FROM graph_meta WHERE key=?",
                (_PENDING_PROMOTIONS_KEY,),
            ).fetchone()
        except sqlite3.Error:
            return []
        if not row or not row["value"]:
            return []
        try:
            parsed = json.loads(row["value"])
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [
            item for item in parsed if isinstance(item, dict) and item.get("id")
        ]

    def _store_pending_promotions(
        self, conn: sqlite3.Connection, entries: List[Dict[str, Any]]
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
            (_PENDING_PROMOTIONS_KEY, json.dumps(entries, ensure_ascii=False)),
        )

    def _merge_pending_promotions(
        self, conn: sqlite3.Connection, proposed: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Merge new proposals into the stored queue (dedupe by id, cap 100)."""
        merged: Dict[str, Dict[str, Any]] = {}
        for item in self._read_pending_promotions(conn) + list(proposed):
            merged[str(item["id"])] = item  # newest proposal wins per id
        entries = list(merged.values())[-_PENDING_PROMOTIONS_CAP:]
        self._store_pending_promotions(conn, entries)
        return entries

    def pending_promotions(self) -> List[Dict[str, Any]]:
        """List promotions waiting for a human decision (review mode)."""
        with self._connect() as conn:
            return self._read_pending_promotions(conn)

    def apply_pending_promotions(
        self, ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Apply stored pending promotions (all of them when ``ids`` is None).

        Uses the exact node-writing path as direct ``curate()`` via
        :meth:`_write_promotion`; applied entries leave the queue.
        """
        wanted = None if ids is None else {str(item) for item in ids}
        applied: List[Dict[str, Any]] = []
        remaining: List[Dict[str, Any]] = []
        with self._connect() as conn:
            for promo in self._read_pending_promotions(conn):
                if wanted is not None and str(promo.get("id")) not in wanted:
                    remaining.append(promo)
                    continue
                applied.append(self._write_promotion(conn, promo))
            self._store_pending_promotions(conn, remaining)
        return {"status": "ok", "applied": applied, "remaining": len(remaining)}

    def reject_pending_promotions(
        self, ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Drop pending promotions without writing (all when ``ids`` is None)."""
        wanted = None if ids is None else {str(item) for item in ids}
        rejected: List[str] = []
        remaining: List[Dict[str, Any]] = []
        with self._connect() as conn:
            for promo in self._read_pending_promotions(conn):
                if wanted is not None and str(promo.get("id")) not in wanted:
                    remaining.append(promo)
                    continue
                rejected.append(str(promo.get("id")))
            self._store_pending_promotions(conn, remaining)
        return {"status": "ok", "rejected": rejected, "remaining": len(remaining)}

    _NOISE_CONTENT_TYPES = (
        "Document",
        "File",
        "CodeFile",
        "Message",
        "AIResponse",
        "Chat",
        "Page",
        "Slide",
        "Spreadsheet",
    )
    _NOISE_CONCEPT_TYPES = ("Concept", "Feature", "Topic", "Code", "Error")

    def curate_noise(
        self,
        *,
        dry_run: bool = True,
        max_df_ratio: float = 0.8,
        min_doc_frequency: int = 1,
        min_corpus_docs: int = 5,
        normalize_verbs: bool = True,
        max_removals: int = 200,
    ) -> Dict[str, Any]:
        """Noise-reduction curation job (backlog #10, review §7.2 D).

        (a) Removes heuristic concept nodes (``auto_extracted`` /
        ``graph_curator``-promoted) whose document frequency marks them as
        noise: ubiquitous (low IDF — linked from more than ``max_df_ratio`` of
        content docs once the corpus has ``min_corpus_docs``) or below the
        ``min_doc_frequency`` floor. Explicitly user-created nodes are never
        touched, whatever their stats.

        (b) Normalizes free-string relation verbs on the legacy edge table via
        the ko/en dictionary in :mod:`lattice_brain.graph.curator`
        ('만들다/만든/creates' → 'created', …), merging rows that collide
        after the rename.

        ``dry_run=True`` (the default) only *reports* what would change.
        """
        from ..curator import (
            build_relation_verb_index,
            plan_concept_noise_reduction,
            plan_relation_normalization,
        )

        max_removals = max(0, int(max_removals))
        # Operate on the legacy write tables directly: they are the mutation
        # target, and raw free-string verbs only exist there (the v4 write
        # door normalizes new edges; the kgv2_* read views collapse
        # legacy_type and would hide exactly the rows this job cleans up).
        nt, et = "nodes", "edges"
        with self._connect() as conn:
            content_ph = ",".join("?" for _ in self._NOISE_CONTENT_TYPES)
            total_docs = conn.execute(
                f"SELECT COUNT(*) AS c FROM {nt} WHERE type IN ({content_ph})",
                self._NOISE_CONTENT_TYPES,
            ).fetchone()["c"]

            concept_ph = ",".join("?" for _ in self._NOISE_CONCEPT_TYPES)
            concept_rows = conn.execute(
                f"SELECT id, type, title, metadata_json FROM {nt} WHERE type IN ({concept_ph})",
                self._NOISE_CONCEPT_TYPES,
            ).fetchall()
            concepts = []
            for row in concept_rows:
                meta = _safe_loads(row["metadata_json"]) or {}
                heuristic = bool(meta.get("auto_extracted")) or (
                    meta.get("source") == "graph_curator" or meta.get("curated") is True
                )
                # Document frequency: distinct *content* nodes linked to this
                # concept in either direction.
                df = conn.execute(
                    f"""
                    SELECT COUNT(DISTINCT n.id) AS c
                    FROM {et} e
                    JOIN {nt} n
                      ON n.id = CASE WHEN e.to_node = ? THEN e.from_node ELSE e.to_node END
                    WHERE (e.to_node = ? OR e.from_node = ?)
                      AND n.type IN ({content_ph})
                    """,
                    (row["id"], row["id"], row["id"], *self._NOISE_CONTENT_TYPES),
                ).fetchone()["c"]
                concepts.append({
                    "id": row["id"],
                    "label": row["title"],
                    "type": row["type"],
                    "df": int(df or 0),
                    "heuristic": heuristic,
                })

            plan = plan_concept_noise_reduction(
                concepts,
                total_docs,
                max_df_ratio=max_df_ratio,
                min_doc_frequency=min_doc_frequency,
                min_corpus_docs=min_corpus_docs,
            )
            removals = plan["remove"][:max_removals]

            verb_index = build_relation_verb_index()
            edge_type_rows = conn.execute(
                f"SELECT DISTINCT type FROM {et}"
            ).fetchall()
            verb_plan = (
                plan_relation_normalization(
                    (row["type"] for row in edge_type_rows), index=verb_index,
                )
                if normalize_verbs
                else {}
            )

            removed_count = 0
            renamed_edges = 0
            if not dry_run:
                for decision in removals:
                    node_id = decision["id"]
                    conn.execute(
                        "DELETE FROM edges WHERE from_node=? OR to_node=?",
                        (node_id, node_id),
                    )
                    conn.execute(
                        "DELETE FROM vector_embeddings WHERE item_id=?", (node_id,)
                    )
                    conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
                    self._v2_delete_nodes(conn, [node_id])
                    removed_count += 1
                for original, canonical in verb_plan.items():
                    renamed_edges += conn.execute(
                        "SELECT COUNT(*) AS c FROM edges WHERE type=?", (original,)
                    ).fetchone()["c"]
                    # UNIQUE(from_node, to_node, type): merge rows that collide
                    # after the rename instead of failing the UPDATE.
                    conn.execute(
                        "UPDATE OR IGNORE edges SET type=? WHERE type=?",
                        (canonical, original),
                    )
                    conn.execute("DELETE FROM edges WHERE type=?", (original,))
                # Stamp every applied run — even a no-op one means the graph
                # was inspected, so the Command Center hygiene advisory
                # (review 2026-07-25 Wave 2.5) stops re-suggesting for a while.
                conn.execute(
                    "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
                    (_LAST_NOISE_CURATE_KEY, _now()),
                )

        return {
            "status": "ok",
            "dry_run": bool(dry_run),
            "total_content_docs": int(total_docs or 0),
            "concepts_examined": len(concepts),
            "remove": removals,
            "remove_total": len(plan["remove"]),
            "kept": len(plan["keep"]),
            "protected_user_nodes": sum(
                1 for item in plan["keep"] if item.get("reason") == "user_created_protected"
            ),
            "verb_normalizations": verb_plan,
            "applied": {
                "removed_nodes": removed_count,
                "renamed_edges": renamed_edges,
            },
            "thresholds": {
                "max_df_ratio": float(max_df_ratio),
                "min_doc_frequency": int(min_doc_frequency),
                "min_corpus_docs": int(min_corpus_docs),
            },
        }

    def last_noise_curate_at(self) -> Optional[str]:
        """Timestamp of the last applied (dry_run=False) noise-curate run.

        ``None`` when the job never ran or the meta table is unreadable —
        advisory readers treat both as "curation is due" (fail-open).
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM graph_meta WHERE key=?",
                    (_LAST_NOISE_CURATE_KEY,),
                ).fetchone()
        except sqlite3.Error:
            return None
        return str(row["value"]) if row and row["value"] else None

    def mark_superseded(self, old_node_id: str, new_node_id: str) -> Dict[str, Any]:
        """Record that ``old_node_id`` was replaced by ``new_node_id``.

        The old node stays queryable (knowledge is durable); readers can follow
        the revision chain via ``nodes_v2.superseded_by``.
        """
        with self._connect() as conn:
            for node_id in (old_node_id, new_node_id):
                exists = conn.execute(
                    "SELECT 1 FROM nodes_v2 WHERE id=?", (node_id,)
                ).fetchone()
                if not exists:
                    raise FileNotFoundError(node_id)
            conn.execute(
                "UPDATE nodes_v2 SET superseded_by=?, updated_at=? WHERE id=?",
                (new_node_id, _now(), old_node_id),
            )
        return {"status": "ok", "node_id": old_node_id, "superseded_by": new_node_id}
