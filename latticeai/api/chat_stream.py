"""SSE framing and streamed-answer finalization for chat routes."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, Optional

from fastapi.responses import JSONResponse, StreamingResponse

from latticeai.api.chat_helpers import single_text_stream


def single_answer_response(req: Any, answer: str, *, model: str):
    if req.stream:
        return StreamingResponse(
            single_text_stream(answer),
            media_type="text/event-stream",
            headers={"X-Model": model},
        )
    return JSONResponse(content={"response": answer})


def agent_payload_stream(
    answer: str,
    payload: Dict[str, Any],
    *,
    router: Any,
    model_id: Optional[str] = None,
) -> AsyncIterator[str]:
    async def _stream() -> AsyncIterator[str]:
        response_model = model_id or router.current_model_id
        yield f"data: {json.dumps({'chunk': answer, 'model': response_model, 'agent': payload}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'chunk': '', 'model': response_model, 'agent': payload}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return _stream()


async def stream_chat(
    req: Any,
    context: str,
    image_data: Optional[str],
    *,
    router: Any,
    chat_service: Any,
    knowledge_graph: Any,
    enable_graph: bool,
    notify: Any,
    trace_seed: Optional[Dict[str, Any]] = None,
    effective_email: Optional[str] = None,
    history_meta: Optional[Dict[str, Any]] = None,
    model_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> AsyncIterator[str]:
    """Stream model chunks and persist exactly one finalized answer."""

    full_response = ""
    stream_error: Optional[str] = None
    try:
        async for chunk in router.stream_generate_as(
            model_id,
            req.message,
            context,
            req.max_tokens,
            req.temperature,
            image_data,
        ):
            clean_chunk = chunk.text if hasattr(chunk, "text") else chunk
            full_response += str(clean_chunk)
            yield f"data: {json.dumps({'chunk': clean_chunk, 'model': model_id}, ensure_ascii=False)}\n\n"
    except Exception as exc:
        stream_error = str(exc)
        logging.warning("chat stream failed: %s", exc)
        yield f"data: {json.dumps({'error': stream_error, 'model': model_id}, ensure_ascii=False)}\n\n"

    persisted_response = full_response
    if stream_error and not persisted_response:
        persisted_response = f"[stream_error] {stream_error}"
    elif stream_error:
        persisted_response = f"{persisted_response}\n\n[stream_error] {stream_error}"

    trace_record = None
    try:
        answer_trace = trace_seed or chat_service.build_graph_trace(
            req.message,
            knowledge_graph if (enable_graph and knowledge_graph) else None,
            context,
            allowed_workspaces={workspace_id} if workspace_id else None,
        )
        trace_record = await chat_service.persist_answer(
            question=req.message,
            response=persisted_response,
            conversation_id=req.conversation_id,
            user_email=effective_email or req.user_email,
            user_nickname=req.user_nickname,
            source=req.source,
            trace=answer_trace,
            workspace_id=workspace_id,
            history_meta=history_meta or {},
            notify=notify,
        )
    except Exception as exc:
        logging.warning("chat stream persistence failed: %s", exc)

    trailer: Dict[str, Any] = {"chunk": "", "model": model_id}
    if trace_record:
        trailer.update({"trace_id": trace_record["id"], "trace": trace_record})
    if stream_error:
        trailer["error"] = stream_error
    yield f"data: {json.dumps(trailer, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


__all__ = ["agent_payload_stream", "single_answer_response", "stream_chat"]
