"""The server owns exactly one event loop, and long work may not sit on it.

Through 10.8.0 several `async def` handlers called blocking work directly:
`ollama pull` and engine install with a 900-second timeout, the three-subprocess
host probe behind `/local/sysinfo`, package installers behind MCP install. While
any of those ran, the loop could not service *anything* — not another chat
stream, not `/health`, not the UI asking why. One admin downloading a model
froze the product for everyone on the machine.

The lint gate (ruff `ASYNC2xx`, enabled in 10.9.0) catches the syntactic shape.
These tests assert the behaviour the gate is a proxy for: the blocking body runs
on a *different* thread than the loop, and the loop keeps ticking while it does.
A future refactor that satisfies the linter without actually yielding — say by
hiding `subprocess.run` one call deeper — fails here.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api import static_routes
from latticeai.services import model_loading


def _loop_thread_ident() -> int:
    return threading.get_ident()


class _RecordingProbe:
    """Stands in for the blocking body; records where it ran and stalls a bit."""

    def __init__(self, stall: float = 0.05) -> None:
        self.stall = stall
        self.ran_on: int | None = None
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        self.ran_on = threading.get_ident()
        time.sleep(self.stall)
        return {"cpu_pct": 1.0, "ram_pct": 2.0, "gpu_mem_pct": 0.0, "readiness": "roomy"}


class _Resolution:
    def __init__(self, model_id, **_):
        self.load_id = model_id
        self.actual_current = None

    @classmethod
    def from_request(cls, model_id, **_):
        return cls(model_id)

    def update_after_load(self, *, actual_current):
        self.actual_current = actual_current

    def to_dict(self):
        return {"load_id": self.load_id}


class _Router:
    current_model_id = "ollama:llama3"

    async def load_model(self, model_id, adapter_path, **kwargs):
        return f"loaded {model_id}"


def _fake_deps() -> dict:
    """Only the collaborators `prepare_and_load_model` reaches outside the
    preparation step, which this test replaces wholesale."""

    async def _smoke(resolution, api_key_override=None):
        return {"ok": True, "status": "ok"}

    return {
        "normalize_local_model_request": lambda mid, engine: mid,
        "_ModelResolution": _Resolution,
        "parse_model_ref": lambda mid: tuple(mid.split(":", 1)) if ":" in mid else ("local_mlx", mid),
        "_model_runtime_compatibility": lambda model, engine=None: {"supported": True},
        "get_current_user": lambda request: "me@local",
        "get_user_api_key": lambda email, provider: None,
        "router": _Router(),
        "_smoke_test_loaded_model": _smoke,
        "MODEL_ENGINE_ALIASES": {},
    }


async def _ticks_during(awaitable) -> int:
    """Run `awaitable`, counting how many times the loop got control meanwhile.

    A blocked loop cannot run the ticker at all, so the count is the assertion:
    zero means the handler held the loop for its whole duration.
    """
    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.005)

    task = asyncio.ensure_future(ticker())
    try:
        await awaitable
    finally:
        stop = True
        await task
    return ticks


def test_probe_host_capacity_is_a_plain_sync_function():
    """The blocking body stays sync so it is callable from a worker thread.

    Making it `async` again would be the regression: it would then have to run
    on the loop, and the endpoint's `to_thread` hop would have nothing to hand
    off.
    """
    assert not inspect.iscoroutinefunction(static_routes._probe_host_capacity)


def test_sysinfo_route_leaves_the_loop_free(monkeypatch):
    probe = _RecordingProbe()
    monkeypatch.setattr(static_routes, "_probe_host_capacity", probe)

    bundle = static_routes.create_static_routes_router(
        static_dir=Path("static"),
        invite_gate_enabled=False,
        invite_code="",
        app_mode="local",
        model_router=type("R", (), {"_current": None})(),
        require_user=lambda request: "user@example.com",
    )
    request = type("Req", (), {"headers": {}, "cookies": {}})()

    async def scenario():
        loop_thread = _loop_thread_ident()
        ticks = await _ticks_during(bundle.local_sysinfo(request))
        return loop_thread, ticks

    loop_thread, ticks = asyncio.run(scenario())

    assert probe.calls == 1
    assert probe.ran_on is not None
    assert probe.ran_on != loop_thread, "host probe ran on the event loop thread"
    assert ticks > 0, "the loop never got control while the host probe ran"


def test_model_source_preparation_leaves_the_loop_free(monkeypatch):
    """Engine install + weight download is the worst offender: up to 900s each.

    This drives the shipped `prepare_and_load_model`, so an inlining that
    satisfied the linter but put the work back on the loop fails here.
    """
    prepare = _RecordingProbe(stall=0.05)

    def _prepare(deps, provider, model, model_id, allow_download):
        prepare(deps, provider, model, model_id, allow_download)
        return {
            "install_result": {},
            "download_result": None,
            "parsed_model": model,
            "model_id": model_id,
        }

    monkeypatch.setattr(model_loading, "_prepare_model_sources", _prepare)
    monkeypatch.setattr(model_loading, "_get_model_runtime_deps", lambda state: _fake_deps())

    async def scenario():
        loop_thread = _loop_thread_ident()
        ticks = await _ticks_during(
            model_loading.prepare_and_load_model(
                "ollama:llama3", request=object(), runtime_state=object(), allow_download=True
            )
        )
        return loop_thread, ticks

    loop_thread, ticks = asyncio.run(scenario())

    assert prepare.calls == 1
    assert prepare.ran_on is not None
    assert prepare.ran_on != loop_thread, "model preparation ran on the event loop thread"
    assert ticks > 0, "the loop never got control while a model was being prepared"


def test_prepare_model_sources_is_a_plain_sync_function():
    assert not inspect.iscoroutinefunction(model_loading._prepare_model_sources)

