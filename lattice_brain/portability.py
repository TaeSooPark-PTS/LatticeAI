"""Knowledge Graph portability — local export / import / backup / restore.

The Knowledge Graph is the user's durable asset, so it must be portable without
any cloud service. Two complementary mechanisms, both fully local:

* **Logical export/import** (JSON): nodes/edges/chunks/sources/provenance with a
  versioned header (schema + projection + embed-dim). Vectors are not in the
  artifact; the importer re-embeds with its own embedder and reports the
  resulting index state under ``result["index"]`` (``degraded: true`` means the
  content landed but recall is lexical-only until a rebuild succeeds). That is
  what makes it portable across machines.
* **Binary backup/restore** (ZIP): a faithful snapshot of the SQLite DB (incl.
  vector embeddings) plus the blob directory, integrity-checked, for
  same-machine recovery.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Set

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .archive import (
    KDF_ITERATIONS,
    BrainArchivePaths,
    EncryptedBrainArchive,
    _derive_key,
)
from .quiet import quiet
from .storage import (
    DockerPostgresWizard,
    PostgresEngine,
    SQLiteToPostgresMigrator,
)
from .utils import sha256_file as _sha256_file
from .utils import utc_now_iso

FORMAT = "latticeai.kg.export"
FORMAT_VERSION = 1
BACKUP_FORMAT = "latticeai.kg.backup"

# ── selective subgraph share (v11.1.0 prototype, off by default) ─────────────
#: Logical format of a *partial* bundle: a chosen slice of the graph plus its
#: provenance, signed by this device. Deliberately distinct from ``FORMAT`` so
#: the whole-graph importer can never be handed a subgraph by accident.
SUBGRAPH_FORMAT = "latticeai.kg.subgraph"
SUBGRAPH_FORMAT_VERSION = 1
#: Encrypted on-disk envelope for the same bundle, reusing the ``.latticebrain``
#: passphrase mechanism (PBKDF2-SHA256 + AES-256-GCM) from ``archive.py``.
SUBGRAPH_ARCHIVE_FORMAT = "latticebrain.subgraph"
#: Opt-in gate. Off unless the operator sets it: sharing knowledge with another
#: Brain is a network act, and local-first means those are never on by default.
BRAIN_NETWORK_ENV = "LATTICEAI_BRAIN_NETWORK"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
#: A received bundle becomes review items, never a merge. The cap keeps one
#: peer from flooding the inbox; what it dropped is reported, not hidden.
SUBGRAPH_PROPOSAL_CAP = 200
#: Review-queue vocabulary. ``kg_change_digest`` is the existing source for
#: "the graph would change"; the kind distinguishes shared-subgraph items.
SUBGRAPH_REVIEW_SOURCE = "kg_change_digest"
SUBGRAPH_REVIEW_KIND = "shared_subgraph_node"
#: Mirrors ``services.review_queue.OPEN_STATUSES``. Duplicated as a literal
#: rather than imported because ``lattice_brain`` must not depend on the
#: ``latticeai`` service layer; the review sink is injected, not imported.
_OPEN_REVIEW_STATUSES = frozenset({"pending", "snoozed"})

BRAIN_NETWORK_DISABLED_DETAIL = (
    "Brain Network sharing is off. It is opt-in by design: set "
    f"{BRAIN_NETWORK_ENV}=1 to enable selective subgraph export and receipt."
)


class BrainNetworkDisabled(PermissionError):
    """Raised when a share surface is used while the opt-in flag is off."""

    def __init__(self, detail: str = BRAIN_NETWORK_DISABLED_DETAIL) -> None:
        super().__init__(detail)


def brain_network_enabled() -> bool:
    """True only when the operator explicitly opted in (default: off)."""
    return os.getenv(BRAIN_NETWORK_ENV, "").strip().lower() in _TRUTHY


def require_brain_network() -> None:
    if not brain_network_enabled():
        raise BrainNetworkDisabled()


def _stamp() -> str:
    return utc_now_iso().replace(":", "").replace("-", "").replace(".", "")[:15]


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


def _checkpoint_sqlite(db_path: Path) -> None:
    if not db_path.exists():
        return
    try:
        with closing(sqlite3.connect(str(db_path))) as conn, conn:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
    except sqlite3.Error:
        # Best-effort only. Existing sibling backup/restore still preserves
        # the WAL files if a live connection prevents a checkpoint.
        return


def _restore_sibling(path: Path, backup: Path) -> None:
    try:
        shutil.copy2(backup, path)
    except FileNotFoundError:
        # The backup vanished (or never existed — transient -wal/-shm): the
        # honest reconstruction is "no such sibling", never a crash that
        # masks the error that triggered the rollback.
        path.unlink(missing_ok=True)


def _replace_sqlite_atomically(src: Path, dest: Path, backup_dir: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.restore-{_stamp()}-{os.getpid()}.tmp"
    shutil.copyfile(src, tmp)
    backups: dict[Path, Path] = {}
    try:
        _checkpoint_sqlite(dest)
        # -wal/-shm are transient: another live connection can checkpoint and
        # remove them between exists() and the copy/unlink. Treat a vanished
        # sibling as "nothing to preserve" instead of crashing the restore.
        for sibling in _sqlite_siblings(dest):
            backup = backup_dir / sibling.name
            try:
                shutil.copy2(sibling, backup)
            except FileNotFoundError:
                quiet()
                continue
            backups[sibling] = backup
        for sibling in _sqlite_siblings(dest)[1:]:
            sibling.unlink(missing_ok=True)
        os.replace(tmp, dest)
    except Exception:
        # Recovery I/O must never replace the swap error being reported —
        # an exception raised here would mask it (the CI-observed
        # "[Errno 2]" over the real failure). Best-effort restore, then
        # always re-raise the original.
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logging.warning("restore tmp cleanup failed: %s", cleanup_exc)
        for sibling in _sqlite_siblings(dest):
            try:
                _restore_sibling(sibling, backups.get(sibling, backup_dir / sibling.name))
            except OSError as rollback_exc:
                logging.warning(
                    "restore sibling rollback incomplete for %s: %s",
                    sibling, rollback_exc,
                )
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
        # Same masking guard as the sqlite swap: rollback I/O is
        # best-effort and the original failure always propagates.
        try:
            if staged.exists():
                shutil.rmtree(staged)
            if dest.exists():
                shutil.rmtree(dest)
            if backup.exists():
                shutil.copytree(backup, dest)
        except OSError as rollback_exc:
            logging.warning("blob tree rollback incomplete: %s", rollback_exc)
        raise


#: Sender-identifying fields stripped from a shared bundle by default. The
#: knowledge travels; "which account, on which machine, at which path" does not.
_REDACTED_FIELDS = frozenset({"owner", "user_email", "source_uri", "permissions"})
#: The signed payload's key set. One tuple so export and verify can never
#: disagree about what the digest covers.
_PAYLOAD_KEYS = (
    "nodes", "chunk_nodes", "edges", "chunks", "knowledge_sources", "provenance",
)
#: Node types a one-hop expansion never admits — they describe the sender
#: (identity, local paths), not the knowledge being shared.
_NEIGHBOR_EXCLUDED_TYPES = frozenset({"Person", "Source"})


def _canonical_digest(payload: Dict[str, Any]) -> str:
    """sha256 over the canonical JSON of a bundle payload.

    Pinned in the signed header so the Ed25519 signature covers the contents,
    not just the manifest describing them.
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _node_source_type(node: Dict[str, Any]) -> str:
    try:
        metadata = json.loads(node.get("metadata_json") or "{}")
    except (TypeError, ValueError):
        return ""
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("source_type") or "").strip().lower()


def _select_node_ids(nodes: List[Dict[str, Any]], selection: Dict[str, Any]) -> Set[str]:
    wanted_ids = set(selection["node_ids"])
    wanted_types = set(selection["node_types"])
    wanted_sources = set(selection["source_types"])
    keep: Set[str] = set()
    for node in nodes:
        node_id = str(node.get("id"))
        if node_id in wanted_ids:
            keep.add(node_id)
            continue
        if wanted_types and str(node.get("type") or "").strip().lower() in wanted_types:
            keep.add(node_id)
            continue
        if wanted_sources and _node_source_type(node) in wanted_sources:
            keep.add(node_id)
    return keep


def _expand_neighbors(
    keep: Set[str], edges: List[Dict[str, Any]], known: Dict[str, str]
) -> Set[str]:
    """One hop out from the selection, in both directions, within the scope.

    ``Person`` and ``Source`` neighbours are never pulled in implicitly: a
    Person node's label *is* an email address and a Source node's summary *is*
    a local file path, so "share this decision and what it touches" would
    quietly ship the sender's identity and directory layout. Naming one of
    those node ids explicitly still exports it — that is a decision, not a
    side effect.
    """
    expanded = set(keep)

    def _admit(candidate: str) -> None:
        if candidate in known and known[candidate] not in _NEIGHBOR_EXCLUDED_TYPES:
            expanded.add(candidate)

    for edge in edges:
        source = str(edge.get("from_node"))
        target = str(edge.get("to_node"))
        if source in keep:
            _admit(target)
        if target in keep:
            _admit(source)
    return expanded


def _strip_fields(raw_json: Optional[str]) -> Optional[str]:
    if not raw_json:
        return raw_json
    try:
        parsed = json.loads(raw_json)
    except (TypeError, ValueError):
        return raw_json
    if not isinstance(parsed, dict):
        return raw_json
    cleaned = {k: v for k, v in parsed.items() if k not in _REDACTED_FIELDS}
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True)


def _redact_node(node: Dict[str, Any]) -> Dict[str, Any]:
    redacted = dict(node)
    redacted["metadata_json"] = _strip_fields(node.get("metadata_json"))
    redacted["raw_json"] = _strip_fields(node.get("raw_json"))
    return redacted


def _redact_provenance_row(row: Dict[str, Any]) -> Dict[str, Any]:
    redacted = dict(row)
    redacted["owner"] = None
    redacted["source_uri"] = None
    redacted["permissions_json"] = "{}"
    redacted["metadata_json"] = _strip_fields(row.get("metadata_json"))
    return redacted


def _scope_node(node: Dict[str, Any], workspace_id: str) -> Dict[str, Any]:
    """Stamp the accepting workspace onto an incoming node.

    Received knowledge belongs to the workspace that accepted it. Without this
    the projection would file it as legacy-global — machine-shared — which is a
    wider scope than the reviewer chose.
    """
    scoped = dict(node)
    try:
        metadata = json.loads(scoped.get("metadata_json") or "{}")
    except (TypeError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["workspace_id"] = workspace_id
    scoped["metadata_json"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return scoped


def _shared_node_summary(node: Dict[str, Any], verdict: Dict[str, Any]) -> str:
    body = str(node.get("summary") or "").strip()
    origin = verdict.get("origin") or "unknown device"
    prefix = f"[{origin}] "
    return (prefix + body)[:500] if body else prefix.strip()


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

    def import_from_file(self, path, *, mode: str = "merge", dry_run: bool = False) -> Dict[str, Any]:
        artifact = json.loads(Path(path).read_text(encoding="utf-8"))
        return self.import_data(artifact, mode=mode, dry_run=dry_run)

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
            # Stated rather than implied: the bundle is signed by this device
            # and encrypted with a shared passphrase. Encrypting *to a
            # recipient's public key* is not implemented in this release.
            "encryption": "passphrase",
            "recipient_public_key_encryption": False,
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
        passphrase: str,
        **selection: Any,
    ) -> Dict[str, Any]:
        """Write the signed subgraph as an encrypted ``.latticebrain`` file.

        Same passphrase mechanism as the whole-Brain archive (PBKDF2-SHA256 →
        AES-256-GCM), a different ``format`` so the two can never be confused.
        The header and signature stay outside the ciphertext on purpose: a
        recipient can see *who* sent a bundle, and how big it is, before typing
        a passphrase into it.
        """
        artifact = self.export_subgraph(**selection)
        self._exports_dir.mkdir(parents=True, exist_ok=True)
        dest = Path(dest_path) if dest_path else self._exports_dir / f"subgraph-{_stamp()}.latticebrain"
        if dest.suffix != ".latticebrain":
            dest = dest.with_suffix(".latticebrain")
        body = {key: value for key, value in artifact.items() if key not in {"header", "signature"}}
        plaintext = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        salt = os.urandom(16)
        nonce = os.urandom(12)
        ciphertext = AESGCM(_derive_key(passphrase, salt)).encrypt(nonce, plaintext, None)
        envelope = {
            "format": SUBGRAPH_ARCHIVE_FORMAT,
            "format_version": SUBGRAPH_FORMAT_VERSION,
            "created_at": utc_now_iso(),
            "kdf": {
                "name": "PBKDF2HMAC-SHA256",
                "iterations": KDF_ITERATIONS,
                "salt": base64.b64encode(salt).decode("ascii"),
            },
            "cipher": {"name": "AES-256-GCM", "nonce": base64.b64encode(nonce).decode("ascii")},
            "header": artifact["header"],
            "signature": artifact["signature"],
            "payload": base64.b64encode(ciphertext).decode("ascii"),
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "path": str(dest),
            "bytes": dest.stat().st_size,
            "encrypted": True,
            "format": SUBGRAPH_ARCHIVE_FORMAT,
            "header": artifact["header"],
            "counts": artifact["header"]["counts"],
        }

    def read_subgraph_archive(self, path, *, passphrase: str) -> Dict[str, Any]:
        """Decrypt an encrypted subgraph bundle back into a signed artifact."""
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
        try:
            salt = base64.b64decode(envelope["kdf"]["salt"])
            nonce = base64.b64decode(envelope["cipher"]["nonce"])
            ciphertext = base64.b64decode(envelope["payload"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Subgraph bundle is missing encryption metadata.") from exc
        try:
            plaintext = AESGCM(_derive_key(passphrase, salt)).decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise ValueError(
                "Subgraph decryption failed; the passphrase or the bundle is invalid."
            ) from exc
        body = json.loads(plaintext.decode("utf-8"))
        return {
            "header": envelope.get("header") or {},
            **body,
            "signature": envelope.get("signature") or {},
        }

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
            from .graph.identity import verify_manifest

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
