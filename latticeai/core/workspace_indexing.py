"""Indexing dashboard and per-source watch control.

Extracted from ``WorkspaceOSStore`` so the store stays a façade over composed
managers rather than a class that also knows how a file watcher reports itself.
Everything here reads the graph and the watcher and writes only timeline
events, so it needs the store for ``record_timeline_event`` and nothing else.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["WorkspaceIndexing"]


class WorkspaceIndexing:
    """Reads index state out of the graph; pauses, resumes, and removes sources."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def build_dashboard(
        self,
        graph: Any,
        watcher_status: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if graph is None:
            return {
                "sources": [],
                "watcher": watcher_status or {"available": False, "active": {}},
                "totals": {"success": 0, "failed": 0, "nodes": 0, "edges": 0},
            }
        stats = graph.stats()
        sources = graph.local_sources().get("sources", [])
        watcher_status = watcher_status or {"available": False, "active": {}}
        active = watcher_status.get("active", {})
        dashboard_sources = []
        total_success = 0
        total_failed = 0
        for source in sources:
            file_status = source.get("file_status") or {}
            success = int(file_status.get("indexed") or 0)
            failed = sum(
                int(file_status.get(key) or 0)
                for key in ("failed", "inaccessible", "skipped_empty_text")
            )
            total_success += success
            total_failed += failed
            watch = active.get(source.get("id")) or {}
            dashboard_sources.append({
                "id": source.get("id"),
                "label": source.get("label"),
                "root_path": source.get("root_path"),
                "status": source.get("status"),
                "watch_enabled": bool(source.get("watch_enabled")),
                "watch_active": source.get("id") in active,
                "watch_status": watch,
                "success_count": success,
                "failure_count": failed,
                "last_run_at": source.get("last_scanned_at") or source.get("updated_at"),
                "file_status": file_status,
                "include_ocr": bool(source.get("include_ocr")),
            })
        return {
            "sources": dashboard_sources,
            "watcher": watcher_status,
            "totals": {
                "success": total_success,
                "failed": total_failed,
                "nodes": sum(int(v or 0) for v in (stats.get("nodes") or {}).values()),
                "edges": sum(int(v or 0) for v in (stats.get("edges") or {}).values()),
                "local_sources": stats.get("local_sources", len(sources)),
            },
            "graph_stats": stats,
        }

    def pause(self, graph: Any, source_id: str, watcher: Any = None) -> Dict[str, Any]:
        result = graph.set_local_source_watch(source_id, False)
        watch = watcher.stop_source(source_id) if watcher else {"stopped": False, "source_id": source_id}
        self.store.record_timeline_event("graph", "indexing_paused", {"source_id": source_id})
        return {"status": "ok", "source": result, "watch": watch}

    def resume(self, graph: Any, source_id: str, watcher: Any = None) -> Dict[str, Any]:
        result = graph.set_local_source_watch(source_id, True)
        watch = {"watching": False, "source_id": source_id}
        source = next(
            (item for item in graph.local_sources().get("sources", []) if item.get("id") == source_id),
            None,
        )
        if watcher and source:
            watch = watcher.start_source(source)
        self.store.record_timeline_event("graph", "indexing_resumed", {"source_id": source_id})
        return {"status": "ok", "source": result, "watch": watch}

    def remove_source(self, graph: Any, source_id: str, watcher: Any = None) -> Dict[str, Any]:
        if watcher:
            watcher.stop_source(source_id)
        if not hasattr(graph, "remove_local_source"):
            raise ValueError("graph store does not support removing local sources")
        result = graph.remove_local_source(source_id)
        self.store.record_timeline_event("graph", "indexing_removed", {"source_id": source_id})
        return {"status": "ok", **result}
