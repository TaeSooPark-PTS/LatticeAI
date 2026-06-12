"""SQLite storage engine for Brain Core.

SQLite is the default and remains fully local-first. sqlite-vec is detected and
loaded when present; otherwise the existing brute-force cosine path remains the
honest, real fallback and is surfaced in capability reports.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from .base import StorageCapabilities, StorageEngine


def _load_sqlite_vec(conn: sqlite3.Connection) -> tuple[bool, Optional[str]]:
    try:
        import sqlite_vec  # type: ignore
    except Exception as exc:
        return False, f"sqlite-vec Python package not installed: {exc}"
    try:
        conn.enable_load_extension(True)
    except Exception:
        pass
    try:
        sqlite_vec.load(conn)
    except Exception as exc:
        return False, f"sqlite-vec extension failed to load: {exc}"
    return True, None


class SQLiteEngine(StorageEngine):
    name = "sqlite"

    def __init__(self, db_path: Path, *, load_vec: bool = True) -> None:
        self.db_path = Path(db_path)
        self.load_vec = bool(load_vec)
        self._sqlite_vec_loaded = False
        self._sqlite_vec_reason: Optional[str] = None

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        if self.load_vec:
            loaded, reason = _load_sqlite_vec(conn)
            self._sqlite_vec_loaded = loaded
            self._sqlite_vec_reason = reason
        return conn

    def initialize(self) -> Dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS storage_meta (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO storage_meta(key, value) VALUES ('engine', 'sqlite')"
            )
        return {"engine": self.name, "db_path": str(self.db_path)}

    def capabilities(self) -> StorageCapabilities:
        if not self.db_path.parent.exists():
            return StorageCapabilities(
                engine=self.name,
                available=True,
                vector_backend="bruteforce-cosine",
                vector_available=True,
                backup_restore=True,
                migrations=True,
                encrypted_archives=True,
                metadata={
                    "db_path": str(self.db_path),
                    "sqlite_vec_loaded": False,
                    "vector_mode": "fallback",
                    "honest_fallback": "sqlite-vec has not been probed yet; vector search uses the real brute-force cosine fallback until sqlite-vec is loaded.",
                },
            )
        # Probe on demand so status is accurate even before the graph opens.
        try:
            with self.connect():
                pass
        except Exception as exc:
            return StorageCapabilities(
                engine=self.name,
                available=False,
                reason=str(exc),
                metadata={"db_path": str(self.db_path)},
            )
        vector_backend = "sqlite-vec" if self._sqlite_vec_loaded else "bruteforce-cosine"
        return StorageCapabilities(
            engine=self.name,
            available=True,
            reason=None if self._sqlite_vec_loaded else (
                f"{self._sqlite_vec_reason}; using real brute-force cosine fallback, not sqlite-vec ANN"
                if self._sqlite_vec_reason
                else "sqlite-vec unavailable; using real brute-force cosine fallback, not sqlite-vec ANN"
            ),
            vector_backend=vector_backend,
            vector_available=True,
            backup_restore=True,
            migrations=True,
            encrypted_archives=True,
            metadata={
                "db_path": str(self.db_path),
                "sqlite_vec_loaded": self._sqlite_vec_loaded,
                "sqlite_vec_ann_available": self._sqlite_vec_loaded,
                "vector_mode": "sqlite-vec" if self._sqlite_vec_loaded else "fallback",
                "degraded": not self._sqlite_vec_loaded,
                "honest_fallback": None if self._sqlite_vec_loaded else "Vector search is available through the deterministic brute-force cosine backend. sqlite-vec ANN is unavailable.",
            },
        )

    def backup(self, destination: Path) -> Dict[str, Any]:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as src, sqlite3.connect(str(dest)) as dst:
            src.backup(dst)
        return {"engine": self.name, "path": str(dest), "bytes": dest.stat().st_size}

    def restore(self, source: Path) -> Dict[str, Any]:
        src = Path(source)
        if not src.exists():
            raise FileNotFoundError(f"SQLite backup not found: {src}")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        for sibling in (self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")):
            if sibling.exists():
                sibling.unlink()
        shutil.copyfile(src, self.db_path)
        return {"engine": self.name, "restored": True, "path": str(self.db_path)}


__all__ = ["SQLiteEngine"]
