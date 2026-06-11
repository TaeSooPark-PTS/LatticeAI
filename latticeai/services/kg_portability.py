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
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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
            from latticeai.brain.identity import verify_manifest

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

    def restore(self, archive_path, *, verify: bool = True) -> Dict[str, Any]:
        self._require()
        archive = Path(archive_path)
        if not archive.exists():
            raise FileNotFoundError(f"Backup archive not found: {archive}")
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
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
                db_dest = Path(self._kg.db_path)
                blob_dest = Path(self._kg.blob_dir)
                db_dest.parent.mkdir(parents=True, exist_ok=True)
                # Drop the live DB + stale WAL/SHM siblings so the restored copy
                # is authoritative (no stale journal overlaying old pages).
                for sib in (db_dest, Path(str(db_dest) + "-wal"), Path(str(db_dest) + "-shm")):
                    if sib.exists():
                        sib.unlink()
                shutil.copyfile(db_src, db_dest)
                blob_src = tmp / "blobs"
                if blob_src.exists():
                    if blob_dest.exists():
                        shutil.rmtree(blob_dest)
                    shutil.copytree(blob_src, blob_dest)
                else:
                    blob_dest.mkdir(parents=True, exist_ok=True)
        stats = self._kg.stats()
        return {
            "restored": True,
            "manifest": manifest,
            "nodes": sum(stats.get("nodes", {}).values()),
        }

    # ── status surface ───────────────────────────────────────────────────────
    def snapshot_metadata(self) -> Dict[str, Any]:
        if not self.available():
            return {"available": False}
        return {
            "available": True,
            **self._kg.schema_versions(),
            "stats": self._kg.stats(),
            "provenance": self._kg.provenance_stats(),
        }

    def recent_ingestions(self, *, limit: int = 50, source_type: Optional[str] = None) -> Dict[str, Any]:
        """Recent provenance records (newest first) for the ingestion-sources UI."""
        if not self.available():
            return {"items": [], "count": 0}
        return self._kg.list_provenance(limit=limit, source_type=source_type)
