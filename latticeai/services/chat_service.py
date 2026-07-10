"""Chat / session service seam.

The streaming chat path in ``server_app`` is intentionally left in place — its
generator and SSE behaviour are sensitive. This service provides a stable seam
for the *bookkeeping* around answers (conversation history access and
Workspace-OS answer-trace recording) so those concerns are named and wrapped
rather than reaching into the store directly from the streaming handler.

It is a behaviour-preserving façade: methods forward to the injected history
accessor and :class:`WorkspaceOSStore`, so wiring the chat path through it
cannot change streaming output.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from latticeai.core.workspace_os import WorkspaceOSStore


class ChatService:
    def __init__(self, *, store: WorkspaceOSStore, get_history: Callable[[], List[Dict[str, Any]]]):
        self._store = store
        self._get_history = get_history

    # ── conversation history ─────────────────────────────────────────────

    def history(self) -> List[Dict[str, Any]]:
        return self._get_history()

    # ── answer-trace recording (Graph RAG) ───────────────────────────────

    def build_graph_trace(
        self,
        question: str,
        graph: Any,
        context: str = "",
        *,
        limit: int = 8,
        allowed_workspaces=None,
    ) -> Dict[str, Any]:
        return self._store.build_graph_trace(
            question,
            graph,
            context,
            limit=limit,
            allowed_workspaces=allowed_workspaces,
        )

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
        return self._store.record_trace(
            question=question,
            response=response,
            conversation_id=conversation_id,
            user_email=user_email,
            trace=trace,
            workspace_id=workspace_id,
        )
