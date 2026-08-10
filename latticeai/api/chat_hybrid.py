"""Chat-path helpers for hybrid cloud branching (Phase 2).

Keeps the main chat router thin: resolve network mode, decide whether to
enter the hybrid stream, and build the StreamingResponse.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi.responses import StreamingResponse

from latticeai.core.network_boundary import (
    NetworkBoundaryMode,
    normalize_network_mode,
)
from latticeai.runtime.network_boundary_wiring import (
    get_hybrid_policy_service,
    resolve_active_network_mode,
)
from latticeai.services.hybrid_chat import stream_hybrid_cloud_turn


def resolve_request_network_mode(
    *,
    request_mode: Optional[str],
    user_email: Optional[str],
    workspace_id: Optional[str],
) -> NetworkBoundaryMode:
    """Per-request override wins; otherwise use the persisted dial."""
    if request_mode:
        return normalize_network_mode(request_mode)
    return normalize_network_mode(
        resolve_active_network_mode(user_email=user_email, workspace_id=workspace_id)
    )


def resolve_hybrid_auto_commit(
    *,
    user_email: Optional[str],
    workspace_id: Optional[str],
) -> bool:
    """The scoped hybrid policy's ``auto_commit`` decision for this turn.

    Sibling of :func:`resolve_request_network_mode`: the dial says whether the
    turn may reach the cloud, this says what may happen to what comes back.
    Default is ``False`` — cloud-derived knowledge waits in the Review Center
    — and a policy that cannot be read is treated as the default rather than
    as permission.
    """
    try:
        policy = get_hybrid_policy_service().resolve(
            user_email=user_email, workspace_id=workspace_id
        )
    except Exception:  # noqa: BLE001 — an unreadable policy never grants a write
        return False
    return bool(policy.get("auto_commit", False))


def maybe_hybrid_stream_response(
    *,
    req: Any,
    mode: NetworkBoundaryMode,
    knowledge_graph: Any,
    enable_graph: bool,
    effective_email: Optional[str],
    workspace_id: Optional[str],
    history_meta: Dict[str, Any],
    history_user: Dict[str, Any],
    chat_service: Any,
    notify: Any,
    model_id: Optional[str],
    review_queue: Any = None,
) -> Optional[StreamingResponse]:
    """Return a StreamingResponse when cloud path should run; else None."""
    if mode != NetworkBoundaryMode.CLOUD_ALLOWED:
        return None
    if not (enable_graph and knowledge_graph is not None):
        # Without a graph there is nothing minimal to send; fall back to local.
        return None

    return StreamingResponse(
        stream_hybrid_cloud_turn(
            user_message=req.message,
            knowledge_graph=knowledge_graph,
            mode=mode,
            workspace_id=workspace_id,
            user_email=effective_email,
            model=model_id,
            chat_service=chat_service,
            history_meta=history_meta,
            history_user=history_user,
            notify=notify,
            source=req.source,
            review_queue=review_queue,
            auto_commit=resolve_hybrid_auto_commit(
                user_email=effective_email, workspace_id=workspace_id
            ),
        ),
        media_type="text/event-stream",
        headers={
            "X-Model": model_id or "cloud",
            "X-Network-Mode": mode.value,
            "X-Hybrid": "1",
        },
    )


__all__ = [
    "resolve_request_network_mode",
    "resolve_hybrid_auto_commit",
    "maybe_hybrid_stream_response",
]
