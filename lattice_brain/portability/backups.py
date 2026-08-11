"""Binary backup/restore, the encrypted archive, and the storage status reads.

The half of :class:`~lattice_brain.portability.KGPortabilityService` that moves
*files*: a ZIP snapshot of the database plus blobs, the passphrase-encrypted
``.latticebrain`` archive, and the status/migration surfaces that sit on top of
them (a Postgres migration refuses to start without a verified backup).

Integrity hashing (``_sha256_file``) and the Postgres migrator are looked up as
this module's globals, so a test standing in for either patches *this* module.
"""

from __future__ import annotations

import json
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from ..archive import BrainArchivePaths, EncryptedBrainArchive
from ..storage import (
    DockerPostgresWizard,
    PostgresEngine,
    SQLiteToPostgresMigrator,
)
from ..utils import sha256_file as _sha256_file
from ..utils import utc_now_iso
from ._contract import PortabilityCore as _Core
from .constants import BACKUP_FORMAT, FORMAT_VERSION, _stamp
from .fsops import (
    _pre_restore_backup_dir,
    _replace_sqlite_atomically,
    _replace_tree_with_backup,
    _rollback_sqlite_from_backup,
    _safe_zip_names,
)


class KGPortabilityBackupMixin(_Core):
    """Backup / restore / storage status. Mixed into ``KGPortabilityService``."""

    # ── binary backup / restore ──────────────────────────────────────────────
    def backup(self, dest_path=None) -> Dict[str, Any]:
        self._require()
        self._exports_dir.mkdir(parents=True, exist_ok=True)
        dest = Path(dest_path) if dest_path else self._exports_dir / f"kg-backup-{_stamp()}.zip"
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            db_copy = tmp / "knowledge_graph.sqlite"
            self._kg.backup_database(db_copy)
            manifest = {
                "format": BACKUP_FORMAT,
                "format_version": FORMAT_VERSION,
                **self._kg.schema_versions(),
                "created_at": utc_now_iso(),
                "db_sha256": _sha256_file(db_copy),
                "has_blobs": Path(self._kg.blob_dir).exists(),
            }
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(db_copy, "knowledge_graph.sqlite")
                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                blob_dir = Path(self._kg.blob_dir)
                if blob_dir.exists():
                    for f in blob_dir.rglob("*"):
                        if f.is_file():
                            zf.write(f, f"blobs/{f.relative_to(blob_dir)}")
        return {"path": str(dest), "bytes": dest.stat().st_size, "manifest": manifest}

    def restore(
        self,
        archive_path,
        *,
        verify: bool = True,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> Dict[str, Any]:
        self._require()
        archive = Path(archive_path)
        if not archive.exists():
            raise FileNotFoundError(f"Backup archive not found: {archive}")
        if not dry_run and not confirm:
            raise ValueError("Explicit confirmation is required before restoring a Knowledge Graph backup.")
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            _safe_zip_names(names)
            if "knowledge_graph.sqlite" not in names:
                raise ValueError("Archive is missing knowledge_graph.sqlite.")
            manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in names else {}
            with tempfile.TemporaryDirectory() as tmp_s:
                tmp = Path(tmp_s)
                zf.extractall(tmp)
                db_src = tmp / "knowledge_graph.sqlite"
                if verify and manifest.get("db_sha256"):
                    if _sha256_file(db_src) != manifest["db_sha256"]:
                        raise ValueError("Backup integrity check failed (db sha256 mismatch).")
                if dry_run:
                    return {
                        "restored": False,
                        "dry_run": True,
                        "verified": True,
                        "manifest": manifest,
                        "planned": {
                            "database": str(self._kg.db_path),
                            "blobs": str(self._kg.blob_dir),
                            "archive": str(archive),
                        },
                    }
                db_dest = Path(self._kg.db_path)
                blob_dest = Path(self._kg.blob_dir)
                backup_dir = _pre_restore_backup_dir(db_dest)
                try:
                    _replace_sqlite_atomically(db_src, db_dest, backup_dir)
                    blob_src = tmp / "blobs"
                    _replace_tree_with_backup(blob_src if blob_src.exists() else None, blob_dest, backup_dir)
                except Exception:
                    # The rollback is best-effort recovery; an I/O slip inside
                    # it must never mask the restore failure being reported.
                    try:
                        _rollback_sqlite_from_backup(db_dest, backup_dir)
                    except OSError as rollback_exc:
                        logging.warning(
                            "pre-restore rollback incomplete (backup kept at %s): %s",
                            backup_dir, rollback_exc,
                        )
                    raise
        stats = self._kg.stats()
        return {
            "restored": True,
            "manifest": manifest,
            "pre_restore_backup": str(backup_dir),
            "nodes": sum(stats.get("nodes", {}).values()),
        }

    def verify_backup(self, archive_path) -> Dict[str, Any]:
        archive = Path(archive_path)
        if not archive.exists():
            return {"ok": False, "path": str(archive), "errors": [f"Backup archive not found: {archive}"]}
        try:
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
                _safe_zip_names(names)
                if "knowledge_graph.sqlite" not in names:
                    raise ValueError("Archive is missing knowledge_graph.sqlite.")
                manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in names else {}
                with tempfile.TemporaryDirectory() as tmp_s:
                    tmp = Path(tmp_s)
                    zf.extract("knowledge_graph.sqlite", tmp)
                    db_src = tmp / "knowledge_graph.sqlite"
                    if manifest.get("db_sha256") and _sha256_file(db_src) != manifest["db_sha256"]:
                        raise ValueError("Backup integrity check failed (db sha256 mismatch).")
            return {"ok": True, "path": str(archive), "manifest": manifest, "errors": []}
        except (ValueError, zipfile.BadZipFile, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "path": str(archive), "errors": [str(exc)]}

    # ── encrypted .latticebrain archive ───────────────────────────────────
    def encrypted_archive(self, dest_path=None, *, passphrase: str) -> Dict[str, Any]:
        self._require()
        self._exports_dir.mkdir(parents=True, exist_ok=True)
        dest = Path(dest_path) if dest_path else self._exports_dir / f"brain-{_stamp()}.latticebrain"
        metadata = {
            "storage": self.storage_status().get("active", {}),
            "snapshot": self.snapshot_metadata(),
            "device_identity": self._identity.describe() if self._identity is not None else {},
            "provenance": {"exported_at": utc_now_iso(), "source": "kg-portability"},
        }
        archive = EncryptedBrainArchive(
            BrainArchivePaths(
                db_path=Path(self._kg.db_path),
                blob_dir=Path(self._kg.blob_dir),
                data_dir=self._data_dir,
                metadata=metadata,
            )
        )
        return archive.create(dest, passphrase=passphrase)

    def inspect_encrypted_archive(self, archive_path, *, passphrase: Optional[str] = None) -> Dict[str, Any]:
        archive = EncryptedBrainArchive(
            BrainArchivePaths(
                db_path=Path(self._kg.db_path),
                blob_dir=Path(self._kg.blob_dir),
                data_dir=self._data_dir,
            )
        )
        return archive.inspect(Path(archive_path), passphrase=passphrase)

    def verify_encrypted_archive(self, archive_path, *, passphrase: str) -> Dict[str, Any]:
        archive = EncryptedBrainArchive(
            BrainArchivePaths(
                db_path=Path(self._kg.db_path),
                blob_dir=Path(self._kg.blob_dir),
                data_dir=self._data_dir,
            )
        )
        return archive.verify(Path(archive_path), passphrase=passphrase)

    def restore_encrypted_archive(
        self,
        archive_path,
        *,
        passphrase: str,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> Dict[str, Any]:
        self._require()
        archive = EncryptedBrainArchive(
            BrainArchivePaths(
                db_path=Path(self._kg.db_path),
                blob_dir=Path(self._kg.blob_dir),
                data_dir=self._data_dir,
            )
        )
        return archive.restore(
            Path(archive_path),
            passphrase=passphrase,
            target=BrainArchivePaths(
                db_path=Path(self._kg.db_path),
                blob_dir=Path(self._kg.blob_dir),
                data_dir=self._data_dir,
            ),
            dry_run=dry_run,
            confirm=confirm,
        )

    def import_encrypted_archive(
        self,
        archive_path,
        *,
        passphrase: str,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> Dict[str, Any]:
        result = self.restore_encrypted_archive(
            archive_path,
            passphrase=passphrase,
            dry_run=dry_run,
            confirm=confirm,
        )
        result["operation"] = "import"
        return result

    # ── status surface ───────────────────────────────────────────────────────
    def snapshot_metadata(self) -> Dict[str, Any]:
        if not self.available():
            return {"available": False}
        return {
            "available": True,
            **self._kg.schema_versions(),
            "stats": self._kg.stats(),
            "provenance": self._kg.provenance_stats(),
            "storage": (
                self._kg.storage_engine.capabilities().as_dict()
                if getattr(self._kg, "storage_engine", None) is not None
                else {"engine": "sqlite", "available": True}
            ),
        }

    def storage_status(self) -> Dict[str, Any]:
        if not self.available():
            return {"available": False}
        return {
            "available": True,
            "active": (
                self._kg.storage_engine.capabilities().as_dict()
                if getattr(self._kg, "storage_engine", None) is not None
                else {"engine": "sqlite", "available": True}
            ),
            "postgres": PostgresEngine("", schema="lattice_brain").capabilities().as_dict(),
            "backup_health": self.backup_health(),
        }

    def backup_health(self) -> Dict[str, Any]:
        self._exports_dir.mkdir(parents=True, exist_ok=True)
        backups = sorted(
            [
                p for p in self._exports_dir.glob("*")
                if p.is_file() and (p.suffix == ".zip" or p.suffix == ".latticebrain")
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        latest = backups[0] if backups else None
        return {
            "available": True,
            "directory": str(self._exports_dir),
            "count": len(backups),
            "latest": str(latest) if latest else None,
            "latest_bytes": latest.stat().st_size if latest else 0,
            "encrypted_archives": sum(1 for p in backups if p.suffix == ".latticebrain"),
            "zip_backups": sum(1 for p in backups if p.suffix == ".zip"),
        }

    def postgres_docker_setup(
        self,
        *,
        consent: bool,
        dry_run: bool = False,
        port: int = 5432,
    ) -> Dict[str, Any]:
        wizard = DockerPostgresWizard(self._data_dir / "postgres", port=port)
        return wizard.start(consent=consent, dry_run=dry_run)

    def migrate_sqlite_to_postgres(
        self,
        *,
        dsn: str,
        schema: str = "lattice_brain",
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        self._require()
        if not dsn:
            raise ValueError("Postgres DSN is required for SQLite to Postgres migration.")
        migrator = SQLiteToPostgresMigrator(
            Path(self._kg.db_path),
            PostgresEngine(dsn, schema=schema),
        )
        if dry_run:
            return migrator.migrate(dry_run=True)
        backup = self.backup()
        verification = self.verify_backup(backup["path"])
        if not verification.get("ok"):
            raise RuntimeError(
                "Pre-migration backup verification failed; Postgres migration was not started: "
                + "; ".join(verification.get("errors") or [])
            )
        result = migrator.migrate(dry_run=False)
        result["pre_migration_backup"] = {
            "path": backup["path"],
            "verified": True,
            "manifest": backup.get("manifest"),
        }
        return result

    def recent_ingestions(self, *, limit: int = 50, source_type: Optional[str] = None) -> Dict[str, Any]:
        """Recent provenance records (newest first) for the ingestion-sources UI."""
        if not self.available():
            return {"items": [], "count": 0}
        return self._kg.list_provenance(limit=limit, source_type=source_type)
