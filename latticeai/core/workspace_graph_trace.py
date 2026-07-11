"""Graph answer trace persistence extracted from WorkspaceOSStore."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .timeutil import now_iso as _now
from .workspace_os_utils import _json_hash, _listify


class WorkspaceGraphTrace:
    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def build_graph_trace(
        self,
        question: str,
        graph: Any,
        context: str = "",
        *,
        limit: int = 8,
        allowed_workspaces=None,
    ) -> Dict[str, Any]:
        if graph is None:
            return {
                "source_files": [],
                "graph_nodes": [],
                "graph_edges": [],
                "confidence": 0.0,
                "retrieval_metadata": {
                    "query": question,
                    "matched_nodes": 0,
                    "graph_enabled": False,
                    "context_chars": len(context or ""),
                },
            }

        matches: List[Dict[str, Any]] = []
        search_error = ""
        try:
            scope_kwargs = (
                {"allowed_workspaces": allowed_workspaces}
                if allowed_workspaces is not None
                else {}
            )
            matches = graph.search(
                question,
                limit=limit,
                **scope_kwargs,
            ).get("matches", [])
        except Exception as exc:
            search_error = str(exc)
            matches = []

        source_files: List[Dict[str, Any]] = []
        seen_sources = set()
        for match in matches:
            meta = match.get("metadata") or {}
            source = (
                meta.get("relative_path")
                or meta.get("file_path")
                or meta.get("filename")
                or meta.get("blob_path")
                or meta.get("source")
            )
            if source and source not in seen_sources:
                seen_sources.add(source)
                source_files.append({
                    "source": source,
                    "node_id": match.get("id"),
                    "node_title": match.get("title"),
                    "node_type": match.get("type"),
                    "jump": {
                        "graph": f"/graph?node={match.get('id')}",
                        "source": source,
                    },
                })

        edges: List[Dict[str, Any]] = []
        edge_seen = set()
        for match in matches[:5]:
            node_id = match.get("id")
            if not node_id:
                continue
            try:
                for edge in graph.neighbors(
                    node_id,
                    **scope_kwargs,
                ).get("edges", []):
                    key = (edge.get("from"), edge.get("to"), edge.get("type"))
                    if key in edge_seen:
                        continue
                    edge_seen.add(key)
                    edges.append(edge)
                    if len(edges) >= 24:
                        break
            except Exception:
                continue

        if matches:
            confidence = min(0.95, 0.35 + min(len(matches), limit) / max(limit, 1) * 0.45 + (0.10 if edges else 0.0))
        else:
            confidence = 0.05 if context else 0.0

        return {
            "source_files": source_files,
            "graph_nodes": matches,
            "graph_edges": edges,
            "confidence": round(confidence, 4),
            "retrieval_metadata": {
                "query": question,
                "matched_nodes": len(matches),
                "matched_edges": len(edges),
                "graph_enabled": True,
                "context_chars": len(context or ""),
                "search_error": search_error,
            },
        }

    def record_trace(
        self,
        *,
        question: str,
        response: str,
        conversation_id: Optional[str],
        user_email: Optional[str],
        trace: Dict[str, Any],
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self.load_state()
        trace_id = f"trace-{_json_hash([question, response, conversation_id, _now()])[:16]}"
        record = {
            "id": trace_id,
            "question": question,
            "response_preview": str(response or "")[:700],
            "conversation_id": conversation_id,
            "user_email": user_email,
            "workspace_id": self._resolve_scope(workspace_id, state),
            "created_at": _now(),
            **trace,
        }
        state.setdefault("traces", []).append(record)
        self.save_state(state)
        self.record_timeline_event(
            "graph",
            "answer_trace",
            {"trace_id": trace_id, "conversation_id": conversation_id},
            workspace_id=record["workspace_id"],
        )
        return record

    def list_traces(self, conversation_id: Optional[str] = None, limit: int = 50, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        traces = self._scoped(_listify(self.load_state().get("traces")), workspace_id)
        if conversation_id:
            traces = [trace for trace in traces if trace.get("conversation_id") == conversation_id]
        return {"traces": list(reversed(traces[-max(1, min(limit, 200)):]))}
