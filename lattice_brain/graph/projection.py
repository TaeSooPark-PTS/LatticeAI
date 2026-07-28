from __future__ import annotations

from ..quiet import quiet

# ruff: noqa: F403,F405
from ._kg_common import *  # noqa: F403,F401

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


class KnowledgeGraphProjectionMixin:
    _FTS_SQL = """
        CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
          node_id UNINDEXED, title, summary, metadata, tokenize='trigram'
        );
        CREATE TRIGGER IF NOT EXISTS node_fts_ai AFTER INSERT ON nodes BEGIN
          INSERT INTO node_fts(node_id, title, summary, metadata)
          VALUES (new.id, new.title, COALESCE(new.summary, ''), new.metadata_json);
        END;
        CREATE TRIGGER IF NOT EXISTS node_fts_au AFTER UPDATE ON nodes BEGIN
          DELETE FROM node_fts WHERE node_id = old.id;
          INSERT INTO node_fts(node_id, title, summary, metadata)
          VALUES (new.id, new.title, COALESCE(new.summary, ''), new.metadata_json);
        END;
        CREATE TRIGGER IF NOT EXISTS node_fts_ad AFTER DELETE ON nodes BEGIN
          DELETE FROM node_fts WHERE node_id = old.id;
        END;
        """

    _V2_VIEWS_SQL = """
        CREATE VIEW IF NOT EXISTS kgv2_nodes AS
          SELECT id,
                 COALESCE(legacy_type, type) AS type,
                 label AS title,
                 summary,
                 attrs AS metadata_json,
                 created_at, updated_at
          FROM nodes_v2;
        CREATE VIEW IF NOT EXISTS kgv2_edges AS
          SELECT id, source AS from_node, target AS to_node,
                 COALESCE(legacy_type, type) AS type,
                 weight,
                 metadata AS metadata_json,
                 created_at
          FROM edges_v2;
        """

    def _init_fts(self) -> None:
        self._fts_enabled = False
        try:
            with self._connect() as conn:
                conn.executescript(self._FTS_SQL)
                fts_count = conn.execute(
                    "SELECT count(*) AS c FROM node_fts"
                ).fetchone()["c"]
                if fts_count == 0:
                    conn.execute(
                        "INSERT INTO node_fts(node_id, title, summary, metadata) "
                        "SELECT id, title, COALESCE(summary, ''), metadata_json FROM nodes"
                    )
            self._fts_enabled = True
        except sqlite3.OperationalError as exc:
            # FTS5/trigram not compiled into this SQLite build. LIKE search
            # stays authoritative; the capability is reported, never faked.
            logging.info(
                "FTS5 trigram index unavailable (%s); keyword search uses LIKE scans.",
                exc,
            )

    def _fts_match_ids(
        self, conn: sqlite3.Connection, query: str, limit: int
    ) -> List[str]:
        """Ranked node ids for a trigram FTS query ('' on any failure)."""
        if not getattr(self, "_fts_enabled", False) or len(query) < 3:
            return []
        escaped = query.replace('"', '""')
        try:
            rows = conn.execute(
                "SELECT node_id FROM node_fts WHERE node_fts MATCH ? ORDER BY rank LIMIT ?",
                (f'"{escaped}"', limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [row["node_id"] for row in rows]

    def _init_v2_schema(self) -> None:
        """Initialize the normalized v2 tables + reconstruction views, migrating
        the projection layout when it is stale — **atomically**.

        The entire DROP → CREATE → VIEWS → BACKFILL → version-stamp sequence runs
        in a single transaction on one connection: on any failure it rolls back,
        leaving the prior projection untouched and the version unchanged, so the
        next startup simply retries. The migration only ever touches the v2
        tables/views and the ``projection_version`` key — never the authoritative
        legacy ``nodes``/``edges`` — so legacy data cannot be corrupted even if
        the rebuild fails midway.
        """
        if KGStoreV2 is None or _exec_script is None:
            return
        self._v2_projection_available = False
        try:
            self._backup_before_v2_flip()
            with self._connect() as conn:
                conn.execute("BEGIN")
                stale = self._projection_version(conn) != _PROJECTION_VERSION
                # Reconstruction views are non-authoritative. Recreate them on
                # every startup so older SQLite rename migrations cannot strand
                # a view against a temporary table such as edges_v2_old.
                for stmt in (
                    "DROP VIEW IF EXISTS kgv2_edges",
                    "DROP VIEW IF EXISTS kgv2_nodes",
                ):
                    conn.execute(stmt)
                if stale:
                    # The projection is non-authoritative; drop it so init_schema
                    # recreates the tables with the current normalized columns.
                    for stmt in (
                        "DROP TABLE IF EXISTS edges_v2",
                        "DROP TABLE IF EXISTS nodes_v2",
                    ):
                        conn.execute(stmt)
                # init_schema(conn=...) joins this transaction (no implicit commit)
                KGStoreV2(self.db_path).init_schema(conn=conn)
                _exec_script(conn, self._V2_VIEWS_SQL)
                self._backfill_v2_on(conn, force=stale)
                # version stamp commits together with the backfill — never stranded
                conn.execute(
                    "INSERT OR REPLACE INTO kg_meta(key, value) VALUES ('projection_version', ?)",
                    (str(_PROJECTION_VERSION),),
                )
                mastered_at = _now()
                conn.execute(
                    "INSERT OR REPLACE INTO kg_meta(key, value) VALUES (?, ?)",
                    (_KG_DB_FORMAT_KEY, str(_KG_DB_FORMAT_VERSION)),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO kg_meta(key, value) VALUES (?, COALESCE((SELECT value FROM kg_meta WHERE key=?), ?))",
                    (_V2_WRITE_MASTER_KEY, _V2_WRITE_MASTER_KEY, mastered_at),
                )
                conn.execute(f"PRAGMA user_version={_KG_DB_FORMAT_VERSION}")
                conn.execute("SELECT 1 FROM kgv2_nodes LIMIT 1").fetchone()
                conn.execute("SELECT 1 FROM kgv2_edges LIMIT 1").fetchone()
            self._v2_projection_available = True
        except Exception as e:
            logging.warning("knowledge_graph: v2 schema init/backfill skipped: %s", e)

    def _backup_before_v2_flip(self) -> Optional[str]:
        """Create one local SQLite backup before the v2 write-master flip."""
        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return None
        with self._connect() as conn:
            try:
                stamped = conn.execute(
                    "SELECT value FROM kg_meta WHERE key=?", (_V2_WRITE_MASTER_KEY,)
                ).fetchone()
            except sqlite3.Error:
                stamped = None
            if stamped:
                return None
            try:
                rows = int(
                    conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] or 0
                )
            except sqlite3.Error:
                rows = 0
            if rows == 0:
                return None
            conn.execute("PRAGMA wal_checkpoint(FULL)")
            backup_dir = self.db_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            dest = (
                backup_dir / f"{self.db_path.stem}.pre-v2-write-master.{stamp}.sqlite"
            )
            conn.execute("VACUUM INTO ?", (str(dest),))
            return str(dest)

    def _projection_version(self, conn: sqlite3.Connection) -> int:
        """Return the stored v2 projection layout version (0 if unknown).

        A fresh DB (kg_meta absent) raises ``sqlite3.OperationalError`` here and
        is correctly treated as version 0 → rebuild. Only sqlite errors are
        swallowed so a real bug doesn't masquerade as a stale projection.
        """
        try:
            row = conn.execute(
                "SELECT value FROM kg_meta WHERE key='projection_version'"
            ).fetchone()
            return int(row["value"]) if row and row["value"] is not None else 0
        except sqlite3.Error:
            return 0

    def _backfill_v2_if_needed(self, *, force: bool = False) -> None:
        """Project legacy nodes/edges into v2 on a fresh transaction.

        Thin wrapper around :meth:`_backfill_v2_on` for callers (tests, ad-hoc
        re-sync) that aren't already inside the migration transaction.
        """
        try:
            with self._connect() as conn:
                self._backfill_v2_on(conn, force=force)
        except Exception as ex:
            logging.warning("knowledge_graph: v2 backfill skipped: %s", ex)

    def _backfill_v2_on(self, conn: sqlite3.Connection, *, force: bool = False) -> None:
        """Project legacy nodes/edges into the normalized v2 tables on ``conn``.

        Non-destructive to legacy. ``force`` rebuilds unconditionally (used after
        a layout migration); otherwise it only projects when v2 is empty. The v2
        graph is a derived projection, so clearing + rebuilding it is always safe.
        Idempotent: no-ops once v2 carries the current projection. Copies the
        legacy column values **verbatim** so the kgv2_* views are byte-faithful.
        """
        legacy_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        if legacy_nodes == 0:
            return
        v2_nodes = conn.execute("SELECT COUNT(*) FROM nodes_v2").fetchone()[0]
        if v2_nodes > 0 and not force:
            return  # current projection already present
        # (re)project: clear v2 graph (not authoritative) and rebuild
        conn.execute("DELETE FROM edges_v2")
        conn.execute("DELETE FROM nodes_v2")
        n = e = 0
        for r in conn.execute(
            "SELECT id, type, title, summary, metadata_json, created_at, updated_at FROM nodes"
        ).fetchall():
            self._v2_project_node(
                conn,
                r["id"],
                r["type"],
                r["title"],
                r["summary"],
                r["metadata_json"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            n += 1
        for r in conn.execute(
            "SELECT id, from_node, to_node, type, weight, metadata_json, created_at FROM edges"
        ).fetchall():
            self._v2_project_edge(
                conn,
                r["from_node"],
                r["to_node"],
                r["type"],
                float(r["weight"] or 1.0),
                r["metadata_json"],
                edge_id=r["id"],
                created_at=r["created_at"],
            )
            e += 1
        logging.info(
            "knowledge_graph: projected legacy → v2 (%d nodes, %d edges)", n, e
        )

    def _v2_project_node(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        node_type: str,
        title: str,
        summary: Optional[str],
        metadata_json: Optional[str],
        *,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        visibility: Optional[str] = None,
        strict: bool = False,
    ) -> None:
        if KGStoreV2 is None:
            if strict:
                raise RuntimeError("Knowledge Graph v2 schema is unavailable")
            return
        ts = updated_at or _now()
        norm_type = (
            NodeType.from_legacy(node_type).value if NodeType is not None else node_type
        )
        # Scope resolution: explicit param > metadata hints > legacy-global.
        # 'legacy' (not 'private') marks unscoped rows — the column default
        # must never silently privatize previously machine-shared data.
        meta = _safe_loads(metadata_json) if metadata_json else {}
        owner = owner or meta.get("user_email") or meta.get("owner") or None
        workspace_id = workspace_id or meta.get("workspace_id") or None
        visibility = visibility or ("legacy" if workspace_id is None else "workspace")
        try:
            conn.execute(
                """
                    INSERT INTO nodes_v2(id, type, legacy_type, label, summary, attrs,
                                         owner_id, workspace_id, visibility,
                                         created_at, updated_at, importance_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0)
                    ON CONFLICT(id) DO UPDATE SET
                      type=excluded.type, legacy_type=excluded.legacy_type,
                      label=excluded.label, summary=excluded.summary,
                      attrs=excluded.attrs, updated_at=excluded.updated_at,
                      owner_id=COALESCE(excluded.owner_id, nodes_v2.owner_id),
                      workspace_id=COALESCE(excluded.workspace_id, nodes_v2.workspace_id),
                      visibility=CASE WHEN excluded.visibility != 'legacy'
                                      THEN excluded.visibility
                                      ELSE nodes_v2.visibility END
                    """,
                (
                    node_id,
                    norm_type,
                    node_type,
                    title,
                    summary,
                    metadata_json if metadata_json is not None else "{}",
                    owner,
                    workspace_id,
                    visibility,
                    created_at or ts,
                    ts,
                ),
            )
        except Exception as ex:
            if strict:
                raise
            logging.debug(
                "knowledge_graph: v2 node projection skipped (%s): %s", node_id, ex
            )

    def _v2_project_edge(
        self,
        conn: sqlite3.Connection,
        from_node: str,
        to_node: str,
        edge_type: str,
        weight: float,
        metadata_json: Optional[str],
        *,
        edge_id: Optional[str] = None,
        created_at: Optional[str] = None,
        strict: bool = False,
        legacy_type: Optional[str] = None,
    ) -> None:
        if KGStoreV2 is None:
            if strict:
                raise RuntimeError("Knowledge Graph v2 schema is unavailable")
            return
        explicit_legacy_type = legacy_type is not None
        leg_type = legacy_type if explicit_legacy_type else edge_type
        # Native canonical writes (and write-door dedupes) use legacy_type=''
        # so (source,target,type) is the effective key.
        # Import paths can pass distinct legacy_type to keep colliding legacy
        # labels as separate rows (lossless for old data).
        if leg_type and EdgeType is not None:
            try:
                if EdgeType.from_legacy(leg_type).value == leg_type:
                    leg_type = ""
            except ValueError:
                quiet()
        norm_type = (
            EdgeType.from_legacy(edge_type).value if EdgeType is not None else edge_type
        )
        if explicit_legacy_type and leg_type:
            eid = f"edge:{_sha256_text(f'{from_node}|{norm_type}|{to_node}|{leg_type}')[:24]}"
        else:
            eid = edge_id or f"edge:{_sha256_text(f'{from_node}|{norm_type}|{to_node}')[:24]}"
        meta_str = metadata_json if metadata_json is not None else "{}"
        confidence = float(_safe_loads(meta_str).get("confidence", 1.0))
        try:
            conn.execute(
                """
                    INSERT INTO edges_v2(id, source, target, type, legacy_type, weight,
                                         confidence, evidence, metadata, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, '[]', ?, 'legacy', ?)
                    ON CONFLICT(source, target, type, legacy_type) DO UPDATE SET
                      weight=max(edges_v2.weight, excluded.weight),
                      confidence=excluded.confidence,
                      metadata=excluded.metadata
                    """,
                (
                    eid,
                    from_node,
                    to_node,
                    norm_type,
                    leg_type,
                    float(weight),
                    confidence,
                    meta_str,
                    created_at or _now(),
                ),
            )
            # Temporal record: every observation of this relationship is kept
            # (the UNIQUE upsert + weight=max alone would erase recurrence).
            row = conn.execute(
                "SELECT id FROM edges_v2 WHERE source=? AND target=? AND type=? AND legacy_type=?",
                (from_node, to_node, norm_type, leg_type),
            ).fetchone()
            if row is not None:
                conn.execute(
                    "INSERT INTO edge_occurrences(edge_id, observed_at, weight, source) VALUES (?, ?, ?, ?)",
                    (
                        row["id"],
                        created_at or _now(),
                        float(weight),
                        _safe_loads(meta_str).get("source"),
                    ),
                )
        except Exception as ex:
            if strict:
                raise
            logging.debug(
                "knowledge_graph: v2 edge projection skipped (%s->%s): %s",
                from_node,
                to_node,
                ex,
            )

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
        from .curator import auto_build_graph_overlay

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
        from .curator import (
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

    def _v2_delete_nodes(self, conn: sqlite3.Connection, ids) -> None:
        """Mirror legacy node deletions into v2 (edges_v2 cascade on the FK)."""
        if KGStoreV2 is None:
            return
        ids = list(ids)
        if not ids:
            return
        ph = ",".join("?" * len(ids))
        try:
            conn.execute(f"DELETE FROM nodes_v2 WHERE id IN ({ph})", ids)
        except Exception as ex:
            logging.debug("knowledge_graph: v2 node delete mirror skipped: %s", ex)

    def _v2_delete_edges_from(self, conn: sqlite3.Connection, node_id: str) -> None:
        """Mirror a legacy ``DELETE FROM edges WHERE from_node=?`` into v2."""
        if KGStoreV2 is None:
            return
        try:
            conn.execute("DELETE FROM edges_v2 WHERE source=?", (node_id,))
        except Exception as ex:
            logging.debug("knowledge_graph: v2 edge delete mirror skipped: %s", ex)

    def _v2_sync_report(self) -> Dict[str, Any]:
        """Diagnose the dual-write invariant: legacy node/edge id sets must equal
        the v2 projection's. Returns counts + any drift (ids missing from / extra
        in v2). ``in_sync`` is True only when both id sets match exactly.

        All legacy writes go through _upsert_node/_upsert_edge (which dual-write)
        and every legacy delete is mirrored, so a non-empty drift signals a
        bypassed write path — this is the runtime guard for that invariant.
        """
        if KGStoreV2 is None:
            return {"available": False, "in_sync": True}
        with self._connect() as conn:
            legacy_nodes = {r[0] for r in conn.execute("SELECT id FROM nodes")}
            v2_nodes = {r[0] for r in conn.execute("SELECT id FROM nodes_v2")}
            legacy_edges = {r[0] for r in conn.execute("SELECT id FROM edges")}
            v2_edges = {r[0] for r in conn.execute("SELECT id FROM edges_v2")}
        return {
            "available": True,
            "in_sync": legacy_nodes == v2_nodes and legacy_edges == v2_edges,
            "nodes_legacy": len(legacy_nodes),
            "nodes_v2": len(v2_nodes),
            "edges_legacy": len(legacy_edges),
            "edges_v2": len(v2_edges),
            "nodes_missing_from_v2": sorted(legacy_nodes - v2_nodes),
            "nodes_extra_in_v2": sorted(v2_nodes - legacy_nodes),
            "edges_missing_from_v2": sorted(legacy_edges - v2_edges),
            "edges_extra_in_v2": sorted(v2_edges - legacy_edges),
        }
