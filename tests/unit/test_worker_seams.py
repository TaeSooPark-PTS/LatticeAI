"""The v11.6.0 worker seams: what a Rust route package may make Python do.

Three chains stay in Python after the front door moves to ``lattice-host``, and
each one is here for a different reason — the history writer is four
dependencies deep, the graph has a single writer by design, and unified memory
can only be read from the process holding the MLX context. So the assertions
that matter are not "does it return 200": they are the gate, the whitelist, and
the honesty of the receipts.

The history chain is driven through the **real**
``latticeai.runtime.history_writer.write_chat_turn`` over fake stores, because
a test that faked the writer could pass while the seam skipped redaction. The
MLX read is driven through a fake ``mlx.core`` module and a fake
``subprocess.run`` (the technique ``test_cov_wp17b_static_routes.py`` uses), so
the Apple-Silicon-only branch and the "no GPU here" branch both run on every
platform instead of only on the machine that has one.
"""

from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api import worker_seams
from latticeai.api.agent_worker_seam import SEAM_ENV_VAR, SEAM_RATE_BUCKET
from latticeai.api.worker_seams import (
    CHAT_ROLES,
    WORKER_SEAM_MESSAGES,
    create_worker_seams_router,
    probe_gpu_memory,
)
from latticeai.core.messages import LANGUAGE_HEADER, MESSAGES
from latticeai.runtime.history_writer import HistoryWriterDeps

USER = "worker@local"
MEMSIZE_16GB = str(16 * 1024 ** 3) + "\n"


# ── fakes ───────────────────────────────────────────────────────────────────


class FakeConversations:
    """The durable store: keeps what it was handed, or refuses to take it."""

    def __init__(self, *, boom: bool = False) -> None:
        self.items: List[Dict[str, Any]] = []
        self.boom = boom

    def append(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if self.boom:
            raise RuntimeError("disk full")
        self.items.append(dict(item))
        return item


@dataclass
class FakeIngestResult:
    """An ``IngestionResult``-shaped receipt: the ids the chain produced."""

    node_id: str

    def as_dict(self) -> Dict[str, Any]:
        return {"status": "ok", "node_id": self.node_id, "chunk_ids": ["c1"]}


class FakePipeline:
    """The ingest door. ``result`` may be a plain dict to prove no shape is assumed."""

    def __init__(self, result: Any = None, *, boom: bool = False) -> None:
        self.result = result if result is not None else FakeIngestResult("node-1")
        self.boom = boom
        self.calls: List[Dict[str, Any]] = []

    def ingest(self, item: Any, **kwargs: Any) -> Any:
        self.calls.append({"item": item, **kwargs})
        if self.boom:
            raise RuntimeError("embedder offline")
        return self.result


class FakeGraph:
    """The single writer, with each whitelisted method recorded."""

    def __init__(self, *, boom: bool = False) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.boom = boom

    def _record(self, op: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"op": op, **kwargs})
        if self.boom:
            raise RuntimeError("database is locked")
        return {"status": "ok", "op": op}

    def curate(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("curate", kwargs)

    def curate_noise(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("curate_noise", kwargs)

    def apply_pending_promotions(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("apply_pending_promotions", kwargs)

    def reject_pending_promotions(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("reject_pending_promotions", kwargs)

    def rebuild_vector_index(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("rebuild_vector_index", kwargs)

    def ingest_event(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("ingest_event", kwargs)

    def set_node_sensitivity(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("set_node_sensitivity", kwargs)

    def import_graph_data(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("import_graph_data", kwargs)

    def delete_document_tree(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("delete_document_tree", kwargs)

    def set_local_source_watch(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("set_local_source_watch", kwargs)

    def remove_local_source(self, **kwargs: Any) -> Dict[str, Any]:
        return self._record("remove_local_source", kwargs)


class RecordingLimiter:
    def __init__(self) -> None:
        self.calls: List[Any] = []

    def __call__(self, email: str, bucket: str) -> None:
        self.calls.append((email, bucket))


class AuditSink:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def __call__(self, name: str, **fields: Any) -> None:
        self.events.append({"event": name, **fields})


def _deps(
    *,
    conversations: Any = None,
    pipeline: Any = None,
    enable_graph: bool = True,
    audit: Any = None,
) -> HistoryWriterDeps:
    """The real dependency bundle, with only the storage sinks faked."""
    return HistoryWriterDeps(
        conversations=conversations if conversations is not None else FakeConversations(),
        append_audit_event=audit if audit is not None else AuditSink(),
        classify_sensitive_message=lambda item, index: {
            "preview": str(item.get("content"))[:20],
            "sensitivity": "none",
            "labels": [],
        },
        redact_secret_text=lambda text: text.replace("sk-SECRET", "[REDACTED]"),
        normalize_branding=lambda text: text.replace("ChatGPT", "Lattice AI"),
        ingestion_pipeline=pipeline,
        ingestion_item_factory=lambda **kwargs: dict(kwargs),
        enable_graph=enable_graph,
        knowledge_graph=object() if enable_graph else None,
    )


def _client(
    *,
    history_deps: Any = None,
    graph_store: Any = None,
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
            history_deps=history_deps,
            graph_store=graph_store,
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


# ── the catalog ─────────────────────────────────────────────────────────────


def test_every_new_message_is_registered_in_both_languages():
    """The seam's wording lives in the one shared catalog, not at the raise site."""
    for key, entry in WORKER_SEAM_MESSAGES.items():
        assert MESSAGES[key] == entry
        assert entry["ko"] and entry["en"] and entry["ko"] != entry["en"]


def test_registering_twice_never_overwrites_the_catalog():
    """So lifting these entries into messages.py later is a no-op, not a revert."""
    MESSAGES["worker_seam.role_invalid"] = {"ko": "이미 옮김", "en": "already moved"}
    try:
        worker_seams.register_worker_seam_messages()
        assert MESSAGES["worker_seam.role_invalid"]["en"] == "already moved"
    finally:
        MESSAGES["worker_seam.role_invalid"] = WORKER_SEAM_MESSAGES["worker_seam.role_invalid"]


# ── the gate: all three seams are the host's, or nobody's ───────────────────


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/worker/chat/record-turn", {"role": "user", "message": "hi"}),
        ("get", "/worker/sysinfo", None),
        ("post", "/worker/llm/stream", {"message": "hi"}),
    ],
)
def test_every_seam_is_absent_until_the_host_opens_it(seam_off, method, path, body):
    client = _client(history_deps=_deps(), graph_store=FakeGraph())
    call = getattr(client, method)
    response = call(path) if body is None else call(path, json=body)
    assert response.status_code == 404
    assert response.json()["detail"] == MESSAGES["agent_seam.disabled"]["ko"]


def test_the_gate_reads_the_environment_per_request(monkeypatch):
    """Not at import: a host that sets the switch after start-up still opens it."""
    client = _client(history_deps=_deps(), graph_store=FakeGraph())
    monkeypatch.delenv(SEAM_ENV_VAR, raising=False)
    assert client.get("/worker/sysinfo").status_code == 404
    monkeypatch.setenv(SEAM_ENV_VAR, "1")
    assert client.get("/worker/sysinfo").status_code == 200


def test_an_unauthenticated_caller_is_refused_before_anything_runs(seam_on):
    conversations = FakeConversations()
    response = _client(
        history_deps=_deps(conversations=conversations), user=None
    ).post("/worker/chat/record-turn", json={"role": "user", "message": "hi"})
    assert response.status_code == 401
    assert conversations.items == []


def test_each_seam_call_is_charged_to_the_per_step_budget(seam_on):
    limiter = RecordingLimiter()
    client = _client(history_deps=_deps(), graph_store=FakeGraph(), limiter=limiter)
    client.post("/worker/chat/record-turn", json={"role": "user", "message": "hi"})
    client.get("/worker/sysinfo")
    client.post("/worker/llm/stream", json={"message": "hi"})
    assert limiter.calls == [(USER, SEAM_RATE_BUCKET)] * 3


# ── POST /worker/chat/record-turn ───────────────────────────────────────────


def test_a_turn_is_redacted_audited_stored_and_ingested_in_that_order(seam_on):
    conversations = FakeConversations()
    pipeline = FakePipeline()
    audit = AuditSink()
    deps = _deps(conversations=conversations, pipeline=pipeline, audit=audit)

    response = _client(history_deps=deps).post(
        "/worker/chat/record-turn",
        json={
            "role": "user",
            "message": "my key is sk-SECRET",
            "user_email": "me@local",
            "user_nickname": "Me",
            "source": "rust",
            "conversation_id": "c-1",
            "workspace_id": "ws-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    # 1. redaction happened before anything downstream saw the text
    assert payload["item"]["content"] == "my key is [REDACTED]"
    assert conversations.items[0]["content"] == "my key is [REDACTED]"
    assert audit.events[0]["content_preview"] == "my key is [REDACTED]"
    # 2. the attribution the caller sent is carried through verbatim
    assert payload["item"]["user_email"] == "me@local"
    assert payload["item"]["conversation_id"] == "c-1"
    assert payload["item"]["workspace_id"] == "ws-1"
    assert payload["item"]["source"] == "rust"
    # 3. the receipts are the chain's own: the stored row and the ingest ids
    assert payload["stored"] is True
    assert payload["ingested"] == {"status": "ok", "node_id": "node-1", "chunk_ids": ["c1"]}
    assert pipeline.calls[0]["user_email"] == "me@local"


def test_an_assistant_turn_is_branding_normalized_like_the_python_route(seam_on):
    deps = _deps(pipeline=FakePipeline())
    response = _client(history_deps=deps).post(
        "/worker/chat/record-turn",
        json={"role": "assistant", "message": "I am ChatGPT"},
    )
    assert response.json()["item"]["content"] == "I am Lattice AI"


def test_an_ingest_receipt_of_any_shape_is_reported_as_it_came(seam_on):
    """Not every port answers with an ``IngestionResult``; none is assumed."""
    deps = _deps(pipeline=FakePipeline(result={"status": "queued"}))
    response = _client(history_deps=deps).post(
        "/worker/chat/record-turn", json={"role": "user", "message": "hi"}
    )
    assert response.json()["ingested"] == {"status": "queued"}


def test_a_worker_without_a_graph_stores_the_turn_and_says_nothing_was_ingested(seam_on):
    deps = _deps(pipeline=None, enable_graph=False)
    response = _client(history_deps=deps).post(
        "/worker/chat/record-turn", json={"role": "user", "message": "hi"}
    )
    payload = response.json()
    assert payload["stored"] is True
    assert payload["ingested"] is None


def test_a_failed_ingest_never_looks_like_a_lost_turn(seam_on):
    """The store write is the contract; graph growth is best-effort (writer §4)."""
    conversations = FakeConversations()
    deps = _deps(conversations=conversations, pipeline=FakePipeline(boom=True))
    response = _client(history_deps=deps).post(
        "/worker/chat/record-turn", json={"role": "user", "message": "hi"}
    )
    payload = response.json()
    assert payload["stored"] is True
    assert payload["ingested"] is None
    assert conversations.items[0]["content"] == "hi"


def test_a_store_that_refused_the_write_is_reported_as_not_stored(seam_on):
    """``write_chat_turn`` swallows by design; the seam must not claim success."""
    deps = _deps(conversations=FakeConversations(boom=True), pipeline=FakePipeline())
    response = _client(history_deps=deps).post(
        "/worker/chat/record-turn", json={"role": "user", "message": "hi"}
    )
    assert response.status_code == 200
    assert response.json() == {"stored": False, "item": None, "ingested": None}


@pytest.mark.parametrize("role", CHAT_ROLES)
def test_every_recordable_role_is_accepted(seam_on, role):
    response = _client(history_deps=_deps()).post(
        "/worker/chat/record-turn", json={"role": role, "message": "hi"}
    )
    assert response.status_code == 200
    assert response.json()["item"]["role"] == role


def test_an_unknown_role_is_refused_rather_than_filed_under_user(seam_on):
    conversations = FakeConversations()
    response = _client(history_deps=_deps(conversations=conversations)).post(
        "/worker/chat/record-turn",
        json={"role": "narrator", "message": "hi"},
        headers={LANGUAGE_HEADER: "en"},
    )
    assert response.status_code == 422
    assert "narrator" in response.json()["detail"]
    assert conversations.items == []


def test_a_worker_without_a_conversation_store_says_so_instead_of_500ing(seam_on):
    response = _client(history_deps=None).post(
        "/worker/chat/record-turn",
        json={"role": "user", "message": "hi"},
        headers={LANGUAGE_HEADER: "en"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == MESSAGES["worker_seam.history_unavailable"]["en"]


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

    assert probe_gpu_memory() == {
        "mlx_available": True,
        "gpu_mem_gb": 2.5,               # active + cache, unified memory
        "gpu_mem_pct": 15.6,             # 2.5 GB of 16 GB
        "total_bytes": 16 * 1024 ** 3,
        "detail": None,
    }


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
