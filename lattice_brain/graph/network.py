"""Brain Network v1 — knowledge exchange between paired Lattice instances.

Local-first federation: no cloud rendezvous, no relay. A peer is another
Lattice installation you deliberately paired with by exchanging device
public keys (LAN/tailnet HTTP). Exchange is per-workspace, per-request,
owner-initiated: a signed export bundle is pushed to (or received from) a
paired peer, verified against the *paired* key, imported through the normal
import path, and stamped with origin-device provenance.

Peer requests authenticate independently of user sessions: each carries an
Ed25519 signature over (body sha256 + timestamp + nonce), with a freshness
window and a seen-nonce set for replay protection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .identity import DeviceIdentity, fingerprint_of, verify_signature

PEER_AUTH_WINDOW_SECONDS = 300
_NONCE_CACHE_MAX = 4096

HEADER_DEVICE = "x-lattice-device"
HEADER_TIMESTAMP = "x-lattice-timestamp"
HEADER_NONCE = "x-lattice-nonce"
HEADER_SIGNATURE = "x-lattice-signature"


def _signing_payload(body: bytes, timestamp: str, nonce: str) -> bytes:
    body_digest = hashlib.sha256(body or b"").hexdigest()
    return f"{body_digest}|{timestamp}|{nonce}".encode("ascii")


class BrainNetwork:
    """Peer registry + signed bundle exchange."""

    def __init__(
        self,
        *,
        identity: DeviceIdentity,
        portability: Any,
        data_dir: Path,
        http_client_factory: Any = None,
    ) -> None:
        self._identity = identity
        self._portability = portability
        self._peers_file = Path(data_dir) / "brain_peers.json"
        self._lock = threading.Lock()
        self._seen_nonces: Dict[str, float] = {}
        # injectable for tests; default builds an httpx client per call
        self._http_client_factory = http_client_factory

    # ── peer registry (deliberate pairing) ─────────────────────────────────
    def _load_peers(self) -> List[Dict[str, Any]]:
        if not self._peers_file.exists():
            return []
        try:
            return json.loads(self._peers_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.warning("brain network: peer registry unreadable: %s", exc)
            return []

    def _save_peers(self, peers: List[Dict[str, Any]]) -> None:
        self._peers_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._peers_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._peers_file)

    def list_peers(self) -> List[Dict[str, Any]]:
        return self._load_peers()

    def add_peer(self, *, name: str, base_url: str, public_key: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        base_url = str(base_url or "").strip().rstrip("/")
        public_key = str(public_key or "").strip()
        if not name or not base_url or not public_key:
            raise ValueError("pairing requires name, base_url, and the peer's public key")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an http(s) URL")
        try:
            fingerprint = fingerprint_of(public_key)
        except Exception as exc:
            raise ValueError(f"public_key is not a valid Ed25519 key: {exc}") from exc
        with self._lock:
            peers = self._load_peers()
            if any(p.get("public_key") == public_key for p in peers):
                raise ValueError("this device is already paired")
            peer = {
                "id": f"peer-{uuid.uuid4().hex[:12]}",
                "name": name,
                "base_url": base_url,
                "public_key": public_key,
                "fingerprint": fingerprint,
                "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            peers.append(peer)
            self._save_peers(peers)
        return peer

    def remove_peer(self, peer_id: str) -> Dict[str, Any]:
        with self._lock:
            peers = self._load_peers()
            kept = [p for p in peers if p.get("id") != peer_id]
            if len(kept) == len(peers):
                raise FileNotFoundError(peer_id)
            self._save_peers(kept)
        return {"status": "removed", "peer_id": peer_id}

    def _peer_by_id(self, peer_id: str) -> Dict[str, Any]:
        peer = next((p for p in self._load_peers() if p.get("id") == peer_id), None)
        if peer is None:
            raise FileNotFoundError(peer_id)
        return peer

    # ── request authentication (peer → this brain) ────────────────────────
    def auth_headers(self, body: bytes) -> Dict[str, str]:
        """Headers this device attaches when pushing to a peer."""
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        return {
            HEADER_DEVICE: self._identity.public_key_b64,
            HEADER_TIMESTAMP: timestamp,
            HEADER_NONCE: nonce,
            HEADER_SIGNATURE: self._identity.sign(_signing_payload(body, timestamp, nonce)),
        }

    def verify_peer_request(self, headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
        """Authenticate an inbound peer request. Raises PermissionError."""
        lowered = {str(k).lower(): v for k, v in headers.items()}
        device = lowered.get(HEADER_DEVICE) or ""
        timestamp = lowered.get(HEADER_TIMESTAMP) or ""
        nonce = lowered.get(HEADER_NONCE) or ""
        signature = lowered.get(HEADER_SIGNATURE) or ""
        if not device or not timestamp or not nonce or not signature:
            raise PermissionError("missing peer authentication headers")
        peer = next((p for p in self._load_peers() if p.get("public_key") == device), None)
        if peer is None:
            raise PermissionError("device is not a paired peer")
        try:
            age = abs(time.time() - int(timestamp))
        except ValueError:
            raise PermissionError("invalid timestamp")
        if age > PEER_AUTH_WINDOW_SECONDS:
            raise PermissionError("request outside the freshness window")
        with self._lock:
            if nonce in self._seen_nonces:
                raise PermissionError("replayed nonce")
            self._seen_nonces[nonce] = time.time()
            if len(self._seen_nonces) > _NONCE_CACHE_MAX:
                cutoff = time.time() - PEER_AUTH_WINDOW_SECONDS * 2
                self._seen_nonces = {n: t for n, t in self._seen_nonces.items() if t > cutoff}
        if not verify_signature(device, _signing_payload(body, timestamp, nonce), signature):
            raise PermissionError("peer request signature invalid")
        return peer

    # ── exchange ────────────────────────────────────────────────────────────
    def push_to_peer(self, peer_id: str, *, workspace_id: Optional[str] = None, timeout: float = 30.0) -> Dict[str, Any]:
        """Owner-initiated: export (signed) and push to one paired peer."""
        peer = self._peer_by_id(peer_id)
        artifact = self._portability.export(workspace_id=workspace_id)
        body = json.dumps(artifact, ensure_ascii=False).encode("utf-8")
        headers = {**self.auth_headers(body), "Content-Type": "application/json"}
        url = f"{peer['base_url']}/network/receive"
        if self._http_client_factory is not None:
            response = self._http_client_factory().post(url, content=body, headers=headers, timeout=timeout)
        else:
            import httpx

            with httpx.Client() as client:
                response = client.post(url, content=body, headers=headers, timeout=timeout)
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        return {
            "status": "ok" if response.status_code == 200 else "failed",
            "http_status": response.status_code,
            "peer": {"id": peer["id"], "name": peer["name"], "fingerprint": peer["fingerprint"]},
            "peer_result": payload,
            "counts": (artifact.get("header") or {}).get("counts"),
        }

    def receive(self, headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
        """Inbound: authenticate the peer, verify the bundle, import."""
        peer = self.verify_peer_request(headers, body)
        try:
            artifact = json.loads(body.decode("utf-8"))
        except Exception:
            raise ValueError("body is not a JSON bundle")
        signature = artifact.get("signature") or {}
        # On the network path the bundle itself MUST be signed by the paired
        # peer too (unsigned-legacy applies to local file imports only).
        if signature.get("public_key") != peer.get("public_key"):
            raise PermissionError("bundle signer does not match the paired peer")
        result = self._portability.import_data(artifact, mode="merge")
        result["peer"] = {"id": peer["id"], "name": peer["name"], "fingerprint": peer["fingerprint"]}
        return result


__all__ = ["BrainNetwork", "PEER_AUTH_WINDOW_SECONDS"]
