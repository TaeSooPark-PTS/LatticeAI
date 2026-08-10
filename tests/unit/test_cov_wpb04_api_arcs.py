"""wpb04 — never-taken branch directions in the HTTP routers.

Every router is built through its factory over injected fakes and driven with
``TestClient``; the assertions are on status codes, payloads and recorded
state. Nothing here opens a socket or reads the developer's machine.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api import browser as browser_module
from latticeai.api import setup as api_setup
from latticeai.api import static_routes as static_routes_module
from latticeai.api.admin import create_admin_router
from latticeai.api.auth import create_auth_router
from latticeai.api.browser import BrowserFetchError, _default_fetch_url
from latticeai.api.chat_stream import stream_chat
from latticeai.api.knowledge_graph import create_knowledge_graph_router
from latticeai.api.realtime import create_realtime_router
from latticeai.api.setup import create_setup_router

USER = "user@example.com"
ADMIN = "admin@example.com"


# ── admin ────────────────────────────────────────────────────────────────────


def _admin_client(*, enable_graph: bool = True, users: Optional[Dict[str, Any]] = None,
                  vpc: Optional[Dict[str, Any]] = None):
    live_users = users if users is not None else {
        ADMIN: {"role": "admin", "name": "Admin", "disabled": False},
        USER: {"role": "user", "name": "Member", "disabled": False},
    }
    state: Dict[str, Any] = {
        "users": live_users,
        "saved_users": [],
        "vpc": dict(vpc or {"provider": "none", "region": "", "private_subnets": ["a"]}),
        "audit": [],
        "graph_stats_calls": 0,
    }

    def _graph_stats():
        state["graph_stats_calls"] += 1
        return {"nodes": 1}

    router = create_admin_router(
        require_admin=lambda request: (ADMIN, state["users"]),
        require_user=lambda request: ADMIN,
        load_users=lambda: state["users"],
        save_users=lambda users_: state["saved_users"].append(dict(users_)),
        get_user_role=lambda email, users_=None: (state["users"].get(email) or {}).get("role", "user"),
        get_history=lambda: [],
        get_audit_log=lambda: [],
        public_user=lambda email, user, users_: {"email": email, **user},
        load_vpc_config=lambda: dict(state["vpc"]),
        save_vpc_config=lambda config: state.update(vpc=dict(config)),
        build_admin_audit_report=lambda **kwargs: {},
        build_sensitivity_report=lambda history: {"summary": {"severity_counts": {}}},
        append_audit_event=lambda event, **kwargs: state["audit"].append((event, kwargs)),
        public_sso_config=lambda **kwargs: {},
        save_sso_config=lambda config: None,
        get_graph_stats=_graph_stats,
        enable_graph=enable_graph,
        invite_code="INVITE",
        invite_gate_enabled=False,
        default_port=4825,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), state


def test_health_summary_skips_the_brain_probe_when_the_graph_is_off():
    """admin.py:194→210 — no graph, so no brain_ops issue and no stats read."""
    client, state = _admin_client(enable_graph=False)

    body = client.get("/admin/health-summary").json()

    assert state["graph_stats_calls"] == 0
    assert [issue["area"] for issue in body["issues"]] == []
    assert body["status"] == "ok"


def test_patching_the_vpc_without_subnets_leaves_the_stored_list_alone():
    """admin.py:387→389 — the subnet-normalising branch is skipped."""
    client, state = _admin_client(vpc={"provider": "none", "private_subnets": ["10.0.0.0/24"]})

    body = client.patch("/admin/vpc", json={"region": "ap-northeast-2"}).json()

    assert body["region"] == "ap-northeast-2"
    assert body["private_subnets"] == ["10.0.0.0/24"], "an unset field is not rewritten"
    assert state["vpc"]["region"] == "ap-northeast-2"


def test_patching_only_a_users_role_does_not_touch_the_disabled_flag():
    """admin.py:404→408 — ``disabled`` was not part of the request."""
    client, state = _admin_client()

    body = client.patch("/admin/users/" + USER, json={"role": "admin"}).json()

    assert body["role"] == "admin"
    assert state["users"][USER]["disabled"] is False
    assert state["saved_users"], "the change was persisted"
    assert state["audit"][0][0] == "user_update"


# ── knowledge_graph ──────────────────────────────────────────────────────────


class _KgStore:
    def __init__(self, *, stats: Any = None) -> None:
        self.stats_payload = stats if stats is not None else {}
        self.ingested: List[Dict[str, Any]] = []
        self.index_status = None  # a store that predates the vector index

    def list_documents(self, limit: int = 200):
        raise RuntimeError("documents table unavailable")

    def stats(self, **_kwargs):
        return self.stats_payload

    def filter_scoped_nodes(self, items, allowed, *, include_legacy_global=False):
        return list(items)

    def ingest_message(self, role, content, **kwargs):
        record = {"role": role, "content": content, **kwargs}
        self.ingested.append(record)
        return {"status": "ok", "node_id": "n1", "role": role}


def _kg_client(store: _KgStore, tmp_path):
    router = create_knowledge_graph_router(
        get_graph=lambda: store,
        require_graph=lambda: None,
        require_user=lambda request: USER,
        static_dir=tmp_path,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_pipeline_status_ignores_a_v2_block_that_is_not_a_mapping(tmp_path):
    """knowledge_graph.py:311→315 — ``edges`` is unusable and there is no v2
    dict to fall back on, so ``connected`` is omitted rather than faked."""
    store = _KgStore(stats={"edges": "many", "v2": "not-a-dict", "nodes": 4})

    body = _kg_client(store, tmp_path).get("/knowledge-graph/pipeline/status").json()

    assert "connected" not in body or body["connected"] is None
    assert body["received"] == 4, "the node count still answers the first stage"


def test_pipeline_status_ignores_a_v2_edge_count_that_is_not_a_number(tmp_path):
    """knowledge_graph.py:313→315 — the v2 block exists but its edge count is
    not a number."""
    store = _KgStore(stats={"edges": None, "v2": {"edges": "lots"}, "nodes": {"Concept": 2, "chunk": 9}})

    body = _kg_client(store, tmp_path).get("/knowledge-graph/pipeline/status").json()

    assert "connected" not in body or body["connected"] is None
    assert body["received"] == 2, "chunk nodes never count as received documents"


def test_ingesting_as_yourself_is_allowed(tmp_path):
    """knowledge_graph.py:479→481 — the declared author matches the caller."""
    store = _KgStore()
    client = _kg_client(store, tmp_path)

    response = client.post("/knowledge-graph/ingest", json={
        "type": "message", "content": "회의 메모", "user_email": USER.upper(),
    })

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert store.ingested[0]["role"] == "user"


def test_ingesting_on_someone_elses_behalf_is_refused(tmp_path):
    store = _KgStore()

    response = _kg_client(store, tmp_path).post("/knowledge-graph/ingest", json={
        "type": "message", "content": "회의 메모", "user_email": "someone@example.com",
    })

    assert response.status_code == 403
    assert store.ingested == []


# ── auth ─────────────────────────────────────────────────────────────────────


def _auth_client(users: Dict[str, Any]):
    app = FastAPI()
    app.include_router(create_auth_router(
        load_users=lambda: users,
        save_users=lambda _users: None,
        hash_password=lambda value: "hashed:" + value,
        verify_and_migrate=lambda *_args: True,
        create_session=lambda email: "session:" + email,
        get_session_email=lambda _token: None,
        invalidate_session=lambda _token: None,
        extract_bearer_token=lambda _request: None,
        get_user_role=lambda _email, _users=None: "user",
        require_user=lambda _request: USER,
        check_ip_rate_limit=lambda *_a, **_kw: None,
        client_ip=lambda _request: "127.0.0.1",
        get_sso_settings=lambda: {},
        get_sso_discovery=lambda _settings: None,
        public_sso_config=lambda **_kw: {},
        open_registration=True,
        session_ttl=3600,
        require_auth=True,
    ))
    return TestClient(app)


def test_updating_only_the_nickname_leaves_the_name_alone():
    """auth.py:337→339 — ``name`` was not part of the request."""
    users = {USER: {"name": "원래 이름", "nickname": "옛 별명"}}

    body = _auth_client(users).patch("/account/profile", json={"nickname": " 새 별명 "}).json()

    assert body == {"status": "ok", "name": "원래 이름", "nickname": "새 별명"}


def test_updating_only_the_name_leaves_the_nickname_alone():
    """auth.py:339→341 — ``nickname`` was not part of the request."""
    users = {USER: {"name": "원래 이름", "nickname": "옛 별명"}}

    body = _auth_client(users).patch("/account/profile", json={"name": " 새 이름 "}).json()

    assert body == {"status": "ok", "name": "새 이름", "nickname": "옛 별명"}


# ── browser ──────────────────────────────────────────────────────────────────


def test_whitespace_only_text_nodes_are_dropped_from_the_extraction():
    """browser.py:85→exit — ``handle_data`` with nothing but whitespace."""
    title, text = browser_module.extract_readable_text(
        "<html><head><title>제목</title></head><body><p>  \n\t  </p><p>본문</p></body></html>"
    )

    assert title == "제목"
    assert text == "본문"


def test_fetching_without_an_injected_transport_uses_the_default_client():
    """browser.py:272→275 — no transport override. The resolver refuses before
    any socket is opened, so this stays a local test."""

    def _resolver(*_args, **_kwargs):
        raise OSError("dns disabled in tests")

    with pytest.raises(BrowserFetchError, match="Could not resolve the page host"):
        _default_fetch_url("https://example.com/doc", resolver=_resolver)


# ── chat_stream ──────────────────────────────────────────────────────────────


class _Chunks:
    def __init__(self, chunks: List[str]) -> None:
        self.chunks = chunks

    async def stream_generate_as(self, model_id, message, context, max_tokens, temperature, image):
        for chunk in self.chunks:
            yield chunk


class _ChatService:
    def __init__(self) -> None:
        self.persisted: List[Dict[str, Any]] = []

    def build_graph_trace(self, *_args, **_kwargs):
        return None  # no graph → nothing to bind citations against

    async def persist_answer(self, **kwargs):
        self.persisted.append(kwargs)
        return {"id": "trace-1", "question": kwargs["question"]}


class _Req:
    message = "안녕"
    max_tokens = 64
    temperature = 0.2
    conversation_id = "c1"
    user_email = USER
    user_nickname = "You"
    source = "web"


def _sse(frames: List[str]) -> List[Dict[str, Any]]:
    return [
        json.loads(line[len("data: "):])
        for frame in frames
        for line in frame.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def test_a_stream_without_a_trace_or_quality_signal_still_finishes_cleanly():
    """chat_stream.py:178→183 (the trace is not a dict, so grounding is not
    attached to it) and 201→203 (no context-quality block on the trailer)."""
    chat_service = _ChatService()

    async def _drive() -> List[str]:
        return [frame async for frame in stream_chat(
            _Req(), context="", image_data=None,
            router=_Chunks(["안", "녕하세요"]),
            chat_service=chat_service,
            knowledge_graph=None,
            enable_graph=False,
            notify=None,
            model_id="local:test",
        )]

    events = _sse(asyncio.run(_drive()))

    assert "".join(e.get("chunk", "") for e in events) == "안녕하세요"
    trailer = events[-1]
    assert trailer["trace_id"] == "trace-1"
    assert "context_quality" not in trailer
    assert chat_service.persisted[0]["trace"] is None
    assert chat_service.persisted[0]["response"] == "안녕하세요"


# ── realtime router ──────────────────────────────────────────────────────────


class _Bus:
    def __init__(self) -> None:
        self.joined: List[Dict[str, Any]] = []
        self.left: List[str] = []

    def join(self, client_id, *, user=None, workspace_id=None):
        record = {"client_id": client_id, "user": user, "workspace_id": workspace_id}
        self.joined.append(record)
        return record

    def leave(self, client_id, *, user=None):
        self.left.append(client_id)

    def stats(self):
        return {"subscribers": 0}


def _realtime_client(bus: _Bus, scopes):
    app = FastAPI()
    app.include_router(create_realtime_router(
        bus=bus,
        require_user=lambda request: USER,
        get_current_user=lambda request: USER,
        allowed_scopes=lambda user: scopes,
    ))
    return TestClient(app)


def test_presence_join_without_scoping_keeps_the_requested_workspace():
    """realtime.py:94→101 — single-user / no-auth mode has no scope set."""
    bus = _Bus()

    body = _realtime_client(bus, None).post(
        "/realtime/presence/join", json={"client_id": "c-1", "workspace_id": "anything"}
    ).json()

    assert body["presence"]["workspace_id"] == "anything"
    assert bus.joined[0]["client_id"] == "c-1"


def test_presence_leave_without_a_client_id_is_a_no_op():
    """realtime.py:111→116 — nothing to leave."""
    bus = _Bus()

    response = _realtime_client(bus, None).post("/realtime/presence/leave", json={})

    assert response.json() == {"status": "ok"}
    assert bus.left == []


# ── setup ────────────────────────────────────────────────────────────────────


class _Json:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def to_json(self) -> Any:
        return self.payload


def test_scan_skips_the_plan_and_preset_rewrite_when_they_are_not_mappings(monkeypatch):
    """setup.py:114→135 and 135→138 — a probe pipeline whose plan/preset came
    back as lists must not be indexed into as dicts."""
    profile = _Json({"os": "linux"})
    recommendation = _Json({"runtime": "ollama", "rationale": ["RAM 32768 MB → 7B 급 모델"]})
    monkeypatch.setattr(api_setup, "auto_setup_probe", lambda: profile)
    monkeypatch.setattr(api_setup, "auto_setup_recommend", lambda _p: recommendation)
    monkeypatch.setattr(api_setup, "auto_setup_plan", lambda _p, _r: _Json(["not", "a", "dict"]))
    monkeypatch.setattr(api_setup, "auto_setup_verify", lambda _p, _r: {"ok": True})
    monkeypatch.setattr(api_setup, "auto_setup_preset", lambda _p, _r: ["also-not-a-dict"])
    monkeypatch.setattr(api_setup, "scan_environment", lambda: {"os": "linux"})
    monkeypatch.setattr(api_setup, "get_recommendations", lambda env: {
        "models": [{"model_id": "ollama:qwen3:4b", "checked": True}],
    })

    app = FastAPI()
    app.include_router(create_setup_router(
        model_router=None, require_user=lambda request: USER,
    ))
    body = TestClient(app).get("/setup/scan").json()

    zero_config = body["zero_config"]
    assert zero_config["recommend"]["model_id"] == "ollama:qwen3:4b"
    assert zero_config["recommend"]["runtime"] == "ollama"
    assert zero_config["plan"] == ["not", "a", "dict"], "the plan was left untouched"
    assert zero_config["preset"] == ["also-not-a-dict"], "the preset was left untouched"
    assert body["recommendations"]["install_plan"] == ["not", "a", "dict"]


# ── static_routes ────────────────────────────────────────────────────────────


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


UNPARSEABLE_TOP = (
    "Processes: 512 total\n"
    "CPU usage: unavailable while sampling\n"
)

DIGITLESS_VM_STAT = (
    "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
    "Pages free: unavailable.\n"
    "Pages active: unavailable.\n"
    "Pages inactive: unavailable.\n"
    "Pages wired down: unavailable.\n"
    "Pages occupied by compressor: unavailable.\n"
)


def test_the_host_probe_survives_output_it_cannot_parse(monkeypatch):
    """static_routes.py:64→61 (a ``CPU usage`` line the regex rejects) and
    73→70 (a page-count line with no number in it)."""

    outputs = {"top": UNPARSEABLE_TOP, "vm_stat": DIGITLESS_VM_STAT}

    def _run(cmd, **_kwargs):
        return _Completed(outputs[cmd[0]])

    monkeypatch.setattr(static_routes_module.subprocess, "run", _run)
    # ``None`` in sys.modules makes the Apple-Silicon import fail everywhere.
    monkeypatch.setitem(sys.modules, "mlx", None)
    monkeypatch.setitem(sys.modules, "mlx.core", None)

    result = static_routes_module._probe_host_capacity()

    assert result["cpu_pct"] == 0.0, "an unparseable sample stays at the honest zero"
    assert result["ram_pct"] == 0.0, "no page counts means no ratio"
    assert result["gpu_mem_pct"] == 0.0
    assert result["readiness"] == "roomy"
