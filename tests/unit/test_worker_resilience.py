"""Unit tests for stateless Python worker timeout isolation and idempotency."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request

from latticeai.api.worker_resilience import (
    IDEMPOTENCY_HEADER,
    REPLAY_HEADER,
    WorkerIdempotencyStore,
    install_worker_compute_resilience,
    resolve_compute_timeout_s,
)


def _build_test_app() -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    counts = {"embed": 0, "boom": 0}
    install_worker_compute_resilience(app, store=WorkerIdempotencyStore(max_entries=16, ttl_s=30.0))

    @app.post("/worker/embed")
    async def _embed(request: Request) -> dict[str, object]:
        payload = await request.json()
        counts["embed"] += 1
        delay = float(payload.get("delay", 0.0))
        if delay > 0:
            await asyncio.sleep(delay)
        return {"vectors": [[1.0, 0.0]], "calls": counts["embed"], "echo": payload.get("text")}

    @app.post("/worker/boom")
    async def _boom() -> dict[str, object]:
        counts["boom"] += 1
        raise RuntimeError("unhandled backend explosion")

    @app.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    return app, counts


def test_idempotency_replays_cached_response_and_rejects_payload_mismatch() -> None:
    async def _run() -> None:
        app, counts = _build_test_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post(
                "/worker/embed",
                headers={IDEMPOTENCY_HEADER: "idem-001"},
                json={"text": "hello brain"},
            )
            assert r1.status_code == 200
            assert r1.headers[REPLAY_HEADER] == "false"
            assert r1.json()["calls"] == 1

            r2 = await client.post(
                "/worker/embed",
                headers={IDEMPOTENCY_HEADER: "idem-001"},
                json={"text": "hello brain"},
            )
            assert r2.status_code == 200
            assert r2.headers[REPLAY_HEADER] == "true"
            assert r2.json()["calls"] == 1
            assert counts["embed"] == 1

            mismatch = await client.post(
                "/worker/embed",
                headers={IDEMPOTENCY_HEADER: "idem-001"},
                json={"text": "mutated payload"},
            )
            assert mismatch.status_code == 409
            assert mismatch.json()["code"] == "idempotency_payload_mismatch"

    asyncio.run(_run())


def test_idempotency_coalesces_concurrent_identical_requests() -> None:
    async def _run() -> None:
        app, counts = _build_test_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1, r2 = await asyncio.gather(
                client.post(
                    "/worker/embed",
                    headers={IDEMPOTENCY_HEADER: "idem-concurrent"},
                    json={"text": "same", "delay": 0.05},
                ),
                client.post(
                    "/worker/embed",
                    headers={IDEMPOTENCY_HEADER: "idem-concurrent"},
                    json={"text": "same", "delay": 0.05},
                ),
            )
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert counts["embed"] == 1
            assert {r1.headers[REPLAY_HEADER], r2.headers[REPLAY_HEADER]} == {"false", "true"}

    asyncio.run(_run())


def test_timeout_and_fault_isolation_keep_worker_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("LATTICEAI_WORKER_COMPUTE_TIMEOUT_S", "0.05")
        assert resolve_compute_timeout_s() == pytest.approx(0.05)

        app, _ = _build_test_app()
        # Second install call is a safe no-op returning existing store.
        assert install_worker_compute_resilience(app) is app.state.worker_idempotency_store

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            timed_out = await client.post("/worker/embed", json={"text": "slow", "delay": 0.25})
            assert timed_out.status_code == 504
            assert timed_out.json()["code"] == "worker_compute_timeout"

            timed_out_idem = await client.post(
                "/worker/embed",
                headers={IDEMPOTENCY_HEADER: "idem-slow"},
                json={"text": "slow", "delay": 0.25},
            )
            assert timed_out_idem.status_code == 504

            boom = await client.post("/worker/boom", json={})
            assert boom.status_code == 503
            assert boom.json()["code"] == "worker_compute_isolated_fault"

            boom_idem = await client.post(
                "/worker/boom",
                headers={IDEMPOTENCY_HEADER: "idem-boom"},
                json={},
            )
            assert boom_idem.status_code == 503

            # Subsequent fast requests still succeed cleanly.
            monkeypatch.setenv("LATTICEAI_WORKER_COMPUTE_TIMEOUT_S", "invalid-float")
            assert resolve_compute_timeout_s() == pytest.approx(60.0)
            ok = await client.post("/worker/embed", json={"text": "recovered"})
            assert ok.status_code == 200
            health = await client.get("/health")
            assert health.status_code == 200

    asyncio.run(_run())
