from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from latticeai.api.agent_worker_seam import SEAM_RATE_BUCKET
from latticeai.api.agent_worker_seam import _seam_open as seam_open
from latticeai.core.messages import http_error, resolve_language
from latticeai.core.quiet import quiet

logger = logging.getLogger(__name__)


def _mlx_memory() -> Tuple[int, int]:
    import mlx.core as mx

    return int(mx.get_active_memory()), int(mx.get_cache_memory())


def _total_memory_bytes() -> int:
    return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))


def probe_gpu_memory() -> Dict[str, Any]:
    try:
        active, cached = _mlx_memory()
        total = _total_memory_bytes()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        quiet("MLX unified-memory probe")
        payload = {
            "mlx_available": False,
            "gpu_mem_gb": 0.0,
            "gpu_mem_pct": 0.0,
            "total_bytes": 0,
            "detail": str(exc),
        }
        payload.update(_sysinfo_extras())
        return payload
    used = active + cached
    payload = {
        "mlx_available": True,
        "gpu_mem_gb": round(used / (1024**3), 2),
        "gpu_mem_pct": round(used / total * 100, 1) if total else 0.0,
        "total_bytes": total,
        "detail": None,
    }
    payload.update(_sysinfo_extras())
    return payload


def _sysinfo_extras() -> Dict[str, Any]:
    try:
        from latticeai.api.worker_compute import sysinfo_payload_extras

        extras = sysinfo_payload_extras()
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        return {}
    return extras if isinstance(extras, dict) else {}


class LlmStreamRequest(BaseModel):
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
    router = APIRouter()

    def _require_seam(request: Request) -> None:
        if not seam_open():
            raise http_error(404, "agent_seam.disabled", resolve_language(request))

    def _admit(request: Request) -> str:
        current_user = require_user(request)
        enforce_rate_limit(current_user, SEAM_RATE_BUCKET)
        return str(current_user or "")

    @router.get("/worker/sysinfo")
    async def worker_sysinfo(request: Request):
        _require_seam(request)
        _admit(request)
        return await asyncio.to_thread(probe_gpu_memory)

    @router.post("/worker/llm/stream")
    async def worker_llm_stream(req: LlmStreamRequest, request: Request):
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
            except (OSError, RuntimeError, ValueError, TypeError, LookupError) as exc:
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
