"""wp32 coverage — the small ``latticeai.api`` router factories.

Every router here is built through its own factory with injected fakes and
driven over ``TestClient``, so the real FastAPI wiring (path params, request
models, dependency gates, error translation) executes. The assertions are on
observable outcomes: status codes, payloads, and what the injected collaborator
was actually asked to do.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from latticeai.api.agent_registry import create_agent_registry_router
from latticeai.api.brain_intelligence import create_brain_intelligence_router
from latticeai.api.change_proposals import create_change_proposals_router
from latticeai.api.command_center import create_command_center_router
from latticeai.api.garden import create_garden_router
from latticeai.api.network import create_network_router
from latticeai.api.permission_mode import create_permission_mode_router
from latticeai.api.project_sessions import create_project_sessions_router
from latticeai.api.voice_capture import create_voice_capture_router
from latticeai.core.agent_registry import AgentRegistry


def _audit_recorder():
    events: list = []

    def append_audit_event(event, **kwargs):
        events.append((event, kwargs))

    return events, append_audit_event


# ── network ──────────────────────────────────────────────────────────────────


class _Network:
    """A peer registry whose failure modes are selected per instance."""

    def __init__(self, *, fail=None):
        self.fail = fail
        self.calls: list = []

    def list_peers(self):
        return [{"id": "peer-1", "name": "laptop"}]

    def add_peer(self, *, name, base_url, public_key):
        self.calls.append(("add", name))
        if self.fail == "value":
            raise ValueError("base_url must be https")
        return {"id": "peer-2", "name": name, "base_url": base_url, "key": public_key}

    def remove_peer(self, peer_id):
        if self.fail == "missing":
            raise FileNotFoundError(peer_id)
        return {"status": "unpaired", "id": peer_id}

    def push_to_peer(self, peer_id, *, workspace_id=None):
        if self.fail == "missing":
            raise FileNotFoundError(peer_id)
        if self.fail == "boom":
            raise RuntimeError("transport exploded")
        return {"pushed": 3, "peer": peer_id, "workspace_id": workspace_id}

    def receive(self, headers, body):
        if self.fail == "forbidden":
            raise PermissionError("unknown device signature")
        if self.fail == "value":
            raise ValueError("payload is not a knowledge bundle")
        return {"accepted": len(body), "signature": headers.get("x-lattice-signature")}


class _Identity:
    def describe(self):
        return {"id": "device-local", "name": "this machine"}


def _network_client(network: _Network) -> TestClient:
    app = FastAPI()
    app.include_router(create_network_router(
        network=network,
        identity=_Identity(),
        require_user=lambda _request: "user@example.com",
        require_admin=lambda _request: ("admin@example.com", {}),
    ))
    return TestClient(app)


def test_network_admin_can_list_pair_unpair_and_push():
    network = _Network()
    client = _network_client(network)

    assert client.get("/network/peers").json() == {"peers": [{"id": "peer-1", "name": "laptop"}]}

    paired = client.post(
        "/network/peers",
        json={"name": "desktop", "base_url": "https://peer", "public_key": "pk"},
    )
    assert paired.status_code == 200
    assert paired.json()["status"] == "paired"
    assert paired.json()["peer"]["name"] == "desktop"

    assert client.delete("/network/peers/peer-2").json() == {"status": "unpaired", "id": "peer-2"}

    pushed = client.post("/network/push/peer-2", json={"workspace_id": "org-1"})
    assert pushed.json() == {"pushed": 3, "peer": "peer-2", "workspace_id": "org-1"}


def test_network_pair_rejects_an_invalid_peer_with_400():
    response = _network_client(_Network(fail="value")).post(
        "/network/peers",
        json={"name": "bad", "base_url": "http://peer", "public_key": "pk"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "base_url must be https"


def test_network_unknown_peer_is_404_on_unpair_and_push():
    client = _network_client(_Network(fail="missing"))

    assert client.delete("/network/peers/ghost").status_code == 404
    push = client.post("/network/push/ghost", json={})
    assert push.status_code == 404
    assert "Unknown peer" in push.json()["detail"]


def test_network_push_transport_failure_is_502():
    response = _network_client(_Network(fail="boom")).post("/network/push/peer-2", json={})

    assert response.status_code == 502
    assert "Push failed" in response.json()["detail"]


def test_network_receive_authenticates_the_peer_not_the_session():
    response = _network_client(_Network()).post(
        "/network/receive", content=b"bundle", headers={"X-Lattice-Signature": "sig"},
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": 6, "signature": "sig"}


def test_network_receive_maps_peer_errors_to_403_and_400():
    forbidden = _network_client(_Network(fail="forbidden")).post("/network/receive", content=b"x")
    invalid = _network_client(_Network(fail="value")).post("/network/receive", content=b"x")

    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "unknown device signature"
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "payload is not a knowledge bundle"


# ── permission mode ──────────────────────────────────────────────────────────


class _PermissionModeService:
    def __init__(self, *, refuse=False):
        self.refuse = refuse
        self.calls: list = []

    def get(self, *, user_email, workspace_id):
        self.calls.append(("get", user_email, workspace_id))
        return {"mode": "strict", "workspace_id": workspace_id, "user": user_email}

    def set_mode(self, mode, *, user_email, workspace_id, acknowledge_risk, source):
        self.calls.append(("set", mode.value, workspace_id, acknowledge_risk, source))
        if self.refuse:
            raise PermissionError("bypass requires an explicit risk acknowledgement")
        return {"mode": mode.value, "workspace_id": workspace_id, "user": user_email}


def _permission_client(service) -> TestClient:
    app = FastAPI()
    app.include_router(create_permission_mode_router(
        service=service,
        require_user=lambda _request: "u@example.com",
    ))
    return TestClient(app)


def test_permission_mode_get_prefers_query_scope_over_header():
    service = _PermissionModeService()
    client = _permission_client(service)

    response = client.get(
        "/api/permission-mode?workspace_id=org-query",
        headers={"X-Workspace-Id": " org-header "},
    )

    assert response.status_code == 200
    assert response.json()["workspace_id"] == "org-query"
    assert service.calls == [("get", "u@example.com", "org-query")]


def test_permission_mode_get_falls_back_to_the_trimmed_header_scope():
    service = _PermissionModeService()

    response = _permission_client(service).get(
        "/api/permission-mode", headers={"X-Workspace-Id": "  org-header  "},
    )

    assert response.json()["workspace_id"] == "org-header"
    assert service.calls == [("get", "u@example.com", "org-header")]


def test_permission_mode_catalog_lists_every_dial_position():
    response = _permission_client(_PermissionModeService()).get("/api/permission-mode/catalog")

    assert response.status_code == 200
    assert {mode["id"] for mode in response.json()["modes"]} == {"strict", "trusted", "bypass"}


def test_permission_mode_set_normalizes_an_alias_and_records_the_source():
    service = _PermissionModeService()

    response = _permission_client(service).post(
        "/api/permission-mode",
        json={"mode": "acceptEdits", "acknowledge_risk": True},
        headers={"X-Workspace-Id": "org-1"},
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "trusted"
    assert service.calls == [("set", "trusted", "org-1", True, "api")]


def test_permission_mode_set_translates_a_refusal_into_400():
    response = _permission_client(_PermissionModeService(refuse=True)).post(
        "/api/permission-mode", json={"mode": "bypass"},
    )

    assert response.status_code == 400
    assert "risk acknowledgement" in response.json()["detail"]


# ── command center ───────────────────────────────────────────────────────────


class _CommandCenterService:
    def __init__(self):
        self.calls: list = []

    def briefing(self, *, user_email, workspace_id):
        self.calls.append(("briefing", user_email, workspace_id))
        return {"greeting": "good morning", "workspace_id": workspace_id}

    def search(self, query, *, user_email, workspace_id, limit):
        self.calls.append(("search", query, user_email, workspace_id, limit))
        return {"query": query, "limit": limit, "results": []}


def test_command_center_briefing_and_search_are_user_and_workspace_scoped():
    service = _CommandCenterService()
    app = FastAPI()
    app.include_router(create_command_center_router(
        service=service,
        require_user=lambda _request: "u@example.com",
        gate_read=lambda _request: "org-1",
    ))
    client = TestClient(app)

    briefing = client.get("/api/command/briefing")
    search = client.get("/api/command/search?q=roadmap&limit=3")

    assert briefing.json() == {"greeting": "good morning", "workspace_id": "org-1"}
    assert search.json() == {"query": "roadmap", "limit": 3, "results": []}
    assert service.calls == [
        ("briefing", "u@example.com", "org-1"),
        ("search", "roadmap", "u@example.com", "org-1", 3),
    ]


def test_command_center_search_rejects_an_out_of_range_limit():
    app = FastAPI()
    app.include_router(create_command_center_router(
        service=_CommandCenterService(),
        require_user=lambda _request: "u@example.com",
        gate_read=lambda _request: None,
    ))

    assert TestClient(app).get("/api/command/search?q=x&limit=99").status_code == 422


# ── garden ───────────────────────────────────────────────────────────────────


class _Gardener:
    def __init__(self):
        self.processed: list = []

    async def process(self, raw_data, category):
        self.processed.append((raw_data, category))
        return {"stored": True, "category": category or "auto"}

    def get_tree(self):
        return {"tree": [{"path": "10_Areas", "children": []}]}


def test_garden_routes_classify_raw_data_and_return_the_tree():
    gardener = _Gardener()
    app = FastAPI()
    app.include_router(create_garden_router(
        gardener=gardener, require_user=lambda _request: "u@example.com",
    ))
    client = TestClient(app)

    stored = client.post("/garden", json={"raw_data": "note body", "category": "areas"})
    tree = client.get("/garden/tree")

    assert stored.json() == {"stored": True, "category": "areas"}
    assert gardener.processed == [("note body", "areas")]
    assert tree.json()["tree"][0]["path"] == "10_Areas"


# ── brain intelligence ───────────────────────────────────────────────────────


class _BrainService:
    def __init__(self):
        self.calls: list = []

    def _record(self, name, user_email, workspace_id):
        self.calls.append((name, user_email, workspace_id))
        return {"report": name, "workspace_id": workspace_id}

    def health_report(self, *, user_email, workspace_id):
        return self._record("health", user_email, workspace_id)

    def insights(self, *, user_email, workspace_id):
        return self._record("insights", user_email, workspace_id)

    def contradictions(self, *, user_email, workspace_id):
        return self._record("contradictions", user_email, workspace_id)


def test_brain_read_endpoints_use_the_read_gate_never_the_write_gate():
    service = _BrainService()
    app = FastAPI()
    app.include_router(create_brain_intelligence_router(
        service=service,
        require_user=lambda _request: "u@example.com",
        gate_read=lambda _request: "org-read",
        gate_write=lambda _request: "org-write",
        append_audit_event=lambda *_a, **_k: None,
    ))
    client = TestClient(app)

    payloads = [
        client.get("/api/brain/health").json(),
        client.get("/api/brain/insights").json(),
        client.get("/api/brain/contradictions").json(),
    ]

    assert [p["report"] for p in payloads] == ["health", "insights", "contradictions"]
    assert {p["workspace_id"] for p in payloads} == {"org-read"}
    assert service.calls == [
        ("health", "u@example.com", "org-read"),
        ("insights", "u@example.com", "org-read"),
        ("contradictions", "u@example.com", "org-read"),
    ]


# ── agent registry ───────────────────────────────────────────────────────────


def _registry_client(tmp_path):
    registry = AgentRegistry(tmp_path / "agents.json")
    events, append_audit_event = _audit_recorder()
    app = FastAPI()
    app.include_router(create_agent_registry_router(
        registry=registry,
        require_user=lambda _request: "u@example.com",
        require_admin=lambda _request: ("admin@example.com", {}),
        append_audit_event=append_audit_event,
    ))
    return TestClient(app), registry, events


def test_agent_registry_capabilities_and_discovery_read_the_real_index(tmp_path):
    client, registry, _events = _registry_client(tmp_path)
    registry.register(name="Scout", capabilities=["research", "summarize"])

    capabilities = client.get("/agents/api/registry/capabilities").json()["capabilities"]
    discovered = client.get("/agents/api/registry/discover?capability=research").json()

    assert "agent:custom:scout" in capabilities["research"]
    assert discovered["capability"] == "research"
    assert [a["id"] for a in discovered["agents"]] == ["agent:custom:scout"]


def test_agent_registry_register_rejects_an_unknown_type_with_400(tmp_path):
    client, _registry, events = _registry_client(tmp_path)

    response = client.post(
        "/agents/api/registry", json={"name": "Scout", "type": "not-a-type"},
    )

    assert response.status_code == 400
    assert "type must be one of" in response.json()["detail"]
    assert events == []


def test_agent_registry_get_returns_the_agent_or_404(tmp_path):
    client, registry, _events = _registry_client(tmp_path)
    registry.register(name="Scout", description="finds things")

    found = client.get("/agents/api/registry/agent:custom:scout")
    missing = client.get("/agents/api/registry/agent:custom:ghost")

    assert found.status_code == 200
    assert found.json()["agent"]["description"] == "finds things"
    assert missing.status_code == 404
    assert "Agent not found" in missing.json()["detail"]


def test_agent_registry_patch_updates_config_and_audits(tmp_path):
    client, registry, events = _registry_client(tmp_path)
    registry.register(name="Scout")

    updated = client.patch(
        "/agents/api/registry/agent:custom:scout",
        json={"config": {"depth": 3}, "enabled": False},
    )
    missing = client.patch("/agents/api/registry/agent:custom:ghost", json={"config": {}})

    assert updated.status_code == 200
    assert updated.json()["agent"]["config"] == {"depth": 3}
    assert updated.json()["agent"]["enabled"] is False
    assert missing.status_code == 404
    assert [event for event, _ in events] == ["agent_config"]


def test_agent_registry_delete_removes_custom_and_refuses_builtins(tmp_path):
    client, registry, events = _registry_client(tmp_path)
    registry.register(name="Scout")

    removed = client.delete("/agents/api/registry/agent:custom:scout")
    missing = client.delete("/agents/api/registry/agent:custom:scout")
    builtin = client.delete("/agents/api/registry/agent:planner")

    assert removed.json() == {"removed": "agent:custom:scout"}
    assert missing.status_code == 404
    assert builtin.status_code == 400
    assert "cannot be removed" in builtin.json()["detail"]
    assert [event for event, _ in events] == ["agent_remove"]


# ── voice capture ────────────────────────────────────────────────────────────


class _VoiceService:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.captured: list = []

    def status(self):
        return {"capture": True, "transcription": False}

    def capture(self, path, **kwargs):
        self.captured.append((path, kwargs))
        if self.fail:
            raise RuntimeError("transcriber unavailable")
        return {"status": "stored", "transcription": "skipped"}


def _voice_client(service, *, append_audit_event=None) -> TestClient:
    app = FastAPI()
    app.include_router(create_voice_capture_router(
        service=service,
        require_user=lambda _request: "u@example.com",
        gate_write=lambda _request: "org-1",
        append_audit_event=append_audit_event,
    ))
    return TestClient(app)


def test_voice_capture_failure_becomes_a_clean_500(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    service = _VoiceService(fail=True)

    response = _voice_client(service).post(
        "/api/capture/voice",
        files={"file": ("memo.m4a", b"audio-bytes", "audio/mp4")},
        data={"title": "standup"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "transcriber unavailable"
    assert service.captured and service.captured[0][1]["title"] == "standup"
    # the failure path still removed the staged temp file
    assert list(tmp_path.glob("ltcai-voice-*")) == []


def test_voice_capture_survives_an_unremovable_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    leaked: list = []

    def refuse_unlink(self, missing_ok=False):
        leaked.append(str(self))
        raise OSError("temp file is locked")

    monkeypatch.setattr(Path, "unlink", refuse_unlink)

    response = _voice_client(_VoiceService()).post(
        "/api/capture/voice", files={"file": ("memo", b"audio", "audio/mp4")},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "stored", "transcription": "skipped"}
    assert leaked and leaked[0].endswith(".m4a")


def test_voice_capture_result_survives_a_broken_audit_sink(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def broken_audit(*_args, **_kwargs):
        raise RuntimeError("audit log is read-only")

    response = _voice_client(
        _VoiceService(), append_audit_event=broken_audit,
    ).post("/api/capture/voice", files={"file": ("memo.m4a", b"audio", "audio/mp4")})

    assert response.status_code == 200
    assert response.json()["status"] == "stored"


# ── change proposals ─────────────────────────────────────────────────────────


class _ProposalService:
    def __init__(self, *, approve_error=None):
        self.approve_error = approve_error

    def pending(self, *, user_email, workspace_id):
        return {"count": 1, "user": user_email, "workspace_id": workspace_id, "items": []}

    def approve_and_apply(self, item_id, *, user_email, workspace_id):
        if self.approve_error is not None:
            raise self.approve_error
        return {"applied": True, "item": {"id": item_id}}


def _proposals_client(service) -> TestClient:
    app = FastAPI()
    app.include_router(create_change_proposals_router(
        service=service,
        require_user=lambda _request: "u@example.com",
        gate_read=lambda _request: "org-read",
        gate_write=lambda _request: "org-write",
    ))
    return TestClient(app)


def test_change_proposals_list_is_scoped_by_the_read_gate():
    response = _proposals_client(_ProposalService()).get("/api/proposals")

    assert response.status_code == 200
    assert response.json() == {
        "count": 1, "user": "u@example.com", "workspace_id": "org-read", "items": [],
    }


def test_change_proposal_approve_reports_an_unapplyable_proposal_as_400():
    response = _proposals_client(
        _ProposalService(approve_error=ValueError("proposal already resolved")),
    ).post("/api/proposals/rp-1/approve")

    assert response.status_code == 400
    assert response.json()["detail"] == "proposal already resolved"


# ── project sessions ─────────────────────────────────────────────────────────


class _ProjectStore:
    def __init__(self):
        self.records = {"proj-1": {"id": "proj-1", "title": "Launch", "todos": []}}

    def get(self, session_id, *, user_email, workspace_id):
        record = self.records.get(session_id)
        if record is None:
            return None
        return {**record, "user_email": user_email, "workspace_id": workspace_id}


def test_project_session_get_returns_the_record_scoped_to_the_reader():
    app = FastAPI()
    app.include_router(create_project_sessions_router(
        store=_ProjectStore(),
        require_user=lambda _request: "u@example.com",
        gate_read=lambda _request: "org-read",
        gate_write=lambda _request: "org-write",
    ))
    client = TestClient(app)

    found = client.get("/api/projects/proj-1")
    missing = client.get("/api/projects/proj-9")

    assert found.status_code == 200
    assert found.json() == {
        "id": "proj-1", "title": "Launch", "todos": [],
        "user_email": "u@example.com", "workspace_id": "org-read",
    }
    assert missing.status_code == 404


# ── shared guard: the factories keep their auth dependency ───────────────────


def test_every_router_factory_still_refuses_an_unauthenticated_caller():
    def deny(_request: Request):
        raise HTTPException(status_code=401, detail="auth required")

    app = FastAPI()
    app.include_router(create_command_center_router(
        service=_CommandCenterService(), require_user=deny, gate_read=lambda _r: None,
    ))
    app.include_router(create_garden_router(gardener=_Gardener(), require_user=deny))

    client = TestClient(app)
    assert client.get("/api/command/briefing").status_code == 401
    assert client.get("/garden/tree").status_code == 401
