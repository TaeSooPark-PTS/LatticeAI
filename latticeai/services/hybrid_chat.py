"""Hybrid chat turn: minimal KG context → cloud stream → local KG expansion.

Phase 1–2 entry point used by the chat path when NetworkBoundaryMode is
CLOUD_ALLOWED. Local-only sessions never enter this module's cloud path.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, Optional

from latticeai.core.network_boundary import (
    NetworkBoundaryMode,
    normalize_network_mode,
)
from latticeai.services.cloud_extraction import plan_kg_expansion_rich
from latticeai.services.cloud_streaming import (
    CloudResponseIngestor,
    CloudStreamingBridge,
    CloudTurnResult,
)
from latticeai.services.cloud_token_guard import budget_for
from latticeai.services.hybrid_context import MinimalContext, build_minimal_context
from latticeai.services.openai_compatible_adapter import OpenAICompatibleAdapter

logger = logging.getLogger(__name__)


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _scope_key(user_email: Optional[str], workspace_id: Optional[str]) -> str:
    return f"{user_email or 'anon'}|{workspace_id or 'global'}"


async def run_hybrid_cloud_turn(
    *,
    user_message: str,
    knowledge_graph: Any,
    mode: NetworkBoundaryMode | str,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
    model: Optional[str] = None,
    top_k: int = 6,
    adapter: Optional[Any] = None,
) -> CloudTurnResult:
    """Non-streaming helper: full answer + expansion plan."""
    mode = normalize_network_mode(mode)
    if mode != NetworkBoundaryMode.CLOUD_ALLOWED:
        raise PermissionError(
            f"hybrid cloud turn refused under NetworkBoundaryMode={mode.value!r}"
        )

    minimal = build_minimal_context(
        user_message,
        store=knowledge_graph,
        mode=mode,
        top_k=top_k,
        allowed_workspaces={workspace_id} if workspace_id else None,
    )
    budget = budget_for(_scope_key(user_email, workspace_id))
    refusal = budget.check_turn(minimal.token_estimate)
    if refusal:
        raise PermissionError(f"cloud token guard: {refusal}")

    bridge = CloudStreamingBridge(adapter=adapter or OpenAICompatibleAdapter())
    result = await bridge.run_turn(
        user_message=user_message,
        minimal=minimal,
        mode=mode,
        model=model,
    )
    plan = plan_kg_expansion_rich(result)
    ingestor = CloudResponseIngestor(store=knowledge_graph)
    ingest_status = ingestor.ingest(plan)
    used = minimal.token_estimate + max(1, len(result.answer_text) // 4)
    budget.record(used)
    result.usage = {
        **(result.usage or {}),
        "minimal_nodes": len(minimal.node_ids),
        "token_estimate": minimal.token_estimate,
        "context_quality": minimal.quality,
        "kg_expansion": ingest_status,
        "token_budget": budget.snapshot(),
    }
    return result


async def stream_hybrid_cloud_turn(
    *,
    user_message: str,
    knowledge_graph: Any,
    mode: NetworkBoundaryMode | str,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
    model: Optional[str] = None,
    top_k: int = 6,
    adapter: Optional[Any] = None,
    chat_service: Any = None,
    history_meta: Optional[Dict[str, Any]] = None,
    history_user: Optional[Dict[str, Any]] = None,
    notify: Any = None,
    source: Optional[str] = None,
) -> AsyncIterator[str]:
    """SSE generator for a hybrid cloud turn (Phase 2 chat path).

    Events:
    * ``hybrid_context`` — which local nodes were selected
    * ``token`` / classic ``chunk`` — streamed text
    * ``hybrid_done`` — final answer + KG expansion status
    * ``error`` — honest failure
    """
    mode = normalize_network_mode(mode)
    if mode != NetworkBoundaryMode.CLOUD_ALLOWED:
        yield _sse(
            {
                "type": "error",
                "detail": f"NetworkBoundaryMode is {mode.value!r}; cloud path disabled",
            }
        )
        yield "data: [DONE]\n\n"
        return

    minimal = build_minimal_context(
        user_message,
        store=knowledge_graph,
        mode=mode,
        top_k=top_k,
        allowed_workspaces={workspace_id} if workspace_id else None,
    )
    budget = budget_for(_scope_key(user_email, workspace_id))
    refusal = budget.check_turn(minimal.token_estimate)
    if refusal:
        yield _sse({"type": "error", "detail": f"cloud token guard: {refusal}"})
        yield "data: [DONE]\n\n"
        return

    yield _sse(
        {
            "type": "hybrid_context",
            "node_ids": minimal.node_ids,
            "keywords": minimal.keywords,
            "token_estimate": minimal.token_estimate,
            "quality": minimal.quality,
            "titles": [str(n.get("title") or n.get("id") or "") for n in minimal.nodes],
            "token_budget": budget.snapshot(),
        }
    )

    chosen_adapter = adapter or OpenAICompatibleAdapter()
    bridge = CloudStreamingBridge(adapter=chosen_adapter)

    try:
        chunks: list[str] = []
        if chosen_adapter is not None and hasattr(chosen_adapter, "stream"):
            system = (
                "You are assisting a user whose private Knowledge Graph lives on their machine. "
                "Use only the provided context. If the context is insufficient, say so honestly."
            )
            async for piece in chosen_adapter.stream(
                system=system,
                user=user_message,
                context=minimal.compact_text,
                model=model,
            ):
                chunks.append(piece)
                # dual shape: hybrid token + classic chunk for existing clients
                yield _sse({"type": "token", "text": piece, "chunk": piece, "model": model})
            answer = "".join(chunks)
            result = CloudTurnResult(
                user_message=user_message,
                answer_text=answer,
                sent_node_ids=list(minimal.node_ids),
                provider=getattr(chosen_adapter, "provider_name", "cloud"),
                model=model or getattr(chosen_adapter, "default_model", ""),
            )
        else:
            result = await bridge.run_turn(
                user_message=user_message,
                minimal=minimal,
                mode=mode,
                model=model,
            )
            yield _sse(
                {
                    "type": "token",
                    "text": result.answer_text,
                    "chunk": result.answer_text,
                    "model": model,
                }
            )

        plan = plan_kg_expansion_rich(result)
        ingest_status = CloudResponseIngestor(store=knowledge_graph).ingest(plan)
        used = minimal.token_estimate + max(1, len(result.answer_text) // 4)
        budget.record(used)

        if chat_service is not None:
            try:
                await chat_service.persist_entry(
                    "assistant",
                    result.answer_text,
                    history_meta=history_meta or {},
                    history_user=history_user or {},
                )
                if notify is not None:
                    notify("assistant", result.answer_text, source)
            except Exception as exc:  # noqa: BLE001
                logger.warning("hybrid chat persistence failed: %s", exc)

        yield _sse(
            {
                "type": "hybrid_done",
                "chunk": "",
                "answer": result.answer_text,
                "sent_node_ids": result.sent_node_ids,
                "provider": result.provider,
                "model": result.model,
                "kg_expansion": ingest_status,
                "token_estimate": minimal.token_estimate,
                "token_budget": budget.snapshot(),
            }
        )
        yield "data: [DONE]\n\n"
    except Exception as exc:  # noqa: BLE001 — surface honest error to client
        logger.warning("hybrid cloud turn failed: %s", exc)
        yield _sse({"type": "error", "detail": str(exc), "error": str(exc)})
        yield "data: [DONE]\n\n"


__all__ = [
    "run_hybrid_cloud_turn",
    "stream_hybrid_cloud_turn",
    "MinimalContext",
]
