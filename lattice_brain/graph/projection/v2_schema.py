"""The legacy → v2 projection: schema, backfill, per-row writes, mirrors.

The v2 tables and the ``kgv2_*`` reconstruction views are *derived* — the legacy
``nodes``/``edges`` tables stay authoritative — which is what makes the whole
DROP → CREATE → VIEWS → BACKFILL → stamp migration safe to run in one
transaction and simply retry on the next startup when it fails. The trigram FTS
index lives here too: it is the other index projected off the same rows.
"""

# ruff: noqa: F403,F405,S608
from __future__ import annotations

from typing import TYPE_CHECKING

from ...quiet import quiet
from .._kg_common import *  # noqa: F401

# The cross-mixin surface (`_connect`, `_upsert_node`, …) is declared in
# `_kg_contract.KnowledgeGraphCore`. It is a typing-only base: at runtime this
# is `object`, so the MRO of `KnowledgeGraphStore` is unchanged.
if TYPE_CHECKING:
    from .._kg_contract import KnowledgeGraphCore as _Core
else:
    _Core = object


class KnowledgeGraphV2SchemaMixin(_Core):
    """Projection + FTS. Mixed into ``KnowledgeGraphProjectionMixin``."""

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

    # ``type`` is reconstructed from ``legacy_type`` when there is one, because
    # that column carries the label a reader expects. The trap the 11.0.1
    # review recorded — and which 11.2.0 fixes — is that ``edges_v2.legacy_type``
    # is ``NOT NULL DEFAULT ''``: a natively-canonical edge is written with
    # ``legacy_type=''`` so its identity is effectively (source, target, type),
    # and ``COALESCE('', type)`` returns ``''`` because COALESCE only skips
    # NULL. Every canonical edge therefore read back with an **empty type**.
    # ``NULLIF(legacy_type, '')`` is the fix: empty means "no legacy label",
    # which is precisely what the write side meant by it.
    #
    # The empty string stays the write-side sentinel on purpose. SQLite treats
    # NULLs as distinct in a UNIQUE index, so moving to NULL would silently
    # disable the (source, target, type, legacy_type) dedupe and let the same
    # relation land twice.
    #
    # The temporal columns pass through *raw* (v11.1.0): validity is not a
    # label, and a COALESCE there would turn "still valid" (NULL) into a value.
    # NULL in, NULL out; the fallback to ``created_at`` belongs to the read
    # predicate (``schema.TEMPORAL_PREDICATE_SQL``), not to the view.
    _V2_VIEWS_SQL = """
        CREATE VIEW IF NOT EXISTS kgv2_nodes AS
          SELECT id,
                 COALESCE(NULLIF(legacy_type, ''), type) AS type,
                 label AS title,
                 summary,
                 attrs AS metadata_json,
                 created_at, updated_at,
                 valid_from, valid_to, superseded_by
          FROM nodes_v2;
        CREATE VIEW IF NOT EXISTS kgv2_edges AS
          SELECT id, source AS from_node, target AS to_node,
                 COALESCE(NULLIF(legacy_type, ''), type) AS type,
                 weight,
                 metadata AS metadata_json,
                 created_at,
                 valid_from, valid_to, superseded_by
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
                self._normalize_v2_legacy_types(conn)
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

    def _normalize_v2_legacy_types(self, conn: sqlite3.Connection) -> Dict[str, int]:
        """Bring pre-11.2.0 rows onto the current ``legacy_type`` convention.

        The convention is: ``legacy_type`` holds the *raw* label only when it
        differs from the canonical type, and ``''`` (edges) / ``NULL`` (nodes)
        otherwise. Rows written by older builds could carry the canonical value
        in both columns, which is redundant and — because the dedupe key
        includes ``legacy_type`` — splits one relation across two rows.

        Idempotent by construction: a second run matches nothing. ``UPDATE OR
        IGNORE`` is used because collapsing a redundant row can collide with
        the canonical one that already exists; those survivors are counted and
        reported rather than deleted, since the fixed view reads both
        identically and deleting an edge is not a migration's business.
        """
        report = {"edges": 0, "nodes": 0, "collisions": 0}
        try:
            before = int(
                conn.execute(
                    "SELECT COUNT(*) FROM edges_v2 WHERE legacy_type = type"
                ).fetchone()[0]
                or 0
            )
            conn.execute(
                "UPDATE OR IGNORE edges_v2 SET legacy_type='' WHERE legacy_type = type"
            )
            after = int(
                conn.execute(
                    "SELECT COUNT(*) FROM edges_v2 WHERE legacy_type = type"
                ).fetchone()[0]
                or 0
            )
            report["edges"] = before - after
            report["collisions"] = after
            cursor = conn.execute(
                "UPDATE nodes_v2 SET legacy_type=NULL WHERE legacy_type = ''"
            )
            report["nodes"] = int(cursor.rowcount or 0)
        except sqlite3.Error as exc:
            # The projection is derived; a migration that cannot run leaves the
            # data exactly as it was and the fixed view still reads it right.
            logging.debug("knowledge_graph: legacy_type normalization skipped: %s", exc)
        return report

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

