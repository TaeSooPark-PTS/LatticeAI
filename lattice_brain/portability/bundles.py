"""Pure shaping of a shared subgraph bundle: select, expand, redact, digest.

None of these touch a store or a file. They decide *what* travels — which
nodes the selection names, which neighbours a one-hop expansion may admit, and
which sender-identifying fields are stripped on the way out — and they pin the
result with the digest the signature covers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Set

from .constants import _NEIGHBOR_EXCLUDED_TYPES, _REDACTED_FIELDS


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
