"""Encrypted .latticebrain archive support.

The archive is intentionally self-contained and local-only: the encrypted
payload holds the SQLite brain, blob store, portable JSON state, workspace
export bundles when present, and public metadata needed to inspect/verify/
restore on another machine without contacting a service.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .utils import sha256_file as _sha256_file


ARCHIVE_FORMAT = "latticebrain.encrypted"
ARCHIVE_VERSION = 2
KDF_ITERATIONS = 390_000
PORTABLE_DATA_FILES = (
    "users.json",
    "chat_history.json",
    "workspace_os.json",
    "vpc_config.json",
    "mcp_installs.json",
    "audit_log.json",
    "sso_config.json",
    "hooks.json",
    "invitations.json",
    "agent_registry.json",
    "brain_peers.json",
)
PORTABLE_EXPORT_SUFFIXES = (".json", ".zip")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    return _now().replace(":", "").replace("-", "").replace(".", "")[:15]


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if not passphrase:
        raise ValueError("A passphrase is required for encrypted .latticebrain archives.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _safe_json(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_safe_json(v) for v in value]
        return str(value)


def _assert_safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Archive payload contains unsafe path: {name}")
    return path


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


def _checkpoint_sqlite(db_path: Path) -> None:
    if not db_path.exists():
        return
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
    except sqlite3.Error:
        return


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
        _checkpoint_sqlite(dest)
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


@dataclass(frozen=True)
class BrainArchivePaths:
    db_path: Path
    blob_dir: Optional[Path] = None
    data_dir: Optional[Path] = None
    metadata: Optional[Dict[str, Any]] = None


class EncryptedBrainArchive:
    """Create and restore encrypted local Brain Core archives."""

    def __init__(self, paths: BrainArchivePaths) -> None:
        self.paths = paths

    def _iter_payload_files(self) -> Iterable[tuple[Path, str]]:
        yield Path(self.paths.db_path), "knowledge_graph.sqlite"
        if self.paths.blob_dir and self.paths.blob_dir.exists():
            blob_root = Path(self.paths.blob_dir)
            for file in sorted(blob_root.rglob("*")):
                if file.is_file():
                    yield file, f"blobs/{file.relative_to(blob_root).as_posix()}"
        if self.paths.data_dir and Path(self.paths.data_dir).exists():
            data_root = Path(self.paths.data_dir)
            for name in PORTABLE_DATA_FILES:
                file = data_root / name
                if file.is_file():
                    yield file, f"data/{name}"
            exports = data_root / "workspace_exports"
            if exports.exists():
                for file in sorted(exports.rglob("*")):
                    if not file.is_file():
                        continue
                    if file.suffix == ".latticebrain":
                        continue
                    if file.suffix not in PORTABLE_EXPORT_SUFFIXES:
                        continue
                    yield file, f"workspace_exports/{file.relative_to(exports).as_posix()}"

    def _build_manifest(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        metadata = _safe_json(self.paths.metadata or {})
        return {
            "format": "latticebrain.payload",
            "format_version": ARCHIVE_VERSION,
            "created_at": _now(),
            "sections": {
                "graph": any(item["path"] == "knowledge_graph.sqlite" for item in entries),
                "blobs": any(item["path"].startswith("blobs/") for item in entries),
                "workspace_state": any(item["path"].startswith("data/") for item in entries),
                "signed_bundles": any(item["path"].startswith("workspace_exports/") for item in entries),
            },
            "metadata": metadata,
            "storage": metadata.get("storage") or {},
            "device_identity": metadata.get("device_identity") or {},
            "provenance": metadata.get("provenance") or {},
            "entries": entries,
        }

    def _payload_zip_bytes(self) -> tuple[bytes, Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as tmp_s:
            payload = Path(tmp_s) / "payload.zip"
            with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zf:
                for src, arcname in self._iter_payload_files():
                    _assert_safe_member(arcname)
                    zf.write(src, arcname)
                    entries.append({
                        "path": arcname,
                        "bytes": src.stat().st_size,
                        "sha256": _sha256_file(src),
                    })
                manifest = self._build_manifest(entries)
                manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
                zf.writestr("manifest.json", manifest_bytes)
            return payload.read_bytes(), manifest

    def create(self, destination: Path, *, passphrase: str) -> Dict[str, object]:
        dest = Path(destination)
        if dest.suffix != ".latticebrain":
            dest = dest.with_suffix(".latticebrain")
        if not self.paths.db_path.exists():
            raise FileNotFoundError(f"Brain database not found: {self.paths.db_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload_bytes, manifest = self._payload_zip_bytes()
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = _derive_key(passphrase, salt)
        ciphertext = AESGCM(key).encrypt(nonce, payload_bytes, None)
        envelope = {
            "format": ARCHIVE_FORMAT,
            "format_version": ARCHIVE_VERSION,
            "created_at": _now(),
            "kdf": {
                "name": "PBKDF2HMAC-SHA256",
                "iterations": KDF_ITERATIONS,
                "salt": base64.b64encode(salt).decode("ascii"),
            },
            "cipher": {
                "name": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
            },
            "payload_sha256": _sha256_bytes(payload_bytes),
            "manifest_summary": {
                "format_version": manifest["format_version"],
                "created_at": manifest["created_at"],
                "sections": manifest["sections"],
                "storage": manifest.get("storage") or {},
                "device_identity": manifest.get("device_identity") or {},
            },
            "payload": base64.b64encode(ciphertext).decode("ascii"),
        }
        dest.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        return {
            "path": str(dest),
            "bytes": dest.stat().st_size,
            "encrypted": True,
            "format_version": ARCHIVE_VERSION,
            "manifest": {
                "sections": manifest["sections"],
                "storage": manifest.get("storage") or {},
                "entries": len(manifest["entries"]),
            },
        }

    def _load_envelope(self, source: Path) -> Dict[str, Any]:
        src = Path(source)
        if not src.exists():
            raise FileNotFoundError(f"Brain archive not found: {src}")
        try:
            envelope = json.loads(src.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Archive envelope is not valid JSON.") from exc
        if envelope.get("format") != ARCHIVE_FORMAT:
            raise ValueError("Not a .latticebrain encrypted archive.")
        version = int(envelope.get("format_version") or 0)
        if version < 1:
            raise ValueError("Archive format version is missing or invalid.")
        if version > ARCHIVE_VERSION:
            raise ValueError(
                f"Archive format version {version} is newer than supported version {ARCHIVE_VERSION}."
            )
        return envelope

    def inspect(self, source: Path, *, passphrase: Optional[str] = None) -> Dict[str, Any]:
        envelope = self._load_envelope(source)
        summary = {
            "valid_envelope": True,
            "encrypted": True,
            "format": envelope.get("format"),
            "format_version": envelope.get("format_version"),
            "created_at": envelope.get("created_at"),
            "cipher": (envelope.get("cipher") or {}).get("name"),
            "kdf": {
                "name": (envelope.get("kdf") or {}).get("name"),
                "iterations": (envelope.get("kdf") or {}).get("iterations"),
            },
            "manifest_summary": envelope.get("manifest_summary") or {},
        }
        if passphrase:
            verified = self.verify(source, passphrase=passphrase)
            summary["verified"] = verified["ok"]
            summary["manifest"] = verified.get("manifest")
            summary["errors"] = verified.get("errors", [])
        return summary

    def _decrypt_payload(self, envelope: Dict[str, Any], passphrase: str) -> bytes:
        try:
            salt = base64.b64decode(envelope["kdf"]["salt"])
            nonce = base64.b64decode(envelope["cipher"]["nonce"])
            ciphertext = base64.b64decode(envelope["payload"])
        except Exception as exc:
            raise ValueError("Archive envelope is missing encryption metadata.") from exc
        key = _derive_key(passphrase, salt)
        try:
            payload = AESGCM(key).decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise ValueError("Archive decryption failed; passphrase or archive data is invalid.") from exc
        expected = envelope.get("payload_sha256")
        if expected and _sha256_bytes(payload) != expected:
            raise ValueError("Archive payload integrity check failed (sha256 mismatch).")
        return payload

    def _read_payload(self, payload: bytes) -> tuple[Dict[str, Any], Dict[str, bytes]]:
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                bad_member = zf.testzip()
                if bad_member:
                    raise ValueError(f"Archive payload member is corrupt: {bad_member}")
                names = zf.namelist()
                for name in names:
                    _assert_safe_member(name)
                raw = {name: zf.read(name) for name in names if not name.endswith("/")}
        except zipfile.BadZipFile as exc:
            raise ValueError("Archive payload is not a valid ZIP file.") from exc
        if "manifest.json" in raw:
            manifest = json.loads(raw["manifest.json"].decode("utf-8"))
        else:
            manifest = {
                "format": "latticebrain.payload",
                "format_version": 1,
                "created_at": None,
                "sections": {
                    "graph": "knowledge_graph.sqlite" in raw,
                    "blobs": any(name.startswith("blobs/") for name in raw),
                    "workspace_state": False,
                    "signed_bundles": False,
                },
                "metadata": {},
                "storage": {},
                "device_identity": {},
                "provenance": {},
                "entries": [
                    {"path": name, "bytes": len(data), "sha256": _sha256_bytes(data)}
                    for name, data in raw.items()
                    if name != "manifest.json"
                ],
            }
        version = int(manifest.get("format_version") or 0)
        if version < 1:
            raise ValueError("Archive manifest format version is missing or invalid.")
        if version > ARCHIVE_VERSION:
            raise ValueError(
                f"Archive manifest version {version} is newer than supported version {ARCHIVE_VERSION}."
            )
        return manifest, raw

    def verify(self, source: Path, *, passphrase: str) -> Dict[str, Any]:
        try:
            envelope = self._load_envelope(source)
            payload = self._decrypt_payload(envelope, passphrase)
            manifest, raw = self._read_payload(payload)
            if "knowledge_graph.sqlite" not in raw:
                raise ValueError("Archive payload is missing knowledge_graph.sqlite.")
            missing: List[str] = []
            mismatched: List[str] = []
            for entry in manifest.get("entries") or []:
                name = str(entry.get("path") or "")
                if not name or name == "manifest.json":
                    continue
                data = raw.get(name)
                if data is None:
                    missing.append(name)
                    continue
                if entry.get("sha256") and _sha256_bytes(data) != entry["sha256"]:
                    mismatched.append(name)
            if missing or mismatched:
                raise ValueError(
                    "Archive manifest integrity check failed "
                    f"(missing={missing}, mismatched={mismatched})."
                )
            return {
                "ok": True,
                "encrypted": True,
                "format_version": envelope.get("format_version"),
                "manifest": manifest,
                "entries": len([name for name in raw if name != "manifest.json"]),
                "errors": [],
            }
        except (ValueError, FileNotFoundError) as exc:
            return {"ok": False, "encrypted": True, "errors": [str(exc)]}

    def restore(
        self,
        source: Path,
        *,
        passphrase: str,
        target: BrainArchivePaths,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> Dict[str, object]:
        if not dry_run and not confirm:
            raise ValueError("Explicit confirmation is required before restoring a .latticebrain archive.")
        envelope = self._load_envelope(source)
        payload = self._decrypt_payload(envelope, passphrase)
        manifest, raw = self._read_payload(payload)
        verified = self.verify(source, passphrase=passphrase)
        if not verified["ok"]:
            raise ValueError("; ".join(verified.get("errors") or ["Archive verification failed."]))
        planned = {
            "database": str(target.db_path),
            "blobs": str(target.blob_dir) if target.blob_dir else None,
            "data_dir": str(target.data_dir) if target.data_dir else None,
            "entries": len([name for name in raw if name != "manifest.json"]),
            "sections": manifest.get("sections") or {},
        }
        if dry_run:
            return {
                "restored": False,
                "dry_run": True,
                "verified": True,
                "planned": planned,
                "manifest": manifest,
            }
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            out = tmp / "out"
            out.mkdir()
            for name, data in raw.items():
                _assert_safe_member(name)
                dest = out / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            db_src = out / "knowledge_graph.sqlite"
            if not db_src.exists():
                raise ValueError("Archive payload is missing knowledge_graph.sqlite.")
            backup_dir = _pre_restore_backup_dir(target.db_path)
            try:
                _replace_sqlite_atomically(db_src, target.db_path, backup_dir)
                blobs_src = out / "blobs"
                if target.blob_dir:
                    _replace_tree_with_backup(blobs_src if blobs_src.exists() else None, target.blob_dir, backup_dir)
            except Exception:
                _rollback_sqlite_from_backup(target.db_path, backup_dir)
                raise
            if target.data_dir:
                data_src = out / "data"
                exports_src = out / "workspace_exports"
                target_data = Path(target.data_dir)
                target_data.mkdir(parents=True, exist_ok=True)
                if data_src.exists():
                    for file in sorted(data_src.rglob("*")):
                        if file.is_file():
                            rel = file.relative_to(data_src)
                            if rel.as_posix() not in PORTABLE_DATA_FILES:
                                continue
                            dest = target_data / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(file, dest)
                if exports_src.exists():
                    exports_dest = target_data / "workspace_exports"
                    exports_dest.mkdir(parents=True, exist_ok=True)
                    for file in sorted(exports_src.rglob("*")):
                        if file.is_file():
                            rel = file.relative_to(exports_src)
                            dest = exports_dest / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(file, dest)
        return {
            "restored": True,
            "dry_run": False,
            "path": str(target.db_path),
            "encrypted": True,
            "verified": True,
            "pre_restore_backup": str(backup_dir),
            "manifest": manifest,
        }


__all__ = ["BrainArchivePaths", "EncryptedBrainArchive"]
