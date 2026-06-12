"""Encrypted .latticebrain archive support."""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


ARCHIVE_FORMAT = "latticebrain.encrypted"
ARCHIVE_VERSION = 1
KDF_ITERATIONS = 390_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


@dataclass(frozen=True)
class BrainArchivePaths:
    db_path: Path
    blob_dir: Optional[Path] = None


class EncryptedBrainArchive:
    """Create and restore encrypted local Brain Core archives."""

    def __init__(self, paths: BrainArchivePaths) -> None:
        self.paths = paths

    def create(self, destination: Path, *, passphrase: str) -> Dict[str, object]:
        dest = Path(destination)
        if dest.suffix != ".latticebrain":
            dest = dest.with_suffix(".latticebrain")
        if not self.paths.db_path.exists():
            raise FileNotFoundError(f"Brain database not found: {self.paths.db_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            payload = tmp / "payload.zip"
            with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(self.paths.db_path, "knowledge_graph.sqlite")
                if self.paths.blob_dir and self.paths.blob_dir.exists():
                    for file in self.paths.blob_dir.rglob("*"):
                        if file.is_file():
                            zf.write(file, f"blobs/{file.relative_to(self.paths.blob_dir)}")
            salt = os.urandom(16)
            nonce = os.urandom(12)
            key = _derive_key(passphrase, salt)
            ciphertext = AESGCM(key).encrypt(nonce, payload.read_bytes(), None)
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
                "payload": base64.b64encode(ciphertext).decode("ascii"),
            }
            dest.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
        return {"path": str(dest), "bytes": dest.stat().st_size, "encrypted": True}

    def restore(self, source: Path, *, passphrase: str, target: BrainArchivePaths) -> Dict[str, object]:
        src = Path(source)
        if not src.exists():
            raise FileNotFoundError(f"Brain archive not found: {src}")
        envelope = json.loads(src.read_text(encoding="utf-8"))
        if envelope.get("format") != ARCHIVE_FORMAT:
            raise ValueError("Not a .latticebrain encrypted archive.")
        salt = base64.b64decode(envelope["kdf"]["salt"])
        nonce = base64.b64decode(envelope["cipher"]["nonce"])
        ciphertext = base64.b64decode(envelope["payload"])
        key = _derive_key(passphrase, salt)
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise ValueError("Archive decryption failed; passphrase or archive data is invalid.") from exc
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            payload = tmp / "payload.zip"
            payload.write_bytes(plaintext)
            with zipfile.ZipFile(payload) as zf:
                zf.extractall(tmp / "out")
            db_src = tmp / "out" / "knowledge_graph.sqlite"
            if not db_src.exists():
                raise ValueError("Archive payload is missing knowledge_graph.sqlite.")
            target.db_path.parent.mkdir(parents=True, exist_ok=True)
            for sibling in (target.db_path, Path(str(target.db_path) + "-wal"), Path(str(target.db_path) + "-shm")):
                if sibling.exists():
                    sibling.unlink()
            shutil.copyfile(db_src, target.db_path)
            blobs_src = tmp / "out" / "blobs"
            if target.blob_dir:
                if target.blob_dir.exists():
                    shutil.rmtree(target.blob_dir)
                if blobs_src.exists():
                    shutil.copytree(blobs_src, target.blob_dir)
                else:
                    target.blob_dir.mkdir(parents=True, exist_ok=True)
        return {"restored": True, "path": str(target.db_path), "encrypted": True}


__all__ = ["BrainArchivePaths", "EncryptedBrainArchive"]
