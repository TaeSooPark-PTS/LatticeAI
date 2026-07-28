"""Safe SQLite to Postgres migration tooling."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .postgres import PostgresEngine, _quote_ident


def _pg_type(sqlite_type: str) -> str:
    t = str(sqlite_type or "").upper()
    if "INT" in t:
        return "bigint"
    if any(token in t for token in ("REAL", "FLOA", "DOUB")):
        return "double precision"
    if "BLOB" in t:
        return "bytea"
    return "text"


def _adapt_value(value: Any) -> Any:
    if isinstance(value, memoryview):
        return bytes(value)
    return value


@dataclass(frozen=True)
class TablePlan:
    name: str
    columns: List[Dict[str, str]]
    rows: int
    conflict_key: str
    conflict_columns: List[str]
    rowid_available: bool


class SQLiteToPostgresMigrator:
    """Copies every user table from a Lattice SQLite brain into Postgres.

    The migration is idempotent: tables with an ``id`` column upsert on ``id``;
    tables without one use the preserved SQLite rowid in ``__source_rowid``.
    SQLite remains untouched throughout.
    """

    def __init__(self, sqlite_path: Path, target: PostgresEngine) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.target = target

    def plan(self) -> Dict[str, Any]:
        if not self.sqlite_path.exists():
            raise FileNotFoundError(f"SQLite brain database not found: {self.sqlite_path}")
        with closing(sqlite3.connect(str(self.sqlite_path))) as conn, conn:
            conn.row_factory = sqlite3.Row
            table_names = [
                row["name"]
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            ]
            tables = []
            for table in table_names:
                cols = [
                    {"name": row["name"], "type": row["type"] or "TEXT"}
                    for row in conn.execute(f"PRAGMA table_info({_quote_sqlite_ident(table)})")
                ]
                row_count = conn.execute(f"SELECT COUNT(*) FROM {_quote_sqlite_ident(table)}").fetchone()[0]
                names = {c["name"] for c in cols}
                rowid_available = _rowid_available(conn, table)
                pk_columns = [
                    row["name"]
                    for row in sorted(
                        conn.execute(f"PRAGMA table_info({_quote_sqlite_ident(table)})"),
                        key=lambda item: int(item["pk"] or 0),
                    )
                    if int(row["pk"] or 0) > 0
                ]
                conflict_columns = (
                    ["id"]
                    if "id" in names
                    else pk_columns
                    if pk_columns
                    else ["__source_rowid"]
                    if rowid_available
                    else []
                )
                if not conflict_columns:
                    raise RuntimeError(
                        f"Cannot safely migrate rowid-less SQLite table without a primary key: {table}"
                    )
                tables.append(
                    TablePlan(
                        name=table,
                        columns=cols,
                        rows=int(row_count),
                        conflict_key=conflict_columns[0],
                        conflict_columns=conflict_columns,
                        rowid_available=rowid_available,
                    )
                )
        return {
            "source": str(self.sqlite_path),
            "target_engine": self.target.name,
            "target_schema": self.target.config.schema,
            "tables": [table.__dict__ for table in tables],
            "total_rows": sum(table.rows for table in tables),
        }

    def migrate(self, *, dry_run: bool = False) -> Dict[str, Any]:
        plan = self.plan()
        if dry_run:
            return {"status": "planned", **plan}
        schema = _quote_ident(self.target.config.schema)
        copied: Dict[str, int] = {}
        self.target.initialize()
        with closing(sqlite3.connect(str(self.sqlite_path))) as src, self.target.session() as dst:
            src.row_factory = sqlite3.Row
            with dst.cursor() as cur:
                for table in plan["tables"]:
                    name = str(table["name"])
                    cols = list(table["columns"])
                    conflict_columns = list(table.get("conflict_columns") or [table["conflict_key"]])
                    rowid_available = bool(table.get("rowid_available", True))
                    pg_table = f"{schema}.{_quote_ident(name)}"
                    defs = ["__source_rowid bigint NOT NULL"] if rowid_available else []
                    for col in cols:
                        defs.append(f"{_quote_ident(col['name'])} {_pg_type(col['type'])}")
                    pk = ", ".join(_quote_ident(c) for c in conflict_columns)
                    cur.execute(
                        f"CREATE TABLE IF NOT EXISTS {pg_table} ({', '.join(defs)}, PRIMARY KEY ({pk}))"
                    )
                    if rowid_available:
                        select_sql = (
                            f"SELECT rowid AS __source_rowid, * FROM {_quote_sqlite_ident(name)} ORDER BY rowid"
                        )
                    else:
                        order_by = ", ".join(_quote_sqlite_ident(c) for c in conflict_columns)
                        select_sql = f"SELECT * FROM {_quote_sqlite_ident(name)} ORDER BY {order_by}"
                    rows = src.execute(select_sql).fetchall()
                    if not rows:
                        copied[name] = 0
                        continue
                    columns = (["__source_rowid"] if rowid_available else []) + [c["name"] for c in cols]
                    placeholders = ", ".join(["%s"] * len(columns))
                    quoted_columns = ", ".join(_quote_ident(c) for c in columns)
                    updates = ", ".join(
                        f"{_quote_ident(c)} = EXCLUDED.{_quote_ident(c)}"
                        for c in columns
                        if c not in conflict_columns
                    )
                    conflict_action = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
                    sql = (
                        f"INSERT INTO {pg_table} ({quoted_columns}) VALUES ({placeholders}) "
                        f"ON CONFLICT ({pk}) {conflict_action}"
                    )
                    cur.executemany(
                        sql,
                        [
                            tuple(_adapt_value(row[col]) for col in columns)
                            for row in rows
                        ],
                    )
                    copied[name] = len(rows)
        return {
            "status": "migrated",
            **plan,
            "copied_rows": copied,
            "total_copied_rows": sum(copied.values()),
        }


def _quote_sqlite_ident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _rowid_available(conn: sqlite3.Connection, table: str) -> bool:
    try:
        conn.execute(f"SELECT rowid FROM {_quote_sqlite_ident(table)} LIMIT 1").fetchall()
        return True
    except sqlite3.OperationalError:
        return False


__all__ = ["SQLiteToPostgresMigrator", "TablePlan"]
