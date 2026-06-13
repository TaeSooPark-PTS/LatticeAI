"""Device identity — the sovereignty primitive.

Every Lattice installation owns an Ed25519 keypair. Exports are signed by
it, peers pair against its public key, and imported knowledge records which
device it came from. The private key never leaves the machine: it lives in
the OS keyring when one is available, otherwise in a 0600 file under the
data directory (the storage backend is reported honestly).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_KEYRING_SERVICE = "lattice-ai-device-identity"
_KEYRING_ENTRY = "ed25519-private-key"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _keyring_opt_in() -> bool:
    """Keyring storage is opt-in (LATTICEAI_DEVICE_KEY_KEYRING=1).

    OS keychain access can block or prompt during startup/tests; the default
    is a 0600 file under the data dir, and ``describe()`` reports which
    backend holds the key — no silent security theater either way.
    """
    return os.getenv("LATTICEAI_DEVICE_KEY_KEYRING", "").strip() in {"1", "true", "yes"}


class DeviceIdentity:
    """Loads-or-creates the installation's Ed25519 keypair."""

    def __init__(self, data_dir: Path, *, use_keyring: Optional[bool] = None):
        if use_keyring is None:
            use_keyring = _keyring_opt_in()
        self._data_dir = Path(data_dir)
        self._key_file = self._data_dir / "device_identity.key"
        self._private: Ed25519PrivateKey
        self.storage: str  # "keyring" | "file"
        self._load_or_create(use_keyring)

    # ── key material ───────────────────────────────────────────────────────
    def _load_or_create(self, use_keyring: bool) -> None:
        raw: Optional[bytes] = None
        backend = "file"
        if use_keyring:
            try:
                import keyring

                stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_ENTRY)
                if stored:
                    raw = _unb64(stored)
                    backend = "keyring"
            except Exception as exc:
                logging.debug("device identity: keyring unavailable (%s)", exc)
        if raw is None and self._key_file.exists():
            raw = _unb64(self._key_file.read_text().strip())
            backend = "file"
        if raw is None:
            key = Ed25519PrivateKey.generate()
            raw = key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            backend = self._persist_new(raw, use_keyring)
        self._private = Ed25519PrivateKey.from_private_bytes(raw)
        self.storage = backend

    def _persist_new(self, raw: bytes, use_keyring: bool) -> str:
        if use_keyring:
            try:
                import keyring

                keyring.set_password(_KEYRING_SERVICE, _KEYRING_ENTRY, _b64(raw))
                return "keyring"
            except Exception as exc:
                logging.debug("device identity: keyring store failed (%s); using file", exc)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._key_file.write_text(_b64(raw))
        os.chmod(self._key_file, 0o600)
        return "file"

    # ── public surface ─────────────────────────────────────────────────────
    @property
    def public_key_b64(self) -> str:
        return _b64(
            self._private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        )

    @property
    def fingerprint(self) -> str:
        """Short human-comparable id: sha256 of the raw public key."""
        raw = self._private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        digest = hashlib.sha256(raw).hexdigest()
        return ":".join(digest[i : i + 4] for i in range(0, 16, 4))

    def describe(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "public_key": self.public_key_b64,
            "algorithm": "ed25519",
            "storage": self.storage,
        }

    # ── signing ────────────────────────────────────────────────────────────
    def sign(self, payload: bytes) -> str:
        return _b64(self._private.sign(payload))

    def sign_manifest(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """Detached signature over the canonical JSON of a manifest."""
        canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return {
            "algorithm": "ed25519",
            "public_key": self.public_key_b64,
            "fingerprint": self.fingerprint,
            "signature": self.sign(canonical),
        }


def fingerprint_of(public_key_b64: str) -> str:
    """Human-comparable fingerprint of an Ed25519 public key.

    Raises ValueError when the input is not a valid key — the pairing flow
    uses this as its validation gate.
    """
    raw = _unb64(public_key_b64)
    Ed25519PublicKey.from_public_bytes(raw)  # validates; raises on garbage
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[i : i + 4] for i in range(0, 16, 4))


def verify_signature(public_key_b64: str, payload: bytes, signature_b64: str) -> bool:
    """True iff ``signature`` is valid for ``payload`` under the given key."""
    try:
        key = Ed25519PublicKey.from_public_bytes(_unb64(public_key_b64))
        key.verify(_unb64(signature_b64), payload)
        return True
    except Exception:
        return False


def verify_manifest(manifest: Dict[str, Any], signature_block: Dict[str, Any]) -> bool:
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return verify_signature(
        str(signature_block.get("public_key") or ""),
        canonical,
        str(signature_block.get("signature") or ""),
    )


__all__ = ["DeviceIdentity", "verify_signature", "verify_manifest"]
