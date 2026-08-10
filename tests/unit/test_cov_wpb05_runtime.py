"""wpb05 — runtime wiring: the branch each seam takes on its *second* visit.

The wiring layer is mostly idempotence and rebinding logic, and that logic only
shows its second branch when something is called twice, called with an argument
omitted, or handed an object that is not a FastAPI app. Every process-wide
singleton these tests touch (``_SHARED``, ``_POLICY``, ``_shared_runtime``, the
egress sink, the dispatch permission resolver) is replaced through
``monkeypatch`` so the module globals are restored when the test ends.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

from latticeai import app_factory
from latticeai.core.config import Config
from latticeai.runtime import network_boundary_wiring as nbw
from latticeai.runtime import permission_mode_wiring as pmw
from latticeai.runtime.access_runtime import build_access_runtime
from latticeai.runtime.build_phases import (
    phase_brain,
    phase_config,
    phase_domain,
    phase_identity,
)
from latticeai.runtime.router_registration import register_interaction_routers
from latticeai.runtime.runtime_context import RuntimeContext
from latticeai.runtime.sso_config_runtime import build_sso_config_runtime
from latticeai.services import cloud_egress_audit
from latticeai.services.tool_dispatch import DEFAULT_TOOL_DISPATCH_SERVICE


class _Request:
    def __init__(self, *, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


class _AppWithoutState:
    """A router host that is not a FastAPI app — no ``state`` to flag."""

    def __init__(self) -> None:
        self.routers: List[Any] = []

    def include_router(self, router: Any) -> None:
        self.routers.append(router)


# ── access_runtime ───────────────────────────────────────────────────────────


def test_a_session_id_that_matches_no_account_resolves_to_nobody():
    """The id scan walks every account and still finds nothing."""
    users = {
        "owner@example.com": {"id": "user:1"},
        "second@example.com": {"id": "user:2"},
        "legacy-row": "not-a-dict",
    }
    sessions = {"stale": "user:404", "live": "user:2"}
    runtime = build_access_runtime(
        config=SimpleNamespace(
            admin_emails=["owner@example.com"], is_public=False, network_exposed=False,
        ),
        require_auth=True,
        http_exception=HTTPException,
        request_type=_Request,
        load_users=lambda: users,
        get_session_email=lambda token: sessions.get(token),
        user_id_for_email=lambda _users, email: None,
    )
    get_current_user = runtime["get_current_user"]

    assert get_current_user(_Request(cookies={"session_token": "stale"})) is None
    assert (
        get_current_user(_Request(cookies={"session_token": "live"}))
        == "second@example.com"
    )


# ── sso_config_runtime ───────────────────────────────────────────────────────


def _sso_runtime(sso_file: Path) -> Dict[str, Any]:
    return build_sso_config_runtime(
        sso_file=sso_file,
        discovery_url="",
        client_id="",
        client_secret="",
        redirect_uri="http://127.0.0.1:4825/auth/sso/callback",
        provider_name="Corp SSO",
        logging=logging,
    )


def test_an_sso_file_holding_a_json_array_is_ignored_in_favour_of_the_env(tmp_path: Path):
    sso_file = tmp_path / "sso_config.json"
    sso_file.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    config = _sso_runtime(sso_file)["load_sso_config"]()

    assert config["provider_name"] == "Corp SSO"
    assert config["enabled"] is False
    assert config["redirect_uri"] == "http://127.0.0.1:4825/auth/sso/callback"


# ── network_boundary_wiring ──────────────────────────────────────────────────


def test_the_hybrid_policy_singleton_rebinds_only_what_the_caller_supplied(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(nbw, "_POLICY", None)
    events: List[tuple] = []

    first = nbw.get_hybrid_policy_service(data_dir=tmp_path / "real")
    second = nbw.get_hybrid_policy_service(audit=lambda *a, **k: events.append((a, k)))

    assert second is first, "the policy service is a process-wide singleton"
    assert second._path == tmp_path / "real" / "hybrid_policy.json", (
        "an omitted data_dir must not reset the bound path"
    )
    assert second._audit is not None


def test_the_boundary_router_mounts_on_a_host_that_has_no_app_state(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(nbw, "_SHARED", None)
    monkeypatch.setattr(nbw, "_POLICY", None)
    monkeypatch.setattr(cloud_egress_audit, "_AUDIT", cloud_egress_audit._AUDIT)
    app = _AppWithoutState()

    service = nbw.register_network_boundary_router(
        app, require_user=lambda _request: "wpb05@example.com", data_dir=tmp_path,
    )

    assert len(app.routers) == 1
    assert service is nbw.get_network_boundary_service()
    assert not hasattr(app, "state"), "the flag has nowhere to live and is skipped"


# ── permission_mode_wiring ───────────────────────────────────────────────────


def test_the_permission_mode_router_mounts_on_a_host_that_has_no_app_state(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(pmw, "_SHARED", None)
    monkeypatch.setattr(
        DEFAULT_TOOL_DISPATCH_SERVICE,
        "permission_mode",
        DEFAULT_TOOL_DISPATCH_SERVICE.permission_mode,
    )
    app = _AppWithoutState()

    service = pmw.register_permission_mode_router(
        app, require_user=lambda _request: "wpb05@example.com", data_dir=tmp_path,
    )

    assert len(app.routers) == 1
    assert service is pmw.get_permission_mode_service()
    assert DEFAULT_TOOL_DISPATCH_SERVICE.permission_mode is pmw.resolve_active_permission_mode


# ── router_registration ──────────────────────────────────────────────────────


def test_interaction_routers_can_still_be_registered_from_loose_keyword_arguments():
    """The pre-AppContext call shape must keep working — no context, all kwargs."""
    app = _AppWithoutState()
    seen: Dict[str, Any] = {}

    def _factory(name: str):
        def _build(*args: Any, **kwargs: Any) -> str:
            seen[name] = {"args": args, "kwargs": kwargs}
            return name

        return _build

    platform = SimpleNamespace(gate_read=object(), gate_write=object())
    routers = register_interaction_routers(
        app,
        create_chat_router=_factory("chat"),
        context="chat-context",
        create_search_router=_factory("search"),
        search_service="search-service",
        require_user="require-user",
        create_tools_router=_factory("tools"),
        data_dir="/tmp/wpb05",
        static_dir="/tmp/wpb05-static",
        create_hooks_router=_factory("hooks"),
        create_agent_registry_router=_factory("registry"),
        create_memory_router=_factory("memory"),
        platform=platform,
    )

    assert routers == ("chat", "search", "tools", "hooks", "registry", "memory")
    assert app.routers == list(routers)
    assert seen["chat"]["args"] == ("chat-context",)
    assert seen["tools"]["kwargs"]["tool_context"] is None
    assert seen["memory"]["kwargs"]["gate_read"] is platform.gate_read


# ── app_factory ──────────────────────────────────────────────────────────────


class _RacingLock:
    """Another thread finishes the build while this caller waits for the lock."""

    def __init__(self, winner: Any) -> None:
        self._winner = winner
        self.entered = 0

    def __enter__(self) -> "_RacingLock":
        self.entered += 1
        app_factory._shared_runtime = self._winner
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False


def test_the_shared_runtime_is_built_once_even_when_two_callers_race(monkeypatch):
    winner = object()
    lock = _RacingLock(winner)
    monkeypatch.setattr(app_factory, "_shared_runtime", None)
    monkeypatch.setattr(app_factory, "_runtime_lock", lock)
    monkeypatch.setattr(
        app_factory,
        "build_runtime",
        lambda config=None: pytest.fail("the losing caller must not build a second runtime"),
    )

    assert app_factory.get_shared_runtime() is winner
    assert lock.entered == 1


# ── build_phases ─────────────────────────────────────────────────────────────


class _RecordingConversations:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def append(self, item: Dict[str, Any]) -> None:
        self.rows.append(item)


def _config(tmp_path: Path, **overrides: str) -> Config:
    env = {
        "LATTICEAI_DATA_DIR": str(tmp_path / "data"),
        "LATTICEAI_STATIC_DIR": str(tmp_path / "static"),
        "LATTICEAI_ENABLE_GRAPH": "true",
        "LATTICEAI_ENABLE_TELEGRAM": "false",
        "LATTICEAI_REQUIRE_AUTH": "false",
        "LATTICEAI_MODE": "local",
    }
    env.update(overrides)
    return Config.from_env(env)


def _brain_ctx(monkeypatch, tmp_path: Path) -> RuntimeContext:
    """Run phase_brain with every heavy constructor replaced by a stand-in."""
    monkeypatch.setattr(
        "latticeai.core.embedding_providers.resolve_embedding_profile",
        lambda name: {"provider": "ollama", "model": "bge-m3", "dimensions": 1024},
    )
    monkeypatch.setattr(
        "latticeai.core.embedding_providers.resolve_embedder",
        lambda provider, **kwargs: SimpleNamespace(
            dim=384,
            fell_back=False,
            as_dict=lambda: {"requested_provider": provider, "fell_back": False},
        ),
    )
    monkeypatch.setattr("lattice_brain.graph.schema.set_embed_dim", lambda dim: None)
    monkeypatch.setattr(
        "lattice_brain.storage.storage_from_env",
        lambda env, data_dir=None: SimpleNamespace(name="storage"),
    )
    monkeypatch.setattr(
        "latticeai.runtime.brain_runtime.build_brain_runtime",
        lambda **kwargs: {
            "KNOWLEDGE_GRAPH": SimpleNamespace(stats=lambda: {"total_nodes": 3}),
            "CONVERSATIONS": _RecordingConversations(),
        },
    )
    monkeypatch.setattr(
        "latticeai.runtime.hooks_runtime.build_hooks_runtime",
        lambda **kwargs: {"HOOKS_REGISTRY": object(), "LOCAL_KG_WATCHER": None},
    )
    monkeypatch.setattr(
        "latticeai.runtime.persistence_runtime.build_persistence_runtime",
        lambda **kwargs: {
            name: object()
            for name in (
                "REALTIME_BUS", "WORKSPACE_OS", "WORKSPACE_SERVICE", "INVITATION_STORE",
                "PLUGIN_REGISTRY", "TEMPLATE_CATALOG", "AGENT_REGISTRY", "MEMORY_SERVICE",
                "BRAIN_INTELLIGENCE", "AUTOMATION_INTELLIGENCE", "INGESTION_PIPELINE",
                "DEVICE_IDENTITY", "KG_PORTABILITY", "FUNNEL_METRICS",
            )
        },
    )
    monkeypatch.setattr(
        "latticeai.runtime.history_runtime.build_history_query_runtime",
        lambda **kwargs: {
            name: (lambda *a, **k: None)
            for name in (
                "_history_allowed_workspaces_for", "_history_include_legacy_global",
                "get_history", "conversation_title", "group_history_conversations",
                "get_conversation_messages", "clear_history", "clear_conversation",
            )
        },
    )
    ctx = RuntimeContext(_config(tmp_path, LATTICEAI_EMBEDDING_PROFILE="bge-m3"))
    phase_config(ctx)
    phase_identity(ctx)
    phase_brain(ctx)
    return ctx


def test_the_admin_audit_report_asks_no_graph_when_the_graph_is_switched_off(
    monkeypatch, tmp_path: Path
):
    ctx = _brain_ctx(monkeypatch, tmp_path)
    ctx.set(ENABLE_GRAPH=False)
    ctx.set(
        KNOWLEDGE_GRAPH=SimpleNamespace(
            stats=lambda: pytest.fail("a disabled graph must never be queried")
        )
    )

    report = ctx.build_admin_audit_report(
        {"wpb05@example.com": {"id": "user:1"}},
        audit_events=[
            {"event_type": "chat_message", "role": "user", "user_email": "wpb05@example.com"}
        ],
    )

    assert "graph_nodes" not in report["summary"]
    assert any(row["email"] == "wpb05@example.com" for row in report["per_user"])


class _Gardener:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def import_vault(self) -> Dict[str, Any]:
        raise AssertionError("the vault must not be imported while the graph is off")


def test_the_domain_phase_skips_the_vault_import_when_the_graph_is_off(monkeypatch, caplog):
    registered: List[Any] = []
    monkeypatch.setattr("latticeai.models.router.LLMRouter", lambda: SimpleNamespace())
    monkeypatch.setattr("lattice_brain.graph.runtime.set_llm_router", registered.append)
    monkeypatch.setattr(
        "latticeai.services.tool_dispatch.configure_tool_dispatch", lambda **kwargs: None
    )
    monkeypatch.setattr("latticeai.services.p_reinforce.PReinforceGardener", _Gardener)
    monkeypatch.setattr(
        "latticeai.services.chat_service.ChatService",
        lambda **kwargs: SimpleNamespace(kwargs=kwargs),
    )

    ctx = RuntimeContext()
    ctx.set(
        ENABLE_GRAPH=False,
        INGESTION_PIPELINE=object(),
        KNOWLEDGE_GRAPH=None,
        WORKSPACE_OS=object(),
        load_users=dict,
        get_user_role=lambda email, users=None: "user",
        get_history=lambda *a, **k: [],
        save_to_history=lambda *a, **k: None,
        get_history_user=lambda *a, **k: {},
    )

    with caplog.at_level(logging.WARNING):
        phase_domain(ctx)

    assert "garden vault import" not in caplog.text
    assert ctx.gardener.kwargs["ingestion_pipeline"] is None
    assert registered == [ctx.model_router]
