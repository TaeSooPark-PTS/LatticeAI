"""The v11.6.0 worker seams: the two chains a Rust route package delegates.

v11.5.1 gave the Rust loop calls back into Python (``/agent/llm``,
``/agent/tool``). v11.6.0 moved the *product* routes to ``lattice-host`` as
well, and two of those routes turn out to need something only this process can
do:

``GET /worker/sysinfo``
    The MLX/GPU block of ``/local/sysinfo``. CPU and RAM are ordinary host
    telemetry that Rust can sample itself; unified-memory usage can only be
    read from the process that holds the MLX context, which is this one.

``POST /worker/llm/stream``
    Token generation never moves (plan §설계 결정 6). ``/agent/llm`` is
    buffered; chat needs the same ``LLMRouter.stream_generate_as`` path
    framed as SSE so the gateway can pass bytes through. ``mode=document``
    selects ``stream_generate_document_as``.

``POST /worker/chat/record-turn`` used to live here as a third seam — the
redact → audit → store → ingest chain behind ``POST /chat``. WP-W3a moved that
whole chain into ``lattice-chat`` (``rust/lattice-chat/src/turn.rs``) and its
own tests assert the seam is requested **zero** times; WP-P1 retires it, along
with the conversation store, the audit sink and the ingestion write door that
were its only reason to exist in a worker that owns no state.

Both remaining seams are gated by the same switch as ``/agent/tool`` —
``LATTICEAI_AGENT_TOOL_SEAM=1``, read per request through
:func:`~latticeai.api.agent_worker_seam._seam_open` so there is one gate, not a
second one that can drift — and answer 404 with the same message when it is
off. They are mounted only by the worker profile
(:func:`latticeai.worker_app.create_worker_app`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess  # noqa: S404 — one fixed sysctl read, no shell, no user input
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from latticeai.api.agent_worker_seam import SEAM_RATE_BUCKET
from latticeai.api.agent_worker_seam import _seam_open as seam_open
from latticeai.core.messages import http_error, resolve_language
from latticeai.core.quiet import quiet

logger = logging.getLogger(__name__)


# ── GPU probe: the MLX half of /local/sysinfo ───────────────────────────────


def _mlx_memory() -> Tuple[int, int]:
    """Active and cached unified memory held by this process's MLX context."""
    import mlx.core as mx

    return int(mx.get_active_memory()), int(mx.get_cache_memory())


def _total_memory_bytes() -> int:
    """Installed physical memory, the denominator ``gpu_mem_pct`` is taken of."""
    completed = subprocess.run(  # noqa: S603,S607 — fixed argv, no shell
        ["sysctl", "-n", "hw.memsize"],
        capture_output=True,
        text=True,
        timeout=2,
    )
    return int(completed.stdout.strip())


def probe_gpu_memory() -> Dict[str, Any]:
    """The GPU block ``/local/sysinfo`` reports, on its own.

    Blocking (one ``sysctl``), so callers hand it to a worker thread. A machine
    without MLX — every Linux CI box, every Intel Mac — is not an error: it
    answers ``mlx_available: false`` with zeroes and the reason, which is what
    "we could not look" honestly looks like.
    """
    try:
        active, cached = _mlx_memory()
        total = _total_memory_bytes()
    except Exception as exc:  # noqa: BLE001 — an absent GPU is not a failure
        quiet("MLX unified-memory probe")
        return {
            "mlx_available": False,
            "gpu_mem_gb": 0.0,
            "gpu_mem_pct": 0.0,
            "total_bytes": 0,
            "detail": str(exc),
        }
    used = active + cached
    return {
        "mlx_available": True,
        "gpu_mem_gb": round(used / (1024 ** 3), 2),
        "gpu_mem_pct": round(used / total * 100, 1) if total else 0.0,
        "total_bytes": total,
        "detail": None,
    }


# ── request bodies ──────────────────────────────────────────────────────────


class LlmStreamRequest(BaseModel):
    """One streaming completion, in the argument shape ``LLMRouter`` already takes."""

    model_id: Optional[str] = None
    message: str
    context: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.2
    image_data: Optional[str] = None
    mode: str = "chat"


def create_worker_seams_router(
    *,
    require_user: Callable[[Request], Any],
    enforce_rate_limit: Callable[[str, str], None],
    model_router: Any = None,
) -> APIRouter:
    """The remaining v11.6.0 delegation seams, wired to what the worker holds.

    ``model_router`` is the in-process
    :class:`~latticeai.models.router.LLMRouter`; absent, the stream seam yields
    the same ``No model.`` marker the router itself would.
    """
    router = APIRouter()

    def _require_seam(request: Request) -> None:
        """404 unless the host opened the seam for this worker."""
        if not seam_open():
            raise http_error(404, "agent_seam.disabled", resolve_language(request))

    def _admit(request: Request) -> str:
        """Authenticate and charge this call against the per-step budget."""
        current_user = require_user(request)
        enforce_rate_limit(current_user, SEAM_RATE_BUCKET)
        return str(current_user or "")

    @router.get("/worker/sysinfo")
    async def worker_sysinfo(request: Request):
        """The unified-memory reading only this process can take."""
        _require_seam(request)
        _admit(request)
        return await asyncio.to_thread(probe_gpu_memory)

    @router.post("/worker/llm/stream")
    async def worker_llm_stream(req: LlmStreamRequest, request: Request):
        """Stream one completion over the same path ``ChatService`` uses.

        Frames are ``data: {"text": …}`` (or ``{"error": …}``) plus the
        terminating ``data: [DONE]`` sentinel. The gateway never content-
        negotiates on ``Accept``; this seam always answers as an event stream
        so a VS Code client that sent only ``stream: true`` still parses.
        """
        _require_seam(request)
        _admit(request)

        async def _frames():
            try:
                if model_router is None:
                    yield (
                        "data: "
                        + json.dumps({"text": "No model."}, ensure_ascii=False)
                        + "\n\n"
                    )
                    yield "data: [DONE]\n\n"
                    return
                if req.mode == "document":
                    agen = model_router.stream_generate_document_as(
                        req.model_id,
                        req.message,
                        req.context or "",
                        max_tokens=req.max_tokens,
                        temperature=req.temperature,
                    )
                else:
                    agen = model_router.stream_generate_as(
                        req.model_id,
                        req.message,
                        req.context,
                        req.max_tokens,
                        req.temperature,
                        req.image_data,
                    )
                async for chunk in agen:
                    text = chunk.text if hasattr(chunk, "text") else chunk
                    yield (
                        "data: "
                        + json.dumps({"text": str(text)}, ensure_ascii=False)
                        + "\n\n"
                    )
            except Exception as exc:  # noqa: BLE001 — already streaming a 200
                logger.warning("worker llm stream failed: %s", exc)
                yield (
                    "data: "
                    + json.dumps({"error": str(exc)}, ensure_ascii=False)
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"

        return StreamingResponse(_frames(), media_type="text/event-stream")

    return router


__all__ = [
    "LlmStreamRequest",
    "create_worker_seams_router",
    "probe_gpu_memory",
]
