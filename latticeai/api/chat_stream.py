"""SSE framing and streamed-answer finalization for chat routes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from fastapi.responses import JSONResponse, StreamingResponse

from latticeai.api.chat_helpers import assess_answer_grounding, single_text_stream


def single_answer_response(req: Any, answer: str, *, model: str):
    if req.stream:
        # The answering surface is reported once: the same ``model`` goes in
        # the X-Model header and in every SSE frame body, so a client reading
        # the stream never disagrees with a client reading the headers.
        return StreamingResponse(
            single_text_stream(answer, model=model),
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


def agent_live_stream(
    start: Callable[[Callable[[Dict[str, Any]], None]], Awaitable[Dict[str, Any]]],
    *,
    router: Any,
    model_id: Optional[str] = None,
    finalize: Optional[Callable[[Dict[str, Any]], str]] = None,
) -> AsyncIterator[str]:
    """Live SSE for an agent run (review Wave 1.1).

    ``start(observer)`` runs the agent with a per-run step observer; every
    observed step is emitted immediately as a named ``agent_step`` frame, so
    the user watches the loop work instead of staring at silence. When the
    run finishes, ``finalize(result)`` (history/funnel side effects) produces
    the answer text and the classic final payload frames follow — clients
    that ignore named events see exactly the historical stream shape.
    """

    async def _stream() -> AsyncIterator[str]:
        response_model = model_id or router.current_model_id
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

        def observer(event: Dict[str, Any]) -> None:
            # Called synchronously from the agent coroutine on this loop.
            queue.put_nowait(event)

        task = asyncio.ensure_future(start(observer))
        try:
            while True:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {getter, task}, return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    step = getter.result()
                    yield (
                        "event: agent_step\n"
                        f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
                    )
                    continue
                getter.cancel()
                break
            while not queue.empty():
                step = queue.get_nowait()
                yield (
                    "event: agent_step\n"
                    f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
                )
            result = task.result()
        except Exception as exc:  # noqa: BLE001 — already streaming a 200
            logging.warning("agent live stream failed: %s", exc)
            detail = getattr(exc, "detail", None) or str(exc)
            yield f"data: {json.dumps({'error': str(detail), 'model': response_model}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        answer = str(result.get("response") or "작업을 완료했습니다.")
        if finalize is not None:
            try:
                answer = finalize(result)
            except Exception as exc:  # noqa: BLE001 — side effects must not kill the stream
                logging.warning("agent live stream finalize failed: %s", exc)
        yield f"data: {json.dumps({'chunk': answer, 'model': response_model, 'agent': result}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'chunk': '', 'model': response_model, 'agent': result}, ensure_ascii=False)}\n\n"
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
    context_quality: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[str]:
    """Stream model chunks and persist exactly one finalized answer.

    ``context_quality`` (v9.8.0, additive) is echoed on the final trailer
    event alongside the answer trace so streaming clients receive the same
    honest RAG signal as the non-streaming JSON response.
    """

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
    grounding = None
    try:
        answer_trace = trace_seed or chat_service.build_graph_trace(
            req.message,
            knowledge_graph if (enable_graph and knowledge_graph) else None,
            context,
            allowed_workspaces={workspace_id} if workspace_id else None,
        )
        # Answer-citation binding (backlog #11): same honest verdict as the
        # non-streaming path, recorded on the persisted trace + trailer.
        try:
            grounding = assess_answer_grounding(
                full_response,
                trace=answer_trace if isinstance(answer_trace, dict) else None,
                context_quality=context_quality,
            )
            if isinstance(answer_trace, dict):
                answer_trace["grounding"] = grounding
        except Exception as exc:  # noqa: BLE001 — annotation must never break streaming
            logging.warning("answer grounding assessment failed: %s", exc)
            grounding = None
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
    if context_quality is not None:
        trailer["context_quality"] = context_quality
    if grounding is not None:
        trailer["grounding"] = grounding
    if stream_error:
        trailer["error"] = stream_error
    yield f"data: {json.dumps(trailer, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


__all__ = [
    "agent_live_stream",
    "agent_payload_stream",
    "single_answer_response",
    "stream_chat",
]
