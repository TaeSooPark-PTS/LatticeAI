"""wp31: the network-boundary router (the local_only / cloud_allowed dial).

``create_network_boundary_router`` was never built from its factory in a test:
half the module — every handler body, the scope helper, both 501 guards and the
egress record — never ran. The services are real (``NetworkBoundaryService`` and
``HybridPolicyService`` over ``tmp_path``); only the knowledge graph is a fake,
because the router asks it for exactly two things.

Token budgets are process-global and keyed by ``user|workspace``, so every test
here uses its own user email — that keeps the budget snapshots deterministic no
matter what else in the suite has touched the guard.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api import network_boundary as network_boundary_api
from latticeai.api.network_boundary import create_network_boundary_router
from latticeai.services.hybrid_policy import HybridPolicyService
from latticeai.services.network_boundary_service import NetworkBoundaryService


class FakeGraph:
    """The two calls the router makes into the store."""

    def __init__(self, *, ok: bool = True, reason: str = "") -> None:
        self.ok = ok
        self.reason = reason
        self.calls: List[Dict[str, Any]] = []
        self.matches: List[Dict[str, Any]] = [
            {
                "node_id": "node-decision",
                "title": "Ship the hybrid retriever",
                "summary": "Decision recorded during the 10.2 review.",
                "type": "Decision",
                "score": 0.9,
            },
            {
                "node_id": "node-note",
                "title": "Scratch note",
                "summary": "Unstructured note.",
                "type": "Note",
                "score": 0.3,
            },
        ]

    def set_node_sensitivity(self, node_id, *, local_only=True, reason=None):
        self.calls.append(
            {"node_id": node_id, "local_only": local_only, "reason": reason}
        )
        if not self.ok:
            return {"ok": False, "reason": self.reason}
        return {"ok": True, "node_id": node_id, "local_only": local_only}

    def hybrid_search(
        self, query, *, top_k=20, allowed_workspaces=None, include_legacy_global=False
    ):
        self.calls.append({"query": query, "top_k": top_k, "allowed": allowed_workspaces})
        return {"mode": "hybrid", "matches": list(self.matches)}


def build(
    tmp_path,
    *,
    user: str,
    graph: Any = None,
    with_policy: bool = True,
):
    service = NetworkBoundaryService(data_dir=tmp_path)
    policy = HybridPolicyService(data_dir=tmp_path) if with_policy else None
    app = FastAPI()
    app.include_router(
        create_network_boundary_router(
            service=service,
            require_user=lambda request: user,
            knowledge_graph=graph,
            policy_service=policy,
        )
    )
    return TestClient(app), service, policy


def test_get_boundary_includes_budget_and_resolved_policy(tmp_path):
    client, _service, _policy = build(tmp_path, user="get@wp31.test")

    body = client.get("/api/network-boundary").json()

    assert body["mode"] == "local_only"
    assert body["allows_cloud"] is False
    assert body["scope"] == {"user_email": "get@wp31.test", "workspace_id": None}
    assert body["token_budget"]["session_used"] == 0
    assert body["policy"]["min_extraction_confidence"] == 0.55


def test_get_boundary_omits_policy_when_no_policy_service(tmp_path):
    client, _service, _policy = build(
        tmp_path, user="nopolicy@wp31.test", with_policy=False
    )

    body = client.get("/api/network-boundary").json()

    assert "policy" not in body
    assert body["mode"] == "local_only"


def test_scope_helper_prefers_the_query_param_over_the_header(tmp_path):
    client, service, _policy = build(tmp_path, user="scope@wp31.test")
    service.set_mode(
        "cloud_allowed",
        user_email="scope@wp31.test",
        workspace_id="ws-header",
        acknowledge_risk=True,
    )

    header_only = client.get(
        "/api/network-boundary", headers={"X-Workspace-Id": " ws-header "}
    ).json()
    param_wins = client.get(
        "/api/network-boundary",
        params={"workspace_id": "ws-param"},
        headers={"X-Workspace-Id": "ws-header"},
    ).json()

    assert header_only["scope"]["workspace_id"] == "ws-header"
    assert header_only["mode"] == "cloud_allowed"
    assert param_wins["scope"]["workspace_id"] == "ws-param"
    assert param_wins["mode"] == "local_only"


def test_catalog_lists_both_modes(tmp_path):
    client, _service, _policy = build(tmp_path, user="catalog@wp31.test")

    modes = client.get("/api/network-boundary/catalog").json()["modes"]

    assert [mode["id"] for mode in modes] == ["local_only", "cloud_allowed"]
    assert [mode["requires_ack"] for mode in modes] == [False, True]


def test_ui_state_is_a_compact_panel_payload(tmp_path):
    client, _service, _policy = build(tmp_path, user="ui@wp31.test")

    body = client.get(
        "/api/network-boundary/ui-state", params={"workspace_id": "ws-ui"}
    ).json()

    assert body["mode"] == "local_only"
    assert body["label"] == "Local only"
    assert body["label_ko"] == "로컬만"
    assert body["allows_cloud"] is False
    assert body["requires_ack"] is False
    assert body["policy"]["auto_commit"] is False
    assert body["token_budget"]["max_tokens_per_turn"] > 0
    assert [mode["id"] for mode in body["catalog"]] == ["local_only", "cloud_allowed"]


def test_ui_state_without_policy_service_returns_an_empty_policy(tmp_path):
    client, _service, _policy = build(
        tmp_path, user="uinopolicy@wp31.test", with_policy=False
    )

    body = client.get("/api/network-boundary/ui-state").json()

    assert body["policy"] == {}


def test_setting_cloud_mode_requires_an_acknowledged_risk(tmp_path):
    client, service, _policy = build(tmp_path, user="set@wp31.test")

    refused = client.post("/api/network-boundary", json={"mode": "cloud_allowed"})
    accepted = client.post(
        "/api/network-boundary",
        json={"mode": "cloud_allowed", "acknowledge_risk": True, "workspace_id": "ws-set"},
    )

    assert refused.status_code == 400
    assert "acknowledge_risk" in refused.json()["detail"]
    assert accepted.status_code == 200
    assert accepted.json()["mode"] == "cloud_allowed"
    assert accepted.json()["allows_cloud"] is True
    assert (
        service.resolve(user_email="set@wp31.test", workspace_id="ws-set").value
        == "cloud_allowed"
    )


def test_preview_reports_exactly_what_would_leave_the_machine(tmp_path):
    graph = FakeGraph()
    client, _service, _policy = build(
        tmp_path, user="preview@wp31.test", graph=graph
    )

    body = client.post(
        "/api/network-boundary/preview",
        json={"message": "what did we decide about the retriever", "top_k": 40},
    ).json()

    assert body["mode"] == "local_only"
    assert body["allows_cloud"] is False
    assert body["node_ids"] == ["node-decision", "node-note"]
    assert body["titles"] == ["Ship the hybrid retriever", "Scratch note"]
    assert body["types"] == ["Decision", "Note"]
    assert "retriever" in body["keywords"]
    assert body["token_estimate"] > 0
    assert "Ship the hybrid retriever" in body["compact_preview"]
    assert body["would_block"] is None
    # top_k is clamped into [1, 12] before it reaches retrieval.
    assert graph.calls[0]["top_k"] == 24


def test_preview_reports_a_refusal_when_the_turn_exceeds_the_token_budget(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LATTICEAI_CLOUD_MAX_TOKENS_PER_TURN", "1")
    graph = FakeGraph()
    client, _service, _policy = build(
        tmp_path, user="budget@wp31.test", graph=graph
    )

    body = client.post(
        "/api/network-boundary/preview", json={"message": "retriever decision"}
    ).json()

    assert body["token_budget"]["max_tokens_per_turn"] == 1
    assert "exceed" in body["would_block"]


def test_node_sensitivity_marks_a_memory_and_records_the_egress_event(
    tmp_path, monkeypatch
):
    graph = FakeGraph()
    recorded: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        network_boundary_api,
        "record_cloud_egress",
        lambda **event: recorded.append(event) or event,
    )
    client, _service, _policy = build(
        tmp_path, user="sens@wp31.test", graph=graph
    )

    marked = client.post(
        "/api/network-boundary/node-sensitivity",
        json={
            "node_id": "node-decision",
            "local_only": True,
            "reason": "contains a client name",
            "workspace_id": "ws-sens",
        },
    )
    cleared = client.post(
        "/api/network-boundary/node-sensitivity",
        json={"node_id": "node-decision", "local_only": False},
    )

    assert marked.status_code == 200
    assert marked.json() == {
        "ok": True,
        "node_id": "node-decision",
        "local_only": True,
    }
    assert cleared.json()["local_only"] is False
    assert [event["outcome"] for event in recorded] == [
        "marked_local_only",
        "cleared_local_only",
    ]
    assert recorded[0]["workspace_id"] == "ws-sens"
    assert recorded[0]["detail"] == "contains a client name"
    assert recorded[0]["token_estimate"] == 0


def test_node_sensitivity_404s_when_the_store_refuses_the_node(tmp_path):
    graph = FakeGraph(ok=False, reason="no such node")
    client, _service, _policy = build(tmp_path, user="miss@wp31.test", graph=graph)

    response = client.post(
        "/api/network-boundary/node-sensitivity", json={"node_id": "ghost"}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "no such node"


def test_node_sensitivity_falls_back_to_a_localized_not_found_message(tmp_path):
    graph = FakeGraph(ok=False, reason="")
    client, _service, _policy = build(tmp_path, user="miss2@wp31.test", graph=graph)

    response = client.post(
        "/api/network-boundary/node-sensitivity",
        json={"node_id": "ghost"},
        headers={"Accept-Language": "en"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "That node was not found."


@pytest.mark.parametrize("graph", [None, object()])
def test_node_sensitivity_501s_without_a_capable_store(tmp_path, graph):
    client, _service, _policy = build(
        tmp_path, user="nostore@wp31.test", graph=graph
    )

    response = client.post(
        "/api/network-boundary/node-sensitivity",
        json={"node_id": "node-decision"},
        headers={"Accept-Language": "en"},
    )

    assert response.status_code == 501
    assert response.json()["detail"] == "The Knowledge Graph is not available."


def test_policy_round_trip_patches_only_the_supplied_fields(tmp_path):
    client, _service, policy = build(tmp_path, user="policy@wp31.test")

    before = client.get("/api/network-boundary/policy").json()
    updated = client.post(
        "/api/network-boundary/policy",
        json={
            "blocked_node_types": ["Secret"],
            "auto_commit": True,
            "workspace_id": "ws-policy",
        },
    ).json()
    after = client.get(
        "/api/network-boundary/policy", params={"workspace_id": "ws-policy"}
    ).json()

    assert before["auto_commit"] is False
    assert "Secret" in updated["blocked_node_types"]
    # The hard circuit breakers are always unioned in, never replaced.
    assert "ApiKey" in updated["blocked_node_types"]
    assert updated["auto_commit"] is True
    # Untouched fields keep their defaults rather than being reset to null.
    assert updated["min_extraction_confidence"] == before["min_extraction_confidence"]
    assert updated["allow_multimodal"] == before["allow_multimodal"]
    assert after["auto_commit"] is True
    resolved = policy.resolve(user_email="policy@wp31.test", workspace_id="ws-policy")
    assert resolved["auto_commit"] is True
    # A different workspace is unaffected by the patch.
    assert (
        policy.resolve(user_email="policy@wp31.test", workspace_id="other")["auto_commit"]
        is False
    )


def test_policy_routes_501_when_the_policy_service_is_absent(tmp_path):
    client, _service, _policy = build(
        tmp_path, user="nopolicy2@wp31.test", with_policy=False
    )
    headers = {"Accept-Language": "en"}

    read = client.get("/api/network-boundary/policy", headers=headers)
    write = client.post(
        "/api/network-boundary/policy", json={"auto_commit": True}, headers=headers
    )

    assert read.status_code == 501
    assert write.status_code == 501
    assert read.json()["detail"] == "The hybrid policy service is not configured."
