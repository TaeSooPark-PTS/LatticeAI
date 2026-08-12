"""Logical export/import, and the opt-in Brain Network subgraph share.

The half of :class:`~lattice_brain.portability.KGPortabilityService` that moves
*knowledge* rather than *files*: a whole-graph JSON artifact, and — behind the
``LATTICEAI_BRAIN_NETWORK`` opt-in — a signed, encrypted slice of the graph that
arrives at the far end as review proposals, never as a merge.

The receiving X25519 key is loaded here (``load_recipient_identity``), so a test
standing in for it patches *this* module rather than the package.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..archive import KDF_ITERATIONS, _derive_key
from ..quiet import quiet
from ..sealed_box import (
    SEALED_BOX_ALGORITHM,
    load_recipient_identity,
    public_key_fingerprint,
    seal,
)
from ..utils import utc_now_iso
from ._contract import PortabilityCore as _Core
from .bundles import (
    _canonical_digest,
    _expand_neighbors,
    _redact_node,
    _redact_provenance_row,
    _scope_node,
    _select_node_ids,
    _shared_node_summary,
)
from .constants import (
    _OPEN_REVIEW_STATUSES,
    _PAYLOAD_KEYS,
    _REDACTED_FIELDS,
    BRAIN_NETWORK_DISABLED_DETAIL,
    BRAIN_NETWORK_ENV,
    BRAIN_NETWORK_GATE,
    ENCRYPTION_MODES,
    FORMAT,
    FORMAT_VERSION,
    SUBGRAPH_ARCHIVE_FORMAT,
    SUBGRAPH_FORMAT,
    SUBGRAPH_FORMAT_VERSION,
    SUBGRAPH_PROPOSAL_CAP,
    SUBGRAPH_REVIEW_KIND,
    SUBGRAPH_REVIEW_SOURCE,
    _stamp,
    brain_network_enabled,
    require_brain_network,
)


class KGPortabilitySharingMixin(_Core):
    """Export / import / share. Mixed into ``KGPortabilityService``."""

    def _recipient_identity(self) -> Any:
        """This Brain's X25519 receiving key, created on first use.

        ``None`` when the data directory cannot hold one — reported as a state
        by :meth:`recipient_key`, never raised into a status read.
        """
        if not self._recipient_loaded:
            self._recipient = load_recipient_identity(self._data_dir)
            self._recipient_loaded = True
        return self._recipient

    def recipient_key(self) -> Dict[str, Any]:
        """The public half to hand a sender, so they can seal a bundle to us.

        Publishing this is safe by construction: a public key encrypts, it does
        not decrypt, and it says nothing about what this Brain contains.
        """
        require_brain_network()
        identity = self._recipient_identity()
        if identity is None:
            return {
                "available": False,
                "detail": (
                    "a receiving key could not be created in the data directory; "
                    "sealed bundles cannot be opened on this machine"
                ),
            }
        return {"available": True, **identity.describe()}

    # ── logical export / import ──────────────────────────────────────────────
    def export(
        self,
        *,
        workspace_id: Optional[str] = None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        self._require()
        data = self._kg.export_graph_data(
            workspace_id=workspace_id,
            include_legacy_global=include_legacy_global,
        )
        header = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            **self._kg.schema_versions(),
            "exported_at": utc_now_iso(),
            "workspace_id": workspace_id,
            "include_legacy_global": bool(include_legacy_global),
            "counts": data.get("counts"),
        }
        artifact = {"header": header, **data}
        if self._identity is not None:
            artifact["signature"] = self._identity.sign_manifest(header)
        return artifact

    def export_to_file(
        self,
        path=None,
        *,
        workspace_id: Optional[str] = None,
        include_legacy_global: bool = False,
    ) -> Dict[str, Any]:
        artifact = self.export(
            workspace_id=workspace_id,
            include_legacy_global=include_legacy_global,
        )
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
            from ..graph.identity import verify_manifest

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
                    node_id="import:" + str((artifact.get("header") or {}).get("exported_at") or utc_now_iso()),
                    source_type="bundle_import",
                    pipeline="kg-portability",
                    owner=None,
                    metadata={"origin": origin, "mode": mode,
                              "counts": (artifact.get("header") or {}).get("counts")},
                )
            except Exception:
                quiet()
        return result

    # ── selective subgraph share (opt-in prototype, off by default) ──────────
    def share_status(self) -> Dict[str, Any]:
        """Honest read of the share surface. Safe to call while it is off.

        A status route that refuses while disabled cannot tell a UI *why* the
        feature is missing, so this one always answers — with ``enabled:
        false`` and the flag that turns it on.
        """
        enabled = brain_network_enabled()
        return {
            "enabled": enabled,
            "flag": BRAIN_NETWORK_ENV,
            "format": SUBGRAPH_FORMAT,
            "format_version": SUBGRAPH_FORMAT_VERSION,
            "graph_available": self.available(),
            "signing": self._identity is not None,
            "device": self._share_device(),
            "proposal_cap": SUBGRAPH_PROPOSAL_CAP,
            # Stated rather than implied: a bundle is always signed by this
            # device, and encrypted either with a shared passphrase or — since
            # 11.2.0 — to the recipient's own public key, so nothing secret has
            # to travel ahead of it.
            "encryption": list(ENCRYPTION_MODES),
            "recipient_public_key_encryption": True,
            "sealed_box_algorithm": SEALED_BOX_ALGORITHM,
            "gate": BRAIN_NETWORK_GATE.describe(),
            "detail": None if enabled else BRAIN_NETWORK_DISABLED_DETAIL,
        }

    def _share_device(self) -> Dict[str, Any]:
        """Signer identity for a bundle — key material only, no storage hints."""
        if self._identity is None:
            return {}
        described = self._identity.describe()
        return {
            "fingerprint": described.get("fingerprint"),
            "public_key": described.get("public_key"),
            "algorithm": described.get("algorithm"),
        }

    def export_subgraph(
        self,
        *,
        node_ids: Optional[Iterable[str]] = None,
        node_types: Optional[Iterable[str]] = None,
        source_types: Optional[Iterable[str]] = None,
        workspace_id: Optional[str] = None,
        include_legacy_global: bool = False,
        include_neighbors: bool = False,
        redact_provenance: bool = True,
    ) -> Dict[str, Any]:
        """A chosen slice of the graph, with provenance, signed by this device.

        At least one selector is required. "Share everything" already exists as
        :meth:`export`; a *selective* door that silently defaults to the whole
        Brain is how an accident becomes a leak.

        ``include_neighbors`` widens the selection by one hop (both directions)
        so "share this decision" can carry the decision's immediate relations.
        ``redact_provenance`` (default on) strips the sender's local identity —
        owner, source URI, permissions — from both node metadata and the
        provenance rows, and records that it did so in the header.

        Knowledge-source rows (connected folder registrations, with their local
        paths) are never included; they describe the sender's machine, not the
        knowledge.
        """
        require_brain_network()
        self._require()
        if self._identity is None:
            raise RuntimeError(
                "A device identity is required to share a subgraph; "
                "an unsigned bundle cannot be attributed and would be refused."
            )
        wanted_ids = sorted({str(v) for v in (node_ids or []) if str(v).strip()})
        wanted_types = sorted({str(v).strip().lower() for v in (node_types or []) if str(v).strip()})
        wanted_sources = sorted({str(v).strip().lower() for v in (source_types or []) if str(v).strip()})
        selection: Dict[str, Any] = {
            "node_ids": wanted_ids,
            "node_types": wanted_types,
            "source_types": wanted_sources,
            "include_neighbors": bool(include_neighbors),
            "workspace_id": workspace_id,
        }
        if not (wanted_ids or wanted_types or wanted_sources):
            raise ValueError(
                "A subgraph share must name what to share "
                "(node_ids, node_types, or source_types)."
            )
        data = self._kg.export_graph_data(
            workspace_id=workspace_id, include_legacy_global=include_legacy_global,
        )
        all_nodes = data.get("nodes") or []
        # Chunks are derived retrieval units, not shareable subjects: they are
        # never selectable, but they travel with the node they belong to so the
        # receiver can actually search the shared content rather than a summary.
        nodes = [n for n in all_nodes if str(n.get("type")) != "Chunk"]
        chunk_nodes_by_id = {
            str(n.get("id")): n for n in all_nodes if str(n.get("type")) == "Chunk"
        }
        edges = list(data.get("edges") or [])
        keep = _select_node_ids(nodes, selection)
        present = {str(n.get("id")): str(n.get("type") or "") for n in nodes}
        missing = [nid for nid in wanted_ids if nid not in present]
        if include_neighbors:
            keep = _expand_neighbors(keep, edges, present)
        if not keep:
            raise ValueError("The selection matched no nodes; nothing was exported.")
        kept_nodes = [n for n in nodes if str(n.get("id")) in keep]
        kept_chunks = [c for c in data.get("chunks") or [] if str(c.get("source_node")) in keep]
        kept_chunk_nodes = [
            chunk_nodes_by_id[str(c.get("id"))]
            for c in kept_chunks
            if str(c.get("id")) in chunk_nodes_by_id
        ]
        carried = keep | {str(n.get("id")) for n in kept_chunk_nodes}
        kept_edges = [
            e for e in edges
            if str(e.get("from_node")) in carried and str(e.get("to_node")) in carried
        ]
        kept_prov = [p for p in data.get("provenance") or [] if str(p.get("node_id")) in keep]
        if redact_provenance:
            kept_nodes = [_redact_node(n) for n in kept_nodes]
            kept_chunk_nodes = [_redact_node(n) for n in kept_chunk_nodes]
            kept_prov = [_redact_provenance_row(p) for p in kept_prov]
        payload = {
            "nodes": kept_nodes,
            "chunk_nodes": kept_chunk_nodes,
            "edges": kept_edges,
            "chunks": kept_chunks,
            "knowledge_sources": [],
            "provenance": kept_prov,
        }
        counts = {key: len(value) for key, value in payload.items()}
        header = {
            "format": SUBGRAPH_FORMAT,
            "format_version": SUBGRAPH_FORMAT_VERSION,
            **self._kg.schema_versions(),
            "exported_at": utc_now_iso(),
            "workspace_id": workspace_id,
            "selection": selection,
            "counts": counts,
            "unmatched_node_ids": missing,
            "redacted": sorted(_REDACTED_FIELDS) if redact_provenance else [],
            "includes_knowledge_sources": False,
            "device": self._share_device(),
            # The signature covers the header; the header pins the payload.
            "payload_sha256": _canonical_digest(payload),
        }
        return {
            "header": header,
            **payload,
            "signature": self._identity.sign_manifest(header),
        }

    def export_subgraph_archive(
        self,
        dest_path=None,
        *,
        passphrase: Optional[str] = None,
        recipient_public_key: Optional[str] = None,
        **selection: Any,
    ) -> Dict[str, Any]:
        """Write the signed subgraph as an encrypted ``.latticebrain`` file.

        Exactly one recipient mechanism, named explicitly:

        * ``passphrase`` — the 11.1.0 mechanism, identical to the whole-Brain
          archive (PBKDF2-SHA256 → AES-256-GCM). Simple, and it means a secret
          has to reach the receiver through some other channel first.
        * ``recipient_public_key`` — an X25519 sealed box (11.2.0). The
          receiver publishes a public key; nothing secret travels at all.

        Supplying both is refused rather than silently preferring one: which
        key opens a bundle is not a detail to guess at. A different ``format``
        from the whole-Brain archive keeps the two from ever being confused,
        and the header and signature stay *outside* the ciphertext on purpose —
        a recipient can see who sent a bundle, and how big it is, before
        deciding to open it.
        """
        mode = self._encryption_mode(passphrase, recipient_public_key)
        artifact = self.export_subgraph(**selection)
        self._exports_dir.mkdir(parents=True, exist_ok=True)
        dest = Path(dest_path) if dest_path else self._exports_dir / f"subgraph-{_stamp()}.latticebrain"
        if dest.suffix != ".latticebrain":
            dest = dest.with_suffix(".latticebrain")
        body = {key: value for key, value in artifact.items() if key not in {"header", "signature"}}
        plaintext = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        envelope: Dict[str, Any] = {
            "format": SUBGRAPH_ARCHIVE_FORMAT,
            "format_version": SUBGRAPH_FORMAT_VERSION,
            "created_at": utc_now_iso(),
            "encryption": mode,
            "header": artifact["header"],
            "signature": artifact["signature"],
        }
        recipient: Optional[str] = None
        if mode == "recipient_public_key":
            sealed = seal(plaintext, recipient_public_key=str(recipient_public_key))
            recipient = str(sealed["recipient_fingerprint"])
            envelope["sealed_box"] = sealed
        else:
            salt = os.urandom(16)
            nonce = os.urandom(12)
            ciphertext = AESGCM(_derive_key(str(passphrase), salt)).encrypt(nonce, plaintext, None)
            envelope["kdf"] = {
                "name": "PBKDF2HMAC-SHA256",
                "iterations": KDF_ITERATIONS,
                "salt": base64.b64encode(salt).decode("ascii"),
            }
            envelope["cipher"] = {
                "name": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
            }
            envelope["payload"] = base64.b64encode(ciphertext).decode("ascii")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "path": str(dest),
            "bytes": dest.stat().st_size,
            "encrypted": True,
            "encryption": mode,
            "recipient_fingerprint": recipient,
            "format": SUBGRAPH_ARCHIVE_FORMAT,
            "header": artifact["header"],
            "counts": artifact["header"]["counts"],
        }

    @staticmethod
    def _encryption_mode(
        passphrase: Optional[str], recipient_public_key: Optional[str]
    ) -> str:
        """``passphrase`` | ``recipient_public_key`` — exactly one, validated."""
        has_passphrase = bool(str(passphrase or "").strip())
        wanted_key = str(recipient_public_key or "").strip()
        if has_passphrase and wanted_key:
            raise ValueError(
                "Choose one: a passphrase, or a recipient public key. "
                "A bundle sealed both ways would hide which key really opens it."
            )
        if wanted_key:
            public_key_fingerprint(wanted_key)  # validates; raises on garbage
            return "recipient_public_key"
        if has_passphrase:
            return "passphrase"
        raise ValueError(
            "A subgraph bundle must be encrypted: supply a passphrase or the "
            "recipient's public key."
        )

    def read_subgraph_archive(
        self, path, *, passphrase: Optional[str] = None
    ) -> Dict[str, Any]:
        """Decrypt an encrypted subgraph bundle back into a signed artifact.

        The envelope says how it was sealed, so the reader never has to guess:
        a passphrase bundle needs the passphrase, a sealed-box bundle needs
        this Brain's own receiving key and no passphrase at all.
        """
        require_brain_network()
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"Subgraph bundle not found: {source}")
        try:
            envelope = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Subgraph bundle is not valid JSON.") from exc
        if envelope.get("format") != SUBGRAPH_ARCHIVE_FORMAT:
            raise ValueError("Not a .latticebrain subgraph bundle.")
        if envelope.get("encryption") == "recipient_public_key":
            plaintext = self._unseal_bundle(envelope)
        else:
            plaintext = self._decrypt_with_passphrase(envelope, passphrase)
        body = json.loads(plaintext.decode("utf-8"))
        return {
            "header": envelope.get("header") or {},
            **body,
            "signature": envelope.get("signature") or {},
        }

    def _unseal_bundle(self, envelope: Dict[str, Any]) -> bytes:
        """Open a sealed box with this Brain's receiving key, or say why not."""
        identity = self._recipient_identity()
        if identity is None:
            raise ValueError(
                "This bundle is sealed to a public key, but no receiving key "
                "could be loaded on this machine."
            )
        block = envelope.get("sealed_box")
        if not isinstance(block, dict):
            raise ValueError("Subgraph bundle is missing encryption metadata.")
        return bytes(identity.unseal(block))

    @staticmethod
    def _decrypt_with_passphrase(
        envelope: Dict[str, Any], passphrase: Optional[str]
    ) -> bytes:
        if not str(passphrase or "").strip():
            raise ValueError("This bundle is passphrase-encrypted; a passphrase is required.")
        try:
            salt = base64.b64decode(envelope["kdf"]["salt"])
            nonce = base64.b64decode(envelope["cipher"]["nonce"])
            ciphertext = base64.b64decode(envelope["payload"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Subgraph bundle is missing encryption metadata.") from exc
        try:
            return bytes(
                AESGCM(_derive_key(str(passphrase), salt)).decrypt(nonce, ciphertext, None)
            )
        except InvalidTag as exc:
            raise ValueError(
                "Subgraph decryption failed; the passphrase or the bundle is invalid."
            ) from exc

    def verify_subgraph(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        """Signature + payload-digest verdict for a received bundle.

        Fail-closed and specific: the caller learns *which* check failed, and
        an unsigned bundle is a failure here (unlike a local file import, where
        pre-v4 unsigned artifacts stay importable).
        """
        errors: List[str] = []
        header = artifact.get("header") if isinstance(artifact, dict) else None
        header = header if isinstance(header, dict) else {}
        signature = artifact.get("signature") if isinstance(artifact, dict) else None
        signature = signature if isinstance(signature, dict) else {}
        if header.get("format") != SUBGRAPH_FORMAT:
            errors.append("not a Lattice subgraph bundle")
        incoming_version = int(header.get("format_version") or 0)
        if incoming_version > SUBGRAPH_FORMAT_VERSION:
            errors.append(
                f"bundle format version {incoming_version} is newer than this build "
                f"({SUBGRAPH_FORMAT_VERSION})"
            )
        if not signature.get("signature"):
            errors.append("bundle is unsigned")
        else:
            from ..graph.identity import verify_manifest

            if not verify_manifest(header, signature):
                errors.append("signature does not match the bundle header")
        expected = header.get("payload_sha256")
        if expected:
            payload = {key: artifact.get(key) or [] for key in _PAYLOAD_KEYS}
            if _canonical_digest(payload) != expected:
                errors.append("bundle contents do not match the signed digest")
        else:
            errors.append("bundle header does not pin its payload digest")
        fingerprint = str(signature.get("fingerprint") or "") or None
        return {
            "ok": not errors,
            "errors": errors,
            "fingerprint": fingerprint,
            "origin": f"device:{fingerprint}" if fingerprint and not errors else None,
            "exported_at": header.get("exported_at"),
            "counts": header.get("counts") or {},
        }

    def import_subgraph_proposals(
        self,
        artifact: Dict[str, Any],
        *,
        review_sink: Any,
        workspace_id: Optional[str] = None,
        user_email: Optional[str] = None,
        dry_run: bool = False,
        cap: int = SUBGRAPH_PROPOSAL_CAP,
    ) -> Dict[str, Any]:
        """Receive a subgraph as *proposals*, never as a merge.

        Every node becomes one review item carrying its edges, chunks, and
        provenance, stamped with the sending device's fingerprint and the
        signature verdict. Nothing reaches the graph until a human accepts an
        item through :meth:`accept_subgraph_proposal`.
        """
        require_brain_network()
        self._require()
        verdict = self.verify_subgraph(artifact)
        if not verdict["ok"]:
            raise ValueError(
                "Refusing the bundle: " + "; ".join(verdict["errors"])
            )
        header = dict(artifact.get("header") or {})
        nodes = [n for n in artifact.get("nodes") or [] if n.get("id")]
        edges = list(artifact.get("edges") or [])
        chunks = list(artifact.get("chunks") or [])
        chunk_nodes = {str(n.get("id")): n for n in artifact.get("chunk_nodes") or []}
        provenance = list(artifact.get("provenance") or [])
        cap = max(1, int(cap))
        selected = nodes[:cap]
        summary: Dict[str, Any] = {
            "status": "dry_run" if dry_run else "proposed",
            "origin": verdict["origin"],
            "fingerprint": verdict["fingerprint"],
            "signed": True,
            "signature_verified": True,
            "nodes": len(nodes),
            "edges": len(edges),
            "chunks": len(chunks),
            "proposed": len(selected),
            "skipped": len(nodes) - len(selected),
            "capped": len(nodes) > len(selected),
            "exported_at": header.get("exported_at"),
            "items": [],
        }
        if dry_run:
            return summary
        for node in selected:
            node_id = str(node.get("id"))
            node_chunks = [c for c in chunks if str(c.get("source_node")) == node_id]
            carried = {node_id} | {str(c.get("id")) for c in node_chunks}
            item = review_sink.create(
                title=str(node.get("title") or node_id)[:200],
                summary=_shared_node_summary(node, verdict),
                source=SUBGRAPH_REVIEW_SOURCE,
                kind=SUBGRAPH_REVIEW_KIND,
                payload={
                    "kind": SUBGRAPH_REVIEW_KIND,
                    "node": node,
                    "chunk_nodes": [
                        chunk_nodes[str(c.get("id"))]
                        for c in node_chunks
                        if str(c.get("id")) in chunk_nodes
                    ],
                    "edges": [
                        e for e in edges
                        if str(e.get("from_node")) in carried or str(e.get("to_node")) in carried
                    ],
                    "chunks": node_chunks,
                    "provenance": [p for p in provenance if str(p.get("node_id")) == node_id],
                    "graph_schema_version": header.get("graph_schema_version"),
                },
                provenance={
                    "origin": verdict["origin"],
                    "fingerprint": verdict["fingerprint"],
                    "public_key": (artifact.get("signature") or {}).get("public_key"),
                    "signature_verified": True,
                    "exported_at": header.get("exported_at"),
                    "source_detail": "brain_network_subgraph",
                    "redacted": header.get("redacted") or [],
                },
                user_email=user_email,
                workspace_id=workspace_id,
            )
            summary["items"].append(item.get("id"))
        try:
            self._kg.record_provenance(
                node_id="subgraph-proposal:" + str(header.get("exported_at") or utc_now_iso()),
                source_type="subgraph_share",
                pipeline="kg-portability",
                owner=user_email,
                workspace_id=workspace_id,
                metadata={
                    "origin": verdict["origin"],
                    "proposed": len(selected),
                    "counts": header.get("counts"),
                },
            )
        except Exception:  # noqa: BLE001 — the proposals already landed
            quiet()
        return summary

    def accept_subgraph_proposal(
        self,
        item_id: str,
        *,
        review_sink: Any,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Merge one accepted proposal into this Brain and approve the item.

        Only the node the reviewer looked at is written. An edge whose other
        endpoint is not (yet) in this Brain is *deferred* and reported, because
        writing a dangling relation would invent a connection the receiver
        cannot see.
        """
        require_brain_network()
        self._require()
        item = review_sink.get(item_id, workspace_id=workspace_id)
        payload = dict((item or {}).get("payload") or {})
        if payload.get("kind") != SUBGRAPH_REVIEW_KIND:
            raise ValueError("That review item is not a shared-subgraph proposal.")
        node = dict(payload.get("node") or {})
        node_id = str(node.get("id") or "")
        if not node_id:
            raise ValueError("The proposal carries no node id.")
        status = str((item or {}).get("status") or "pending")
        if status not in _OPEN_REVIEW_STATUSES:
            # Import first, approve second would re-write an already-decided
            # item; refuse before touching the graph instead.
            raise ValueError(f"That proposal is already {status}; nothing was imported.")
        chunk_nodes = [dict(n) for n in payload.get("chunk_nodes") or []]
        if workspace_id:
            node = _scope_node(node, workspace_id)
            chunk_nodes = [_scope_node(n, workspace_id) for n in chunk_nodes]
        # The chunk rows land in the same write, so their nodes count as
        # present even though this Brain has not seen them yet.
        writing = {node_id} | {str(n.get("id")) for n in chunk_nodes}
        kept: List[Dict[str, Any]] = []
        deferred: List[Dict[str, Any]] = []
        for edge in payload.get("edges") or []:
            endpoints = {str(edge.get("from_node")), str(edge.get("to_node"))}
            outside = endpoints - writing
            if all(self._node_present(other) for other in outside):
                kept.append(edge)
            else:
                deferred.append({
                    "from": edge.get("from_node"),
                    "to": edge.get("to_node"),
                    "type": edge.get("type"),
                    "reason": "the other node is not in this Brain yet",
                })
        result = self._kg.import_graph_data(
            {
                "header": {"graph_schema_version": payload.get("graph_schema_version")},
                "nodes": [node, *chunk_nodes],
                "edges": kept,
                "chunks": list(payload.get("chunks") or []),
                "knowledge_sources": [],
                "provenance": list(payload.get("provenance") or []),
            },
            mode="merge",
        )
        approved = review_sink.approve(item_id, workspace_id=workspace_id)
        return {
            "status": "accepted",
            "item_id": item_id,
            "node_id": node_id,
            "imported": result,
            "edges_written": len(kept),
            "edges_deferred": deferred,
            "origin": (item.get("provenance") or {}).get("origin"),
            "review_status": approved.get("status"),
        }

    def _node_present(self, node_id: str) -> bool:
        try:
            self._kg.get_node(node_id)
        except Exception:  # noqa: BLE001 — "not found" is the answer, not a failure
            return False
        return True
