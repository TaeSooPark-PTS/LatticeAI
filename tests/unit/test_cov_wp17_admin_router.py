"""wp17 — admin router surfaces driven through their factory.

Covers the workspace-scoping helpers, the audit filter predicates, retention
timestamp parsing, health-summary degradation paths, and every mutating admin
endpoint (users, VPC, SSO) including the localized refusals. Collaborators are
injected fakes that record their side effects, so each assertion is on a status
code, a payload, or recorded state.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.admin import create_admin_router

ADMIN = "admin@example.com"
MEMBER = "member@example.com"

_UNSET = object()


def _default_users() -> Dict[str, Dict[str, Any]]:
    return {
        ADMIN: {"role": "admin", "name": "Admin", "disabled": False},
        MEMBER: {"role": "user", "name": "Member", "disabled": False},
    }


def _public_user(email: str, user: Dict[str, Any], _users: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "email": email,
        "role": user.get("role", "user"),
        "disabled": bool(user.get("disabled")),
    }


def _sso_projection(source: Dict[str, Any]) -> Dict[str, Any]:
    """The shape server_app hands the router: never echoes the secret."""
    return {
        "enabled": bool(source.get("enabled")),
        "provider_name": source.get("provider_name", ""),
        "client_secret_set": bool(source.get("client_secret")),
    }


def _admin_client(
    *,
    users: Optional[Dict[str, Any]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
    sensitivity: Optional[Callable[[List[Dict[str, Any]]], Any]] = None,
    graph_stats: Optional[Callable[[], Any]] = None,
    hardening: Any = _UNSET,
    enable_graph: bool = True,
    invite_gate_enabled: bool = False,
    invite_code: str = "INVITE-123",
    default_port: int = 4825,
    vpc: Optional[Dict[str, Any]] = None,
    sso: Optional[Dict[str, Any]] = None,
):
    """Build the admin router over fakes. Returns ``(client, state)``."""

    live_users = _default_users() if users is None else users
    state: Dict[str, Any] = {
        "users": live_users,
        "saved_users": [],
        "saved_vpc": [],
        "saved_sso": [],
        "audit_events": [],
        "vpc": dict(vpc) if vpc is not None else {"provider": "aws", "private_subnets": ["10.0.0.0/24"]},
        "sso": dict(sso) if sso is not None else {"enabled": False, "provider_name": ""},
    }

    def _save_users(updated: Dict[str, Any]) -> None:
        state["saved_users"].append({email: dict(user) for email, user in updated.items()})

    def _save_vpc(config: Dict[str, Any]) -> None:
        state["vpc"] = dict(config)
        state["saved_vpc"].append(dict(config))

    def _save_sso(update: Dict[str, Any]) -> Dict[str, Any]:
        state["sso"].update(update)
        state["saved_sso"].append(dict(update))
        return dict(state["sso"])

    def _append_audit(event_type: str, **fields: Any) -> None:
        state["audit_events"].append((event_type, fields))

    def _public_sso(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return _sso_projection(config if config is not None else state["sso"])

    def _sensitivity(scoped_history: List[Dict[str, Any]]) -> Any:
        if sensitivity is not None:
            return sensitivity(scoped_history)
        return {"summary": {"severity_counts": {"high": 0}}, "scanned": len(scoped_history)}

    def _graph_stats() -> Any:
        if graph_stats is not None:
            return graph_stats()
        return {"nodes": 3}

    kwargs: Dict[str, Any] = dict(
        require_admin=lambda _request: (ADMIN, live_users),
        require_user=lambda _request: ADMIN,
        load_users=lambda: live_users,
        save_users=_save_users,
        get_user_role=lambda email, all_users=None: (all_users or live_users).get(email, {}).get("role", "user"),
        get_history=lambda: list(history or []),
        get_audit_log=lambda: list(audit_log or []),
        public_user=_public_user,
        load_vpc_config=lambda: dict(state["vpc"]),
        save_vpc_config=_save_vpc,
        build_admin_audit_report=lambda _users, events: {"recent_events": list(events)},
        build_sensitivity_report=_sensitivity,
        append_audit_event=_append_audit,
        public_sso_config=_public_sso,
        save_sso_config=_save_sso,
        get_graph_stats=_graph_stats,
        enable_graph=enable_graph,
        invite_code=invite_code,
        invite_gate_enabled=invite_gate_enabled,
        default_port=default_port,
    )
    if hardening is not _UNSET:
        kwargs["product_hardening_status"] = hardening

    app = FastAPI()
    app.include_router(create_admin_router(**kwargs))
    return TestClient(app), state


# ── scoping helpers ────────────────────────────────────────────────────────

def test_unscoped_request_keeps_every_history_row():
    """No workspace selector at all → `_matches_scope` short-circuits to True."""
    history = [
        {"role": "user", "timestamp": "2026-06-01T00:00:00", "workspace_id": "org-a"},
        {"role": "assistant", "timestamp": "2026-06-01T00:01:00"},
    ]
    client, _ = _admin_client(history=history)

    body = client.get("/admin/summary").json()

    assert body["total_messages"] == 2
    assert body["user_messages"] == 1
    assert body["assistant_messages"] == 1
    assert body["last_message_at"] == "2026-06-01T00:01:00"


# ── audit filters ──────────────────────────────────────────────────────────

_AUDIT_EVENTS = [
    {
        "event_type": "chat_message",
        "user_email": "bob@example.com",
        "severity": "warning",
        "timestamp": "2026-06-01T00:00:00",
    },
]


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ({"actor": "alice"}, "actor filter"),
        ({"action": "file_delete"}, "action filter"),
        ({"severity": "critical"}, "severity filter"),
    ],
)
def test_audit_filters_reject_non_matching_events(query, reason):
    client, _ = _admin_client(audit_log=list(_AUDIT_EVENTS))

    body = client.get("/admin/audit", params=query).json()

    assert body["recent_events"] == [], reason
    assert body["filters"]["matched_events"] == 0
    assert body["filters"]["scoped_events"] == 1


def test_audit_filters_keep_matching_events_and_report_graph_failure():
    def _boom():
        raise RuntimeError("graph offline")

    client, _ = _admin_client(audit_log=list(_AUDIT_EVENTS), graph_stats=_boom, enable_graph=True)

    body = client.get(
        "/admin/audit",
        params={"actor": "bob", "action": "chat", "severity": "warning", "q": "bob"},
    ).json()

    assert body["filters"]["matched_events"] == 1
    assert body["recent_events"][0]["user_email"] == "bob@example.com"
    assert body["graph"] == {"error": "graph offline"}


# ── retention timestamp parsing ────────────────────────────────────────────

def test_log_retention_parses_missing_aware_and_malformed_timestamps():
    audit_log = [
        {"event_type": "no_timestamp"},
        {"event_type": "aware", "timestamp": "2020-01-01T00:00:00+00:00"},
        {"event_type": "malformed", "timestamp": "not-a-timestamp"},
    ]
    client, _ = _admin_client(audit_log=audit_log)

    body = client.get("/admin/log-retention").json()

    assert body["total_events"] == 3
    # Only the tz-aware 2020 event is older than the 90-day window; the
    # unparseable and absent timestamps must be retained, never pruned.
    assert body["prune_candidates"] == 1
    assert body["retained_events"] == 2
    assert body["retention_days"] == 90
    assert body["editable"] is False


# ── health summary degradation ─────────────────────────────────────────────

def test_health_summary_reports_high_risk_events():
    client, _ = _admin_client(
        history=[{"role": "user", "timestamp": "2026-06-01T00:00:00"}],
        sensitivity=lambda _history: {"summary": {"severity_counts": {"high": 2}}},
    )

    body = client.get("/admin/health-summary").json()

    assert body["status"] == "attention"
    assert {"area": "security", "severity": "high", "message": "2 high-risk event(s)"} in body["issues"]


def test_health_summary_survives_a_failing_sensitivity_report():
    def _boom(_history):
        raise RuntimeError("sensitivity down")

    client, _ = _admin_client(sensitivity=_boom)

    body = client.get("/admin/health-summary").json()

    assert body["status"] == "ok"
    assert body["issue_count"] == 0


def test_health_summary_flags_graph_error_payload():
    client, _ = _admin_client(graph_stats=lambda: {"error": "kg missing"}, enable_graph=True)

    body = client.get("/admin/health-summary").json()

    assert {"area": "brain_ops", "severity": "warning", "message": "Knowledge graph unavailable"} in body["issues"]
    assert body["issue_count"] == 1


def test_health_summary_flags_graph_exception():
    def _boom():
        raise RuntimeError("kg exploded")

    client, _ = _admin_client(graph_stats=_boom, enable_graph=True)

    issues = client.get("/admin/health-summary").json()["issues"]

    assert issues == [{"area": "brain_ops", "severity": "warning", "message": "kg exploded"}]


def test_health_summary_survives_a_failing_hardening_provider():
    def _boom():
        raise RuntimeError("hardening down")

    client, _ = _admin_client(hardening=_boom)

    body = client.get("/admin/health-summary").json()

    assert body["status"] == "ok"
    assert body["issues"] == []


# ── read-only admin surfaces ───────────────────────────────────────────────

def test_admin_stats_buckets_by_day_and_ignores_other_roles():
    history = [
        {"role": "user", "timestamp": "2026-06-01T09:00:00"},
        {"role": "assistant", "timestamp": "2026-06-01T09:00:01"},
        {"role": "user", "timestamp": "2026-06-02T09:00:00"},
        {"role": "system", "timestamp": "2026-06-02T09:00:01"},
        {"role": "user"},
    ]
    client, _ = _admin_client(history=history)

    daily = client.get("/admin/stats").json()["daily"]
    by_day = {row["date"]: row for row in daily}

    assert by_day["2026-06-01"] == {"date": "2026-06-01", "user": 1, "assistant": 1}
    assert by_day["2026-06-02"] == {"date": "2026-06-02", "user": 1, "assistant": 0}
    assert by_day["unknown"] == {"date": "unknown", "user": 1, "assistant": 0}


def test_admin_users_lists_public_projections():
    client, _ = _admin_client()

    body = client.get("/admin/users").json()

    assert body == [
        {"email": ADMIN, "role": "admin", "disabled": False},
        {"email": MEMBER, "role": "user", "disabled": False},
    ]


def test_admin_sensitivity_returns_the_scoped_report():
    history = [
        {"role": "user", "timestamp": "2026-06-01T00:00:00", "workspace_id": "org-a"},
        {"role": "user", "timestamp": "2026-06-01T00:00:01", "workspace_id": "org-b"},
    ]
    client, _ = _admin_client(history=history)

    body = client.get("/admin/sensitivity", headers={"X-Workspace-Id": "org-a"}).json()

    assert body["scanned"] == 1


@pytest.mark.parametrize("gate", [True, False])
def test_admin_policies_reflect_the_invite_gate(gate):
    client, _ = _admin_client(invite_gate_enabled=gate)

    policies = {row["id"]: row for row in client.get("/admin/policies").json()["policies"]}

    assert policies["invite_gate"]["enforced"] is gate
    assert policies["invite_gate"]["value"] == ("Signed access gate" if gate else "Disabled")
    assert policies["local_file_access"]["enforced"] is True


def test_product_hardening_reports_missing_provider():
    client, _ = _admin_client()

    body = client.get("/admin/product-hardening").json()

    assert body["available"] is False
    assert "not configured" in body["reason"]


def test_product_hardening_returns_the_provider_payload():
    client, _ = _admin_client(hardening=lambda: {"startup": {"network_exposed": False}, "available": True})

    assert client.get("/admin/product-hardening").json() == {
        "startup": {"network_exposed": False},
        "available": True,
    }


def test_vpc_status_returns_the_stored_config():
    client, _ = _admin_client(vpc={"provider": "gcp", "region": "asia-northeast3"})

    assert client.get("/vpc/status").json() == {"provider": "gcp", "region": "asia-northeast3"}


def test_admin_updates_vpc_and_trims_blank_subnets():
    client, state = _admin_client(vpc={"provider": "aws", "private_subnets": ["old"]})

    body = client.patch(
        "/admin/vpc",
        json={"region": "us-west-2", "private_subnets": [" 10.0.1.0/24 ", "  ", "10.0.2.0/24"]},
    ).json()

    assert body["region"] == "us-west-2"
    assert body["private_subnets"] == ["10.0.1.0/24", "10.0.2.0/24"]
    assert state["saved_vpc"][-1]["private_subnets"] == ["10.0.1.0/24", "10.0.2.0/24"]
    assert state["vpc"]["provider"] == "aws", "unset fields must survive a partial patch"


# ── user mutation ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "사용자를 찾을 수 없습니다."), ("en", "No such user.")],
)
def test_update_unknown_user_is_localized_404(language, expected):
    client, _ = _admin_client()

    response = client.patch(
        "/admin/users/ghost@example.com",
        json={"role": "user"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "role은 admin 또는 user만 가능합니다."), ("en", "Role must be either 'admin' or 'user'.")],
)
def test_update_user_rejects_unknown_role(language, expected):
    client, state = _admin_client()

    response = client.patch(
        f"/admin/users/{MEMBER}",
        json={"role": "superuser"},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected
    assert state["saved_users"] == []


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "자기 자신은 비활성화할 수 없습니다."), ("en", "You cannot disable your own account.")],
)
def test_admin_cannot_disable_itself(language, expected):
    client, state = _admin_client()

    response = client.patch(
        f"/admin/users/{ADMIN}",
        json={"disabled": True},
        headers={"X-Lattice-Language": language},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected
    assert state["users"][ADMIN]["disabled"] is False


def test_update_user_promotes_disables_and_audits():
    client, state = _admin_client()

    body = client.patch(f"/admin/users/{MEMBER}", json={"role": "admin", "disabled": True}).json()

    assert body == {"email": MEMBER, "role": "admin", "disabled": True}
    assert state["users"][MEMBER]["role"] == "admin"
    assert state["saved_users"], "the change must be persisted"
    event_type, fields = state["audit_events"][-1]
    assert event_type == "user_update"
    assert fields["target_email"] == MEMBER
    assert fields["before"]["role"] == "user"
    assert fields["after"]["role"] == "admin"


@pytest.mark.parametrize(
    ("language", "expected"),
    [("ko", "자기 자신은 삭제할 수 없습니다."), ("en", "You cannot delete your own account.")],
)
def test_admin_cannot_delete_itself(language, expected):
    client, state = _admin_client()

    response = client.delete(f"/admin/users/{ADMIN}", headers={"X-Lattice-Language": language})

    assert response.status_code == 400
    assert response.json()["detail"] == expected
    assert ADMIN in state["users"]


def test_delete_unknown_user_is_404():
    client, _ = _admin_client()

    response = client.delete("/admin/users/ghost@example.com")

    assert response.status_code == 404
    assert response.json()["detail"] == "사용자를 찾을 수 없습니다."


def test_delete_user_removes_persists_and_audits():
    client, state = _admin_client()

    body = client.delete(f"/admin/users/{MEMBER}").json()

    assert body == {"status": "ok", "deleted": {"email": MEMBER, "role": "user", "disabled": False}}
    assert MEMBER not in state["users"]
    assert MEMBER not in state["saved_users"][-1]
    event_type, fields = state["audit_events"][-1]
    assert event_type == "user_delete"
    assert fields["deleted_user"]["email"] == MEMBER


# ── invite link / SSO / enterprise ─────────────────────────────────────────

def test_invite_link_carries_the_code_only_while_the_gate_is_on():
    gated, _ = _admin_client(invite_gate_enabled=True, invite_code="CODE-9")
    open_client, _ = _admin_client(invite_gate_enabled=False, invite_code="CODE-9")

    gated_body = gated.get("/admin/invite-link", headers={"host": "lattice.example"}).json()
    open_body = open_client.get("/admin/invite-link", headers={"host": "lattice.example"}).json()

    assert gated_body == {
        "invite_url": "http://lattice.example/?code=CODE-9",
        "invite_code": "CODE-9",
        "gate_enabled": True,
    }
    assert open_body["invite_url"] == "http://lattice.example/"
    assert open_body["gate_enabled"] is False


def test_invite_link_honours_forwarded_https():
    client, _ = _admin_client(invite_gate_enabled=True, invite_code="CODE-9")

    body = client.get(
        "/admin/invite-link",
        headers={"host": "lattice.example", "x-forwarded-proto": "https"},
    ).json()

    assert body["invite_url"] == "https://lattice.example/?code=CODE-9"


def test_admin_sso_get_returns_the_public_projection():
    client, _ = _admin_client(sso={"enabled": True, "provider_name": "Okta", "client_secret": "s3cret"})

    body = client.get("/admin/sso").json()

    assert body == {"enabled": True, "provider_name": "Okta", "client_secret_set": True}


def test_admin_sso_patch_saves_audits_and_never_echoes_the_secret():
    client, state = _admin_client(sso={"enabled": False, "provider_name": ""})

    body = client.patch(
        "/admin/sso",
        json={"enabled": True, "provider_name": "Okta", "discovery_url": "https://okta.example/.well-known", "client_secret": "s3cret"},
    ).json()

    assert body == {"enabled": True, "provider_name": "Okta", "client_secret_set": True}
    assert "client_secret" not in body
    assert state["saved_sso"][-1]["provider_name"] == "Okta"
    event_type, fields = state["audit_events"][-1]
    assert event_type == "sso_config_update"
    assert fields["provider_name"] == "Okta"
    assert fields["discovery_url"] == "https://okta.example/.well-known"
    assert fields["enabled"] is True


def test_enterprise_overview_and_siem_export_are_community_stubs():
    client, _ = _admin_client()

    overview = client.get("/admin/enterprise").json()
    siem = client.get("/admin/enterprise/siem-export").json()

    assert set(overview) >= {"edition", "admin_policies", "audit_export", "siem_export", "organization_settings"}
    assert siem["streamed"] is False
    assert "preview_envelope" in siem
