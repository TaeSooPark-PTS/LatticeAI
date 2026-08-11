"""Index jobs API (v11.5.0, plan §3a) — the trigger the embed queue never had.

``vector_jobs`` has been a durable backlog since v11.1.0: an ingest whose
inline embedding fails lands anyway, the node is queued, and
``VectorEmbedQueue.tick`` will embed it *when someone runs a tick*. Nobody did.
The only drain in the product was
:meth:`~lattice_brain.ingestion.IngestionPipeline.drain_vector_queue`, callable
from a test or a REPL and from nothing that runs on its own — which is what
FEATURE_STATUS.md:179-185 says out loud.

This router is the missing half: an HTTP surface a scheduler can call.

* ``POST /api/index/drain`` — run one tick now, answer with what it did and
  what is left.
* ``GET  /api/index/queue`` — the backlog counts, read-only.

Their two siblings under the same prefix live in ``search.py``
(``/api/index/status``, ``/api/index/rebuild``): those answer *is the index
complete* and *rebuild it from scratch*, which is the operator's hammer. This
pair is the small, repeatable step a timer can take every minute.

Two honest boundaries, stated here so the payloads are not read as more than
they are:

* **The backlog is machine-wide.** One SQLite queue serves every workspace, so
  a drain embeds whatever is owed regardless of who asked, and the counts are
  totals for this Brain. The workspace gate still runs on both paths — it is
  the authorization check, not a filter — and the drain payload says
  ``scope: "machine"`` rather than implying a scoped number.
* **Draining is not indexing.** The tick delegates to the store's own indexer;
  a node that fails goes back to ``pending`` until its retry budget runs out.
  The response reports ``claimed``/``indexed``/``retried``/``failed`` verbatim
  from the queue instead of summarising them into a success flag.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from lattice_brain.graph.vector_index import DEFAULT_TICK_LIMIT, VECTOR_JOB_STATUSES
from latticeai.core.messages import http_error, resolve_language

#: Bounds on one drain. The floor keeps a caller from asking for a no-op tick
#: that still claims the queue's lock; the ceiling keeps one HTTP request from
#: turning into an unbounded embedding run behind a client that will time out.
MIN_DRAIN_LIMIT = 1
MAX_DRAIN_LIMIT = 100


class DrainRequest(BaseModel):
    """How many queued nodes one drain may claim.

    Omitted (or a body-less POST) means the queue's own tick size, so a caller
    with no opinion inherits the number the queue was designed around.
    """

    limit: Optional[int] = None


def create_index_jobs_router(
    *,
    pipeline: Any,
    knowledge_graph: Any,
    require_user: Callable[[Request], Any],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
) -> APIRouter:
    router = APIRouter()

    def _require_pipeline(request: Request) -> Any:
        """The pipeline, or 503 — a drain without ingestion is not a 500."""
        if pipeline is None or not pipeline.available():
            raise http_error(503, "capture.ingestion_disabled", resolve_language(request))
        return pipeline

    def _queue_state() -> Dict[str, Any]:
        """Backlog counts, or an explicit "not tracked" — never a fake zero.

        A store without a durable queue reports ``available: false`` alongside
        its zeros, because "0 pending" and "nothing is counting" are different
        answers and a scheduler polling this would otherwise read the second as
        the first.
        """
        queue = getattr(knowledge_graph, "vector_queue", None)
        if queue is None or not queue.available:
            return {
                "available": False,
                "counts": dict.fromkeys(VECTOR_JOB_STATUSES, 0),
                "pending": 0,
            }
        return {
            "available": True,
            "counts": dict(queue.snapshot()),
            "pending": int(queue.pending_count()),
        }

    @router.post("/api/index/drain")
    async def drain_index_queue(request: Request, req: Optional[DrainRequest] = None):
        """Run one background-embedding tick and report what it did.

        Off the event loop: the tick opens SQLite and calls the embedder, and
        this server has one loop for every user (10.9.0).
        """
        require_user(request)
        gate_write(request)
        active = _require_pipeline(request)
        requested = req.limit if req is not None else None
        limit = DEFAULT_TICK_LIMIT if requested is None else requested
        if limit < MIN_DRAIN_LIMIT or limit > MAX_DRAIN_LIMIT:
            raise http_error(
                422,
                "index.limit_out_of_range",
                resolve_language(request),
                min=MIN_DRAIN_LIMIT,
                max=MAX_DRAIN_LIMIT,
            )
        tick = await asyncio.to_thread(active.drain_vector_queue, limit)
        return {
            **dict(tick),
            "limit": limit,
            "scope": "machine",
            "queue": _queue_state(),
        }

    @router.get("/api/index/queue")
    async def index_queue(request: Request):
        """The embed backlog, counted. Reads nothing else and writes nothing."""
        require_user(request)
        gate_read(request)
        _require_pipeline(request)
        return _queue_state()

    return router


__all__ = [
    "MAX_DRAIN_LIMIT",
    "MIN_DRAIN_LIMIT",
    "DrainRequest",
    "create_index_jobs_router",
]
