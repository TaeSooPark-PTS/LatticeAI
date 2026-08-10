"""Sealing a bundle to a recipient's public key (v11.2.0).

Through 11.1.0 a shared subgraph was encrypted with a passphrase, which means
the sender and the receiver must first agree on a secret over some *other*
channel — and whatever that channel is, the passphrase is now in it. This
module removes that step: the receiver publishes an X25519 public key, the
sender seals a bundle to it, and only the holder of the matching private key
can open it. Nothing secret ever travels.

The construction is the standard sealed box, assembled from primitives
``cryptography`` already ships (it is a core dependency; nothing new is added):

1. the sender generates a **single-use ephemeral X25519 keypair**;
2. ``ephemeral_private × recipient_public`` yields a shared secret only the two
   of them can compute;
3. **HKDF-SHA256** stretches that secret into a 256-bit key, with both public
   keys mixed into ``info`` so a key derived for one recipient can never be
   reused for another;
4. **AES-256-GCM** encrypts the payload under it.

Forward secrecy comes free: the ephemeral private key is discarded the moment
the box is sealed, so a later compromise of the recipient's long-term key still
does not decrypt an already-sent bundle — nothing else in the envelope can
reconstruct the shared secret.

The sender's Ed25519 signature over the bundle header is **untouched** by all
of this. Signing says who wrote it; sealing says who may read it. Collapsing
the two into one keypair is the usual way to end up with neither.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: Named in the envelope so a future construction can be told apart from this
#: one instead of being silently mis-decrypted.
SEALED_BOX_ALGORITHM = "x25519-hkdf-sha256-aes256gcm"
#: Domain separation for the KDF — this key schedule is for subgraph bundles
#: and nothing else.
HKDF_INFO_PREFIX = b"latticeai.subgraph.sealed-box.v1"
KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16
#: Filename of the recipient key under the data directory (0600, like the
#: device identity's key file).
RECIPIENT_KEY_FILENAME = "subgraph_recipient.key"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padded = str(text) + "=" * (-len(str(text)) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _raw_public(key: X25519PublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def public_key_fingerprint(public_key_b64: str) -> str:
    """Human-comparable fingerprint of an X25519 public key.

    Raises ``ValueError`` when the input is not a usable key — callers use this
    as the validation gate before sealing anything to a pasted string.
    """
    try:
        raw = _unb64(public_key_b64)
        X25519PublicKey.from_public_bytes(raw)
    except Exception as exc:  # noqa: BLE001 — any parse failure is one answer
        raise ValueError(f"not a valid X25519 public key: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[i : i + 4] for i in range(0, 16, 4))


def _derive(shared: bytes, salt: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=salt,
        info=HKDF_INFO_PREFIX + b"|" + ephemeral + b"|" + recipient,
    ).derive(shared)


def seal(plaintext: bytes, *, recipient_public_key: str) -> Dict[str, Any]:
    """Encrypt ``plaintext`` so only the recipient's private key can open it.

    Returns the JSON-safe crypto block that goes in the bundle envelope. The
    recipient's own public key is echoed back into it so a receiver holding
    several keys can tell at a glance whether this box was meant for them —
    without trying to decrypt it, and without the sender having to be asked.
    """
    try:
        recipient_raw = _unb64(recipient_public_key)
        peer = X25519PublicKey.from_public_bytes(recipient_raw)
    except Exception as exc:  # noqa: BLE001 — a bad key is a caller error, said plainly
        raise ValueError(f"not a valid X25519 public key: {exc}") from exc
    ephemeral = X25519PrivateKey.generate()
    ephemeral_raw = _raw_public(ephemeral.public_key())
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    key = _derive(ephemeral.exchange(peer), salt, ephemeral_raw, recipient_raw)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return {
        "algorithm": SEALED_BOX_ALGORITHM,
        "ephemeral_public_key": _b64(ephemeral_raw),
        "recipient_public_key": _b64(recipient_raw),
        "recipient_fingerprint": public_key_fingerprint(recipient_public_key),
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
    }


def unseal(block: Dict[str, Any], private_key: X25519PrivateKey) -> bytes:
    """Open a sealed box with the recipient's private key.

    Fail-closed and specific: an unknown algorithm, a missing field, a box
    addressed to a different key, and a failed tag check each say which one
    happened rather than collapsing into "decryption failed".
    """
    algorithm = str((block or {}).get("algorithm") or "")
    if algorithm != SEALED_BOX_ALGORITHM:
        raise ValueError(
            f"unsupported sealed-box algorithm: {algorithm or 'missing'}"
        )
    try:
        ephemeral_raw = _unb64(block["ephemeral_public_key"])
        salt = _unb64(block["salt"])
        nonce = _unb64(block["nonce"])
        ciphertext = _unb64(block["ciphertext"])
        peer = X25519PublicKey.from_public_bytes(ephemeral_raw)
    except Exception as exc:  # noqa: BLE001 — a malformed envelope is one answer
        raise ValueError(f"sealed bundle is missing encryption metadata: {exc}") from exc
    recipient_raw = _raw_public(private_key.public_key())
    addressed = str(block.get("recipient_public_key") or "")
    if addressed and _unb64(addressed) != recipient_raw:
        raise ValueError(
            "this bundle was sealed to a different recipient key "
            f"({block.get('recipient_fingerprint') or 'unknown fingerprint'})."
        )
    key = _derive(private_key.exchange(peer), salt, ephemeral_raw, recipient_raw)
    try:
        return bytes(AESGCM(key).decrypt(nonce, ciphertext, None))
    except InvalidTag as exc:
        raise ValueError(
            "Sealed bundle decryption failed; the recipient key or the bundle is invalid."
        ) from exc


class RecipientIdentity:
    """This Brain's X25519 receiving keypair — loads or creates, file-backed.

    Deliberately *not* the device's Ed25519 identity. That key signs, and a
    signing key that also decrypts is a key whose compromise costs twice. The
    private half lives in a 0600 file under the data directory and never leaves
    the machine; :meth:`describe` reports exactly that rather than implying a
    keychain this class does not use.
    """

    def __init__(self, data_dir: Any) -> None:
        self._data_dir = Path(data_dir)
        self._key_file = self._data_dir / RECIPIENT_KEY_FILENAME
        self._private = self._load_or_create()

    def _load_or_create(self) -> X25519PrivateKey:
        if self._key_file.exists():
            return X25519PrivateKey.from_private_bytes(
                _unb64(self._key_file.read_text(encoding="utf-8").strip())
            )
        key = X25519PrivateKey.generate()
        raw = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._key_file.write_text(_b64(raw), encoding="utf-8")
        os.chmod(self._key_file, 0o600)
        return key

    @property
    def public_key_b64(self) -> str:
        return _b64(_raw_public(self._private.public_key()))

    @property
    def fingerprint(self) -> str:
        return public_key_fingerprint(self.public_key_b64)

    def describe(self) -> Dict[str, Any]:
        return {
            "public_key": self.public_key_b64,
            "fingerprint": self.fingerprint,
            "algorithm": SEALED_BOX_ALGORITHM,
            "storage": "file",
            "path": str(self._key_file),
        }

    def seal_to_self(self, plaintext: bytes) -> Dict[str, Any]:
        """Seal to this Brain's own key — the honest way to test a round trip."""
        return seal(plaintext, recipient_public_key=self.public_key_b64)

    def unseal(self, block: Dict[str, Any]) -> bytes:
        return unseal(block, self._private)


def load_recipient_identity(data_dir: Any) -> Optional[RecipientIdentity]:
    """Best-effort recipient key, or ``None`` when the directory is unusable.

    Sharing is opt-in and a missing key is a *state*, not a crash: the status
    surface reports "no receiving key" rather than failing a status read.
    """
    try:
        return RecipientIdentity(data_dir)
    except (OSError, ValueError):  # noqa: BLE001 — unwritable dir / corrupt key file
        return None


__all__ = [
    "HKDF_INFO_PREFIX",
    "RECIPIENT_KEY_FILENAME",
    "SEALED_BOX_ALGORITHM",
    "RecipientIdentity",
    "load_recipient_identity",
    "public_key_fingerprint",
    "seal",
    "unseal",
]
