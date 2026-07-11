"""Helper utilities extracted from the monolithic WorkspaceOSStore module.

These are internal (_-prefixed) helpers for JSON handling, slugging, timeline,
and snapshot conversion. Moving them keeps workspace_os.py focused on the
store class and public constants while preserving exact behavior.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import atomic_write_json as _atomic_write_json  # noqa: F401 - legacy helper re-export
from .io_utils import parse_iso as _parse_iso  # noqa: F401 - legacy helper re-export


def _safe_slug(raw: str) -> str:
    value = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in str(raw or "").strip())
    value = "-".join(part for part in value.split("-") if part)
    return (value or "item")[:96]


def _json_hash(value: Any) -> str:
    import hashlib

    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _deep_merge(default: Any, loaded: Any) -> Any:
    if isinstance(default, dict) and isinstance(loaded, dict):
        merged = {key: _deep_merge(value, loaded.get(key)) for key, value in default.items()}
        for key, value in loaded.items():
            if key not in merged:
                merged[key] = value
        return merged
    if loaded is None:
        return default
    return loaded


def _listify(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _snapshot_graph_import_payload(graph_payload: Dict[str, Any], *, workspace_id: Optional[str]) -> Dict[str, Any]:
    """Convert the UI graph snapshot shape into the logical import artifact."""

    nodes = []
    for node in _listify((graph_payload or {}).get("nodes")):
        node_id = node.get("id")
        if not node_id:
            continue
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        raw = node.get("raw") if isinstance(node.get("raw"), dict) else {}
        if workspace_id and not metadata.get("workspace_id"):
            metadata = {**metadata, "workspace_id": workspace_id}
        nodes.append({
            "id": node_id,
            "type": node.get("type") or "Concept",
            "title": node.get("title") or node.get("label") or node_id,
            "summary": node.get("summary") or "",
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "raw_json": json.dumps(raw, ensure_ascii=False),
        })

    edges = []
    for edge in _listify((graph_payload or {}).get("edges")):
        source = edge.get("from_node") or edge.get("from") or edge.get("source")
        target = edge.get("to_node") or edge.get("to") or edge.get("target")
        if not source or not target:
            continue
        edges.append({
            "from_node": source,
            "to_node": target,
            "type": edge.get("type") or "related_to",
            "weight": edge.get("weight") or 1.0,
            "metadata_json": json.dumps(edge.get("metadata") or {}, ensure_ascii=False),
        })

    return {
        "header": {"graph_schema_version": 1, "workspace_id": workspace_id, "source": "workspace_snapshot"},
        "nodes": nodes,
        "edges": edges,
        "chunks": [],
        "knowledge_sources": [],
        "provenance": [],
        "counts": {"nodes": len(nodes), "edges": len(edges)},
    }


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return 0


def remove_skill_directory(skills_dir: Path, skill: str) -> Dict[str, Any]:
    """Remove an installed skill directory after caller has performed auth checks.

    Moved here from workspace_os.py for smaller focused surface.
    """
    safe_name = _safe_slug(skill)
    target = (skills_dir / safe_name).resolve()
    root = skills_dir.resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("invalid skill path")
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(skill)
    shutil.rmtree(target)
    return {"status": "ok", "skill": safe_name, "removed_path": str(target)}
