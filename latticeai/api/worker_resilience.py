from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = "x-lattice-idempotency-key"
REPLAY_HEADER = "x-lattice-idempotent-replay"
DEFAULT_COMPUTE_TIMEOUT_S = 60.0
DEFAULT_CACHE_MAX_ENTRIES = 512
DEFAULT_CACHE_TTL_S = 300.0

STREAMING_WORKER_PATHS = frozenset(
    {
        "/worker/llm/stream",
        "/engines/prepare-model/stream",
    }
)


def resolve_compute_timeout_s() -> float:
    raw = os.environ.get("LATTICEAI_WORKER_COMPUTE_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_COMPUTE_TIMEOUT_S
    try:
        parsed = float(raw)
    except ValueError:
        return DEFAULT_COMPUTE_TIMEOUT_S
    return max(0.05, parsed)


@dataclass(frozen=True)
class CachedComputeResponse:
    body_sha256: str
    status_code: int
    body: bytes
    media_type: str
    stored_at: float


class WorkerIdempotencyStore:
    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
        ttl_s: float = DEFAULT_CACHE_TTL_S,
    ) -> None:
        self._max_entries = max(16, int(max_entries))
        self._ttl_s = max(1.0, float(ttl_s))
        self._entries: "OrderedDict[Tuple[str, str, str], CachedComputeResponse]" = (
            OrderedDict()
        )
        self._inflight: Dict[
            Tuple[str, str, str], Tuple[str, asyncio.Future[CachedComputeResponse]]
        ] = {}

    def _evict_expired(self, now: float) -> None:
        stale = [
            key
            for key, entry in self._entries.items()
            if (now - entry.stored_at) > self._ttl_s
        ]
        for key in stale:
            self._entries.pop(key, None)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def lookup(
        self,
        scope_key: Tuple[str, str, str],
        body_sha256: str,
        *,
        now: Optional[float] = None,
    ) -> Tuple[Optional[CachedComputeResponse], bool]:
        ts = time.monotonic() if now is None else now
        self._evict_expired(ts)
        cached = self._entries.get(scope_key)
        if cached is None:
            return None, False
        if cached.body_sha256 != body_sha256:
            return None, True
        self._entries.move_to_end(scope_key)
        return cached, False

    def get_inflight(
        self, scope_key: Tuple[str, str, str], body_sha256: str
    ) -> Tuple[Optional[asyncio.Future[CachedComputeResponse]], bool]:
        existing = self._inflight.get(scope_key)
        if existing is None:
            return None, False
        inflight_hash, fut = existing
        if inflight_hash != body_sha256:
            return None, True
        return fut, False

    def register_inflight(
        self, scope_key: Tuple[str, str, str], body_sha256: str
    ) -> asyncio.Future[CachedComputeResponse]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[CachedComputeResponse] = loop.create_future()
        self._inflight[scope_key] = (body_sha256, fut)
        return fut

    def finish_inflight_success(
        self,
        scope_key: Tuple[str, str, str],
        entry: CachedComputeResponse,
        *,
        cacheable: bool,
    ) -> None:
        inflight = self._inflight.pop(scope_key, None)
        if cacheable:
            self._evict_expired(entry.stored_at)
            self._entries[scope_key] = entry
            self._entries.move_to_end(scope_key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        if inflight is not None and not inflight[1].done():
            inflight[1].set_result(entry)

    def finish_inflight_error(
        self, scope_key: Tuple[str, str, str], exc: BaseException
    ) -> None:
        inflight = self._inflight.pop(scope_key, None)
        if inflight is not None and not inflight[1].done():
            inflight[1].set_exception(exc)
            inflight[1].exception()


def _eligible_compute_request(request: Request) -> bool:
    path = request.url.path
    if path in STREAMING_WORKER_PATHS:
        return False
    return path.startswith("/worker/")


def _to_response(entry: CachedComputeResponse, *, replay: bool) -> Response:
    headers = {REPLAY_HEADER: "true" if replay else "false"}
    return Response(
        content=entry.body,
        status_code=entry.status_code,
        media_type=entry.media_type,
        headers=headers,
    )


def install_worker_compute_resilience(
    app: FastAPI,
    *,
    store: Optional[WorkerIdempotencyStore] = None,
) -> WorkerIdempotencyStore:
    existing = getattr(app.state, "worker_idempotency_store", None)
    if existing is not None and store is None:
        return existing

    idem_store = store or WorkerIdempotencyStore()
    app.state.worker_idempotency_store = idem_store

    @app.middleware("http")
    async def _worker_compute_resilience(request: Request, call_next):
        if not _eligible_compute_request(request):
            return await call_next(request)

        timeout_s = resolve_compute_timeout_s()
        idem_key = request.headers.get(IDEMPOTENCY_HEADER, "").strip()

        if not idem_key:
            try:
                return await asyncio.wait_for(call_next(request), timeout=timeout_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "worker compute timeout after %.2fs on %s",
                    timeout_s,
                    request.url.path,
                )
                return JSONResponse(
                    status_code=504,
                    content={
                        "detail": f"worker compute timed out after {timeout_s:.2f}s",
                        "code": "worker_compute_timeout",
                        "retryable": True,
                    },
                )
            except Exception:
                logger.exception(
                    "unhandled worker compute failure on %s", request.url.path
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "worker compute isolated an unexpected error",
                        "code": "worker_compute_isolated_fault",
                        "retryable": True,
                    },
                )

        raw_body = await request.body()
        body_sha = hashlib.sha256(raw_body).hexdigest()
        scope_key = (request.method.upper(), request.url.path, idem_key)

        cached, conflict = idem_store.lookup(scope_key, body_sha)
        if conflict:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "idempotency key reused with a different request payload",
                    "code": "idempotency_payload_mismatch",
                },
            )
        if cached is not None:
            return _to_response(cached, replay=True)

        inflight_fut, inflight_conflict = idem_store.get_inflight(scope_key, body_sha)
        if inflight_conflict:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "idempotency key currently in flight with a different payload",
                    "code": "idempotency_payload_mismatch",
                },
            )
        if inflight_fut is not None:
            try:
                joined = await asyncio.wait_for(
                    asyncio.shield(inflight_fut), timeout=timeout_s
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "coalesced worker compute timeout after %.2fs on %s",
                    timeout_s,
                    request.url.path,
                )
                return JSONResponse(
                    status_code=504,
                    content={
                        "detail": f"worker compute timed out after {timeout_s:.2f}s",
                        "code": "worker_compute_timeout",
                        "retryable": True,
                    },
                )
            except Exception:
                logger.exception(
                    "coalesced worker compute observed leader failure on %s",
                    request.url.path,
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "worker compute isolated an unexpected error",
                        "code": "worker_compute_isolated_fault",
                        "retryable": True,
                    },
                )
            return _to_response(joined, replay=True)

        async def _execute_and_buffer() -> Tuple[Response, bytes]:
            resp = await call_next(request)
            chunks = [chunk async for chunk in resp.body_iterator]
            return resp, b"".join(chunks)

        idem_store.register_inflight(scope_key, body_sha)
        try:
            response, payload_bytes = await asyncio.wait_for(
                _execute_and_buffer(), timeout=timeout_s
            )
            media_type = response.media_type or response.headers.get(
                "content-type", "application/json"
            )
            entry = CachedComputeResponse(
                body_sha256=body_sha,
                status_code=response.status_code,
                body=payload_bytes,
                media_type=media_type.split(";")[0].strip() or "application/json",
                stored_at=time.monotonic(),
            )
            idem_store.finish_inflight_success(
                scope_key,
                entry,
                cacheable=(200 <= response.status_code < 300),
            )
            passthrough_headers = dict(response.headers)
            passthrough_headers[REPLAY_HEADER] = "false"
            passthrough_headers.pop("content-length", None)
            return Response(
                content=payload_bytes,
                status_code=response.status_code,
                media_type=entry.media_type,
                headers=passthrough_headers,
            )
        except asyncio.TimeoutError as exc:
            idem_store.finish_inflight_error(scope_key, exc)
            logger.warning(
                "worker compute timeout after %.2fs on %s", timeout_s, request.url.path
            )
            return JSONResponse(
                status_code=504,
                content={
                    "detail": f"worker compute timed out after {timeout_s:.2f}s",
                    "code": "worker_compute_timeout",
                    "retryable": True,
                },
            )
        except Exception as exc:
            idem_store.finish_inflight_error(scope_key, exc)
            logger.exception("unhandled worker compute failure on %s", request.url.path)
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "worker compute isolated an unexpected error",
                    "code": "worker_compute_isolated_fault",
                    "retryable": True,
                },
            )
        except BaseException as exc:
            idem_store.finish_inflight_error(scope_key, exc)
            raise

    return idem_store


__all__ = [
    "CachedComputeResponse",
    "DEFAULT_COMPUTE_TIMEOUT_S",
    "IDEMPOTENCY_HEADER",
    "REPLAY_HEADER",
    "STREAMING_WORKER_PATHS",
    "WorkerIdempotencyStore",
    "install_worker_compute_resilience",
    "resolve_compute_timeout_s",
]
