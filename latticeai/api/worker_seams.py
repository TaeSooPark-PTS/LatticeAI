"""The v11.6.0 worker seams: the four chains a Rust route package delegates.

v11.5.1 gave the Rust loop three calls back into Python (``/agent/llm``,
``/agent/tool``, ``/agent/change-proposal``). v11.6.0 moves the *product*
routes to ``lattice-host`` as well, and three of those routes turn out to need
something Python still owns:

``POST /worker/chat/record-turn``
    The history write chain — redact → audit → store → ingest. Rust will own
    ``POST /chat`` (plan §6), but the chain that persists a turn is four Python
    dependencies deep (secret redaction, the audit log's sensitivity verdict,
    the durable conversation store, and the ingestion pipeline that is the
    Brain's single write door). This seam runs the *existing*
    :func:`~latticeai.runtime.history_writer.write_chat_turn` — the same
    function ``phase_brain`` wires ``save_to_history`` to — and reports the
    receipts it produced rather than reimplementing any step of it.

``GET /worker/sysinfo``
    The MLX/GPU block of ``/local/sysinfo``. CPU and RAM are ordinary host
    telemetry that Rust can sample itself; unified-memory usage can only be
    read from the process that holds the MLX context, which is this one.

``POST /worker/llm/stream``
    Token generation never moves (plan §설계 결정 6). ``/agent/llm`` is
    buffered; chat needs the same ``LLMRouter.stream_generate_as`` path
    ``ChatService`` already uses, framed as SSE so the gateway can pass
    bytes through. ``mode=document`` selects ``stream_generate_document_as``.

All four are gated by the same switch as ``/agent/tool`` —
``LATTICEAI_AGENT_TOOL_SEAM=1``, read per request through
:func:`~latticeai.api.agent_worker_seam._seam_open` so there is one gate, not a
second one that can drift — and answer 404 with the same message when it is
off. They are mounted only by the worker profile
(:func:`latticeai.worker_app.create_worker_app`); ``create_app`` does not
include this router, so the product application and its committed OpenAPI
contract are unchanged by this file's existence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess  # noqa: S404 — one fixed sysctl read, no shell, no user input
from dataclasses import replace
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from latticeai.api.agent_worker_seam import SEAM_RATE_BUCKET
from latticeai.api.agent_worker_seam import _seam_open as seam_open
from latticeai.core.messages import MESSAGES, http_error, resolve_language
from latticeai.core.quiet import quiet
from latticeai.runtime.history_writer import write_chat_turn

logger = logging.getLogger(__name__)

#: Roles a chat turn may carry. The conversation store defaults anything else
#: to ``"user"``, which would quietly file a mislabelled turn under the wrong
#: speaker, so the seam refuses instead of guessing.
CHAT_ROLES: Tuple[str, ...] = ("user", "assistant", "system")

#: Messages this module owns, in the shape ``latticeai.core.messages`` renders.
#: Registered into the shared catalog below rather than duplicated: one id per
#: message stays true, and ``http_error``/``translate`` need no special case.
WORKER_SEAM_MESSAGES: Dict[str, Dict[str, str]] = {
    "worker_seam.role_invalid": {
        "ko": "'{role}' 은(는) 기록할 수 있는 역할이 아닙니다. {allowed} 중 하나여야 합니다.",
        "en": "'{role}' is not a role a turn can be recorded under. Use one of {allowed}.",
    },
    "worker_seam.history_unavailable": {
        "ko": "대화 기록 저장소가 연결되어 있지 않습니다.",
        "en": "The conversation history store is not connected.",
    },
    "worker_seam.graph_unavailable": {
        "ko": "지식 그래프가 꺼져 있어 쓰기를 위임할 수 없습니다.",
        "en": "The knowledge graph is off, so there is nothing to delegate the write to.",
    },
    "worker_seam.op_not_allowed": {
        "ko": "'{op}' 은(는) 위임할 수 있는 그래프 쓰기가 아닙니다. {allowed} 중 하나여야 합니다.",
        "en": "'{op}' is not a graph write this seam delegates. Use one of {allowed}.",
    },
    "worker_seam.arg_not_allowed": {
        "ko": "'{op}' 은(는) '{arg}' 값을 받지 않습니다. {allowed} 만 보낼 수 있습니다.",
        "en": "'{op}' does not take '{arg}'. Only {allowed} may be sent.",
    },
    "worker_seam.graph_mutation_failed": {
        "ko": "'{op}' 그래프 쓰기가 실패했습니다: {reason}",
        "en": "The '{op}' graph write failed: {reason}",
    },
}


def register_worker_seam_messages() -> None:
    """Publish this module's messages into the one shared catalog.

    ``setdefault`` rather than assignment so the entries can be lifted into
    ``latticeai/core/messages.py`` verbatim later (the integrator's step) and
    this registration becomes a no-op instead of overwriting them.
    """
    for key, entry in WORKER_SEAM_MESSAGES.items():
        MESSAGES.setdefault(key, entry)


register_worker_seam_messages()


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


# ── history chain: observe the receipts without changing the chain ──────────


class _RecordingConversations:
    """The durable store, with the row it accepted kept for the receipt."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.item: Optional[Dict[str, Any]] = None

    def append(self, item: Dict[str, Any]) -> Any:
        result = self._inner.append(item)
        # After the call, so a store that raises leaves ``stored`` false rather
        # than claiming a write that did not land.
        self.item = dict(item)
        return result


class _RecordingPipeline:
    """The ingestion pipeline, with its ``IngestionResult`` kept for the receipt."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.result: Any = None

    def ingest(self, item: Any, **kwargs: Any) -> Any:
        outcome = self._inner.ingest(item, **kwargs)
        to_dict = getattr(outcome, "as_dict", None)
        self.result = to_dict() if callable(to_dict) else outcome
        return outcome


class _TurnRecorder:
    """One call's worth of instrumentation around the history dependencies.

    :func:`write_chat_turn` returns ``None`` and swallows its own failures by
    design — a chat reply must not be lost to a logging bug. That is right for
    the in-process caller and useless to a remote one, which needs to know
    whether the turn landed and under which node. Wrapping the two dependencies
    that produce receipts answers that without touching the chain: the order,
    the redaction and the best-effort ingest are still exactly the writer's.
    """

    def __init__(self, deps: Any) -> None:
        self.conversations = _RecordingConversations(deps.conversations)
        pipeline = deps.ingestion_pipeline
        self.pipeline = _RecordingPipeline(pipeline) if pipeline is not None else None
        self.deps = replace(
            deps,
            conversations=self.conversations,
            ingestion_pipeline=self.pipeline,
        )

    def receipt(self) -> Dict[str, Any]:
        """What the chain actually produced, with nothing inferred."""
        return {
            "stored": self.conversations.item is not None,
            "item": self.conversations.item,
            "ingested": self.pipeline.result if self.pipeline is not None else None,
        }


# ── request bodies ──────────────────────────────────────────────────────────


class RecordTurnRequest(BaseModel):
    """One chat turn, in the exact argument shape ``write_chat_turn`` takes."""

    role: str
    message: str
    user_email: Optional[str] = None
    user_nickname: Optional[str] = None
    source: Optional[str] = None
    conversation_id: Optional[str] = None
    workspace_id: Optional[str] = None


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
    history_deps: Any,
    graph_store: Any,
    require_user: Callable[[Request], Any],
    enforce_rate_limit: Callable[[str, str], None],
    model_router: Any = None,
) -> APIRouter:
    """The remaining v11.6.0 delegation seams, wired to what the worker holds.

    ``history_deps`` is a :class:`~latticeai.runtime.history_writer.HistoryWriterDeps`
    (or ``None`` when this process has no conversation store). ``graph_store``
    is accepted for call-site stability after W3b retired
    ``POST /worker/graph/mutate``; it is unused. ``model_router`` is the
    in-process :class:`~latticeai.models.router.LLMRouter`; absent, the stream
    seam yields the same ``No model.`` marker the router itself would.
    """
    _ = graph_store
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

    @router.post("/worker/chat/record-turn")
    async def worker_record_turn(req: RecordTurnRequest, request: Request):
        """Persist one chat turn: redact → audit → store → ingest.

        The chain is :func:`write_chat_turn` verbatim, so a turn recorded
        through this seam is byte-identical to one the Python chat route wrote:
        same redaction, same audit event, same store row, same ingest door.
        What differs is only that the receipts come back.
        """
        _require_seam(request)
        _admit(request)
        language = resolve_language(request)
        if history_deps is None:
            raise http_error(503, "worker_seam.history_unavailable", language)
        role = req.role.strip().lower()
        if role not in CHAT_ROLES:
            raise http_error(
                422,
                "worker_seam.role_invalid",
                language,
                role=req.role,
                allowed=", ".join(CHAT_ROLES),
            )
        recorder = _TurnRecorder(history_deps)

        def _run() -> None:
            write_chat_turn(
                role,
                req.message,
                user_email=req.user_email,
                user_nickname=req.user_nickname,
                source=req.source,
                conversation_id=req.conversation_id,
                workspace_id=req.workspace_id,
                deps=recorder.deps,
            )

        # Redaction, an audit append, a SQLite insert and an embedding pass —
        # all blocking, none of it belonging on the one event loop (10.9.0).
        await asyncio.to_thread(_run)
        return recorder.receipt()

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
    "CHAT_ROLES",
    "WORKER_SEAM_MESSAGES",
    "LlmStreamRequest",
    "RecordTurnRequest",
    "create_worker_seams_router",
    "probe_gpu_memory",
    "register_worker_seam_messages",
]
