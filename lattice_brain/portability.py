"""Knowledge Graph portability — local export / import / backup / restore.

The Knowledge Graph is the user's durable asset, so it must be portable without
any cloud service. Two complementary mechanisms, both fully local:

* **Logical export/import** (JSON): nodes/edges/chunks/sources/provenance with a
  versioned header (schema + projection + embed-dim). Re-embeds on import, so it
  is portable across machines.
* **Binary backup/restore** (ZIP): a faithful snapshot of the SQLite DB (incl.
  vector embeddings) plus the blob directory, integrity-checked, for
  same-machine recovery.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional

from .archive import BrainArchivePaths, EncryptedBrainArchive
from .storage import (
    DockerPostgresWizard,
    PostgresEngine,
    SQLiteToPostgresMigrator,
)

FORMAT = "latticeai.kg.export"
FORMAT_VERSION = 1
BACKUP_FORMAT = "latticeai.kg.backup"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return _now_iso().replace(":", "").replace("-", "").replace(".", "")[:15]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _safe_zip_names(names) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Backup archive contains unsafe path: {name}")


def _pre_restore_backup_dir(anchor: Path) -> Path:
    backup_dir = anchor.parent / f"{anchor.name}.pre-restore-{_stamp()}"
    index = 1
    while backup_dir.exists():
        backup_dir = anchor.parent / f"{anchor.name}.pre-restore-{_stamp()}-{index}"
        index += 1
    backup_dir.mkdir(parents=True)
    return backup_dir


def _sqlite_siblings(db_path: Path) -> tuple[Path, Path, Path]:
    return (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm"))


def _restore_sibling(path: Path, backup: Path) -> None:
    if backup.exists():
        shutil.copy2(backup, path)
    elif path.exists():
        path.unlink()


def _replace_sqlite_atomically(src: Path, dest: Path, backup_dir: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.restore-{_stamp()}-{os.getpid()}.tmp"
    shutil.copyfile(src, tmp)
    backups: dict[Path, Path] = {}
    try:
        for sibling in _sqlite_siblings(dest):
            if sibling.exists():
                backup = backup_dir / sibling.name
                shutil.copy2(sibling, backup)
                backups[sibling] = backup
        for sibling in _sqlite_siblings(dest)[1:]:
            if sibling.exists():
                sibling.unlink()
        os.replace(tmp, dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        for sibling in _sqlite_siblings(dest):
            _restore_sibling(sibling, backups.get(sibling, backup_dir / sibling.name))
        raise


def _rollback_sqlite_from_backup(dest: Path, backup_dir: Path) -> None:
    for sibling in _sqlite_siblings(dest):
        _restore_sibling(sibling, backup_dir / sibling.name)


def _replace_tree_with_backup(src: Optional[Path], dest: Path, backup_dir: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = dest.parent / f".{dest.name}.restore-{_stamp()}-{os.getpid()}"
    backup = backup_dir / dest.name
    if src and src.exists():
        shutil.copytree(src, staged)
    else:
        staged.mkdir(parents=True)
    try:
        if dest.exists():
            shutil.copytree(dest, backup)
            shutil.rmtree(dest)
        os.replace(staged, dest)
    except Exception:
        if staged.exists():
            shutil.rmtree(staged)
        if dest.exists():
            shutil.rmtree(dest)
        if backup.exists():
            shutil.copytree(backup, dest)
        raise


class KGPortabilityService:
    def __init__(self, *, knowledge_graph: Any, data_dir, enable_graph: bool = True, device_identity: Any = None) -> None:
        self._kg = knowledge_graph
        self._data_dir = Path(data_dir)
        self._enable = bool(enable_graph)
        self._exports_dir = self._data_dir / "workspace_exports"
        # v4 sovereignty: when a DeviceIdentity is wired, exports are signed
        # and imports record origin provenance. Pre-v4 unsigned bundles stay
        # importable locally (origin='unsigned-legacy') — signatures are
        # mandatory only on the Brain Network peer path.
        self._identity = device_identity

    def available(self) -> bool:
        return self._enable and self._kg is not None

    def _require(self) -> None:
        if not self.available():
            raise RuntimeError("Knowledge Graph is disabled (LATTICEAI_ENABLE_GRAPH).")

    # ── logical export / import ──────────────────────────────────────────────
    def export(self, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        self._require()
        data = self._kg.export_graph_data(workspace_id=workspace_id)
        header = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            **self._kg.schema_versions(),
            "exported_at": _now_iso(),
            "workspace_id": workspace_id,
            "counts": data.get("counts"),
        }
        artifact = {"header": header, **data}
        if self._identity is not None:
            artifact["signature"] = self._identity.sign_manifest(header)
        return artifact

    def export_to_file(self, path=None, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        artifact = self.export(workspace_id=workspace_id)
        self._exports_dir.mkdir(parents=True, exist_ok=True)
        path = Path(path) if path else self._exports_dir / f"kg-export-{_stamp()}.json"
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), "header": artifact["header"], "bytes": path.stat().st_size}

    def import_data(self, artifact: Dict[str, Any], *, mode: str = "merge", dry_run: bool = False) -> Dict[str, Any]:
        self._require()
        if not isinstance(artifact, dict) or "nodes" not in artifact:
            raise ValueError("Invalid Knowledge Graph export artifact.")
        if mode not in ("merge", "replace"):
            raise ValueError("mode must be 'merge' or 'replace'.")
        origin = "unsigned-legacy"
        signature = artifact.get("signature")
        if signature:
            from .graph.identity import verify_manifest

            if not verify_manifest(artifact.get("header") or {}, signature):
                raise ValueError("Bundle signature verification failed — refusing to import.")
            origin = f"device:{signature.get('fingerprint') or 'unknown'}"
        result = self._kg.import_graph_data(artifact, mode=mode, dry_run=dry_run)
        result["header"] = artifact.get("header")
        result["origin"] = origin
        result["signed"] = bool(signature)
        if not dry_run:
            try:
                self._kg.record_provenance(
                    node_id="import:" + str((artifact.get("header") or {}).get("exported_at") or _now_iso()),
                    source_type="bundle_import",
                    pipeline="kg-portability",
                    owner=None,
                    metadata={"origin": origin, "mode": mode,
                              "counts": (artifact.get("header") or {}).get("counts")},
                )
            except Exception:
                pass
        return result

    def import_from_file(self, path, *, mode: str = "merge", dry_run: bool = False) -> Dict[str, Any]:
        artifact = json.loads(Path(path).read_text(encoding="utf-8"))
        return self.import_data(artifact, mode=mode, dry_run=dry_run)

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
                "created_at": _now_iso(),
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
                    _rollback_sqlite_from_backup(db_dest, backup_dir)
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
            "provenance": {"exported_at": _now_iso(), "source": "kg-portability"},
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
