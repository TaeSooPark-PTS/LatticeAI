"""Hybrid chat turn: minimal KG context → cloud stream → local KG expansion.

Phase 1–2 entry point used by the chat path when NetworkBoundaryMode is
CLOUD_ALLOWED. Local-only sessions never enter this module's cloud path.

Both turn functions take the Review Center sink and the hybrid policy's
``auto_commit`` decision as arguments rather than reaching for them: the
caller that knows *whose* turn this is is the only one that can resolve a
per-user policy, and a service that reads a process singleton cannot be
tested against both branches. Defaults are the safe ones — no sink, no
auto-commit — so a headless caller stages nothing rather than writing.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, Optional

from latticeai.core.network_boundary import (
    NetworkBoundaryMode,
    normalize_network_mode,
)
from latticeai.core.sse import sse_frame
from latticeai.services.cloud_egress_audit import record_cloud_egress
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
    return sse_frame(None, data)


def _scope_key(user_email: Optional[str], workspace_id: Optional[str]) -> str:
    return f"{user_email or 'anon'}|{workspace_id or 'global'}"


def _ingest_cloud_expansion(
    result: CloudTurnResult,
    *,
    knowledge_graph: Any,
    review_queue: Any,
    auto_commit: bool,
    user_email: Optional[str],
    workspace_id: Optional[str],
) -> Dict[str, Any]:
    """Stage what the cloud answer taught the Brain (v11.2.0 wiring).

    ``plan_kg_expansion_rich`` builds every plan with ``auto_commit=False``
    because extraction has no idea what the user consented to; the policy dial
    does, and this is where the two meet. With the sink bound the plan lands in
    the Review Center as a ``change_proposal``, which is what makes
    cloud-derived memory growth a thing the user approves rather than a thing
    that happens to them.
    """
    plan = plan_kg_expansion_rich(result)
    plan.auto_commit = bool(auto_commit)
    ingestor = CloudResponseIngestor(
        store=knowledge_graph,
        review_queue=review_queue,
        user_email=user_email,
        workspace_id=workspace_id,
    )
    return ingestor.ingest(plan)


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
    review_queue: Any = None,
    auto_commit: bool = False,
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
        record_cloud_egress(
            node_ids=minimal.node_ids, token_estimate=minimal.token_estimate,
            mode=mode.value, provider="(refused)", model=model,
            user_email=user_email, workspace_id=workspace_id,
            outcome="refused_token_guard", detail=refusal,
        )
        raise PermissionError(f"cloud token guard: {refusal}")

    chosen_adapter = adapter or OpenAICompatibleAdapter()
    record_cloud_egress(
        node_ids=minimal.node_ids, token_estimate=minimal.token_estimate,
        mode=mode.value, provider=getattr(chosen_adapter, "name", type(chosen_adapter).__name__),
        model=model, user_email=user_email, workspace_id=workspace_id,
    )
    bridge = CloudStreamingBridge(adapter=chosen_adapter)
    result = await bridge.run_turn(
        user_message=user_message,
        minimal=minimal,
        mode=mode,
        model=model,
    )
    ingest_status = _ingest_cloud_expansion(
        result,
        knowledge_graph=knowledge_graph,
        review_queue=review_queue,
        auto_commit=auto_commit,
        user_email=user_email,
        workspace_id=workspace_id,
    )
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
    review_queue: Any = None,
    auto_commit: bool = False,
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
        # A refusal is auditable too: "nothing left the machine, and here is why".
        record_cloud_egress(
            node_ids=minimal.node_ids, token_estimate=minimal.token_estimate,
            mode=mode.value, provider="(refused)", model=model,
            user_email=user_email, workspace_id=workspace_id,
            outcome="refused_token_guard", detail=refusal,
        )
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

    # Recorded before the call, not after: if the provider hangs or the process
    # dies mid-stream, the record of what was about to leave must already exist.
    record_cloud_egress(
        node_ids=minimal.node_ids, token_estimate=minimal.token_estimate,
        mode=mode.value, provider=getattr(chosen_adapter, "name", type(chosen_adapter).__name__),
        model=model, user_email=user_email, workspace_id=workspace_id,
    )

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
                model=str(model or getattr(chosen_adapter, "default_model", "")),
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

        ingest_status = _ingest_cloud_expansion(
            result,
            knowledge_graph=knowledge_graph,
            review_queue=review_queue,
            auto_commit=auto_commit,
            user_email=user_email,
            workspace_id=workspace_id,
        )
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
