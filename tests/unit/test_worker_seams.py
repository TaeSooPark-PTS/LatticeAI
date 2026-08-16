"""The v11.6.0 worker seams: what a Rust route package may make Python do.

Two chains stay in Python after the front door moves to ``lattice-host``, and
each one is here for a different reason — unified memory can only be read from
the process holding the MLX context, and token generation never moves. So the
assertions that matter are not "does it return 200": they are the gate, the
whitelist, and the honesty of the readings.

``POST /worker/chat/record-turn`` was the third, and this file used to drive it
through the **real** ``write_chat_turn`` over fake stores. WP-W3a moved that
whole chain into ``lattice-chat`` (its own tests assert the seam is requested
zero times) and WP-P1 deleted the handler with the conversation store it wrote
through, so its cases went with it.

The MLX read is driven through a fake ``mlx.core`` module and a fake
``subprocess.run`` (the technique ``test_cov_wp17b_static_routes.py`` uses), so
the Apple-Silicon-only branch and the "no GPU here" branch both run on every
platform instead of only on the machine that has one.
"""

from __future__ import annotations

import subprocess
import sys
import types
from typing import Any, List, Optional

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api import worker_seams
from latticeai.api.agent_worker_seam import SEAM_ENV_VAR, SEAM_RATE_BUCKET
from latticeai.api.worker_seams import create_worker_seams_router, probe_gpu_memory
from latticeai.core.messages import MESSAGES

USER = "worker@local"
MEMSIZE_16GB = str(16 * 1024 ** 3) + "\n"


# ── fakes ───────────────────────────────────────────────────────────────────


class RecordingLimiter:
    def __init__(self) -> None:
        self.calls: List[Any] = []

    def __call__(self, email: str, bucket: str) -> None:
        self.calls.append((email, bucket))


def _client(
    *,
    user: Optional[str] = USER,
    limiter: Any = None,
    model_router: Any = None,
) -> TestClient:
    def require_user(_request: Request) -> str:
        if user is None:
            raise HTTPException(status_code=401, detail="auth required")
        return user

    app = FastAPI()
    app.include_router(
        create_worker_seams_router(
            require_user=require_user,
            enforce_rate_limit=limiter if limiter is not None else RecordingLimiter(),
            model_router=model_router,
        )
    )
    return TestClient(app)


@pytest.fixture
def seam_on(monkeypatch):
    monkeypatch.setenv(SEAM_ENV_VAR, "1")


@pytest.fixture
def seam_off(monkeypatch):
    monkeypatch.delenv(SEAM_ENV_VAR, raising=False)


# ── the gate: both seams are the host's, or nobody's ────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/worker/sysinfo", None),
        ("post", "/worker/llm/stream", {"message": "hi"}),
    ],
)
def test_every_seam_is_absent_until_the_host_opens_it(seam_off, method, path, body):
    client = _client()
    call = getattr(client, method)
    response = call(path) if body is None else call(path, json=body)
    assert response.status_code == 404
    assert response.json()["detail"] == MESSAGES["agent_seam.disabled"]["ko"]


def test_the_gate_reads_the_environment_per_request(monkeypatch):
    """Not at import: a host that sets the switch after start-up still opens it."""
    client = _client()
    monkeypatch.delenv(SEAM_ENV_VAR, raising=False)
    assert client.get("/worker/sysinfo").status_code == 404
    monkeypatch.setenv(SEAM_ENV_VAR, "1")
    assert client.get("/worker/sysinfo").status_code == 200


def test_an_unauthenticated_caller_is_refused_before_anything_runs(seam_on):
    response = _client(user=None).get("/worker/sysinfo")
    assert response.status_code == 401


def test_each_seam_call_is_charged_to_the_per_step_budget(seam_on):
    limiter = RecordingLimiter()
    client = _client(limiter=limiter)
    client.get("/worker/sysinfo")
    client.post("/worker/llm/stream", json={"message": "hi"})
    assert limiter.calls == [(USER, SEAM_RATE_BUCKET)] * 2


# ── GET /worker/sysinfo ─────────────────────────────────────────────────────


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


def _fake_sysctl(monkeypatch, stdout: str) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_kwargs: _Completed(stdout))


def _install_fake_mlx(monkeypatch, *, active_bytes: int, cache_bytes: int) -> None:
    """Make the Apple-Silicon-only unified-memory read executable anywhere."""
    mlx = types.ModuleType("mlx")
    core = types.ModuleType("mlx.core")
    core.get_active_memory = lambda: active_bytes
    core.get_cache_memory = lambda: cache_bytes
    mlx.core = core
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", core)


def _hide_mlx(monkeypatch) -> None:
    """``None`` in sys.modules makes the import fail on every platform."""
    monkeypatch.setitem(sys.modules, "mlx", None)
    monkeypatch.setitem(sys.modules, "mlx.core", None)


def test_the_probe_reports_unified_memory_the_way_local_sysinfo_does(monkeypatch):
    _install_fake_mlx(monkeypatch, active_bytes=2 * 1024 ** 3, cache_bytes=1024 ** 3 // 2)
    _fake_sysctl(monkeypatch, MEMSIZE_16GB)

    result = probe_gpu_memory()
    assert result["mlx_available"] is True
    assert result["gpu_mem_gb"] == 2.5               # active + cache, unified memory
    assert result["gpu_mem_pct"] == 15.6             # 2.5 GB of 16 GB
    assert result["total_bytes"] == 16 * 1024 ** 3
    assert result["detail"] is None
    assert "capabilities" in result
    assert "pointer_tools" in result["capabilities"]
    assert isinstance(result["capabilities"]["pointer_tools"], bool)
    assert result["python_version"]


def test_a_host_that_reports_no_memory_at_all_yields_zero_not_a_zero_division(monkeypatch):
    _install_fake_mlx(monkeypatch, active_bytes=1024, cache_bytes=0)
    _fake_sysctl(monkeypatch, "0\n")

    result = probe_gpu_memory()
    assert result["gpu_mem_pct"] == 0.0
    assert result["mlx_available"] is True


def test_a_machine_without_mlx_says_so_rather_than_reporting_a_fake_zero(monkeypatch):
    _hide_mlx(monkeypatch)
    _fake_sysctl(monkeypatch, MEMSIZE_16GB)

    result = probe_gpu_memory()
    assert result["mlx_available"] is False
    assert result["gpu_mem_gb"] == 0.0
    assert result["gpu_mem_pct"] == 0.0
    assert result["total_bytes"] == 0
    assert result["detail"]


def test_a_sysctl_that_cannot_be_read_is_a_missing_reading_not_a_500(monkeypatch):
    _install_fake_mlx(monkeypatch, active_bytes=1024, cache_bytes=0)

    def _boom(cmd, **_kwargs):
        raise OSError("sysctl: command not found")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert probe_gpu_memory()["detail"] == "sysctl: command not found"


def test_the_route_answers_with_the_probe(seam_on, monkeypatch):
    monkeypatch.setattr(
        worker_seams,
        "probe_gpu_memory",
        lambda: {"mlx_available": True, "gpu_mem_gb": 1.0},
    )
    response = _client().get("/worker/sysinfo")
    assert response.status_code == 200
    assert response.json() == {"mlx_available": True, "gpu_mem_gb": 1.0}


# ── POST /worker/llm/stream ─────────────────────────────────────────────────


class FakeRouter:
    """The in-process LLMRouter, reduced to the two stream methods chat calls."""

    def __init__(
        self,
        chunks: Optional[List[str]] = None,
        *,
        boom: bool = False,
        document_chunks: Optional[List[str]] = None,
    ) -> None:
        self.chunks = ["Hello", " world"] if chunks is None else chunks
        self.document_chunks = ["Doc"] if document_chunks is None else document_chunks
        self.boom = boom
        self.calls: List[Any] = []

    async def stream_generate_as(
        self, model_id, message, context, max_tokens, temperature, image_data
    ):
        self.calls.append(
            ("chat", model_id, message, context, max_tokens, temperature, image_data)
        )
        if self.boom:
            raise RuntimeError("mlx exploded")
        for chunk in self.chunks:
            yield chunk

    async def stream_generate_document_as(
        self, model_id, message, context, max_tokens, temperature
    ):
        self.calls.append(
            ("document", model_id, message, context, max_tokens, temperature)
        )
        if self.boom:
            raise RuntimeError("mlx exploded")
        for chunk in self.document_chunks:
            yield chunk


def _sse_payloads(body: str) -> List[str]:
    frames = []
    for block in body.split("\n\n"):
        line = block.strip()
        if line.startswith("data:"):
            frames.append(line[len("data:") :].strip())
    return frames


def test_the_llm_stream_passthrough_uses_the_chat_router_path(seam_on):
    router = FakeRouter()
    response = _client(model_router=router).post(
        "/worker/llm/stream",
        json={
            "model_id": "m",
            "message": "hi",
            "context": "ctx",
            "max_tokens": 32,
            "temperature": 0.1,
            "image_data": None,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert _sse_payloads(response.text) == [
        '{"text": "Hello"}',
        '{"text": " world"}',
        "[DONE]",
    ]
    assert router.calls == [("chat", "m", "hi", "ctx", 32, 0.1, None)]


def test_the_document_mode_selects_the_document_stream(seam_on):
    router = FakeRouter(document_chunks=["# title"])
    response = _client(model_router=router).post(
        "/worker/llm/stream",
        json={"message": "보고서", "context": "SYS", "mode": "document"},
    )
    assert _sse_payloads(response.text) == ['{"text": "# title"}', "[DONE]"]
    assert router.calls[0][0] == "document"


def test_a_worker_without_a_router_yields_the_no_model_marker(seam_on):
    response = _client(model_router=None).post(
        "/worker/llm/stream", json={"message": "hi"}
    )
    assert response.status_code == 200
    assert _sse_payloads(response.text) == ['{"text": "No model."}', "[DONE]"]


def test_a_stream_failure_is_an_error_frame_not_a_500(seam_on):
    response = _client(model_router=FakeRouter(boom=True)).post(
        "/worker/llm/stream", json={"message": "hi"}
    )
    assert response.status_code == 200
    assert _sse_payloads(response.text) == [
        '{"error": "mlx exploded"}',
        "[DONE]",
    ]


def test_a_chunk_object_with_a_text_attribute_is_unwrapped(seam_on):
    class _Piece:
        def __init__(self, text: str) -> None:
            self.text = text

    class _ObjRouter(FakeRouter):
        async def stream_generate_as(self, *args):
            self.calls.append(args)
            yield _Piece("token")

    response = _client(model_router=_ObjRouter()).post(
        "/worker/llm/stream", json={"message": "hi"}
    )
    assert _sse_payloads(response.text) == ['{"text": "token"}', "[DONE]"]
