"""wp36: the four late build phases, covered on purpose instead of by accident.

``phase_web``, ``phase_foundation_routes``, ``phase_platform_features`` and
``phase_interaction`` were already "green" before this file existed — but only
because ``tests/unit/test_security.py`` does ``from server import ...``, and
that import builds the entire shared runtime as a collection-time side effect.
Nothing asserted those phases; they were measured the way a light left on in
the next room lights this one. Narrow that import and ~104 statements drop out
of the report with no test failing to explain why.

So these tests run each late phase against a hand-built
:class:`~latticeai.runtime.runtime_context.RuntimeContext`, exactly as
``tests/unit/test_cov_wp19_build_phases.py`` does for the earlier phases: the
context is the whole interface a phase reads and writes, and a phase's real
product is the routers it mounts and the closures it publishes. What is faked
is deliberately small — the model router, the knowledge graph, the embedder and
the history readers, i.e. the things that would otherwise mean a GPU, a
network, or a model download. Everything else is the real object built against
``tmp_path``: the real config and identity phases, the real hooks registry, the
real workspace store, the real routers. Assertions call the published closures
and drive the mounted routes, because "the phase ran without raising" is not
evidence that the phase wired anything.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai import tools as latticeai_tools
from latticeai.core.audit import (
    build_admin_audit_report,
    build_sensitivity_report,
    classify_sensitive_message,
)
from latticeai.core.config import Config
from latticeai.core.security import bytes_match_extension
from latticeai.runtime.build_phases import (
    phase_config,
    phase_foundation_routes,
    phase_identity,
    phase_interaction,
    phase_platform_features,
    phase_services,
    phase_web,
)
from latticeai.runtime.hooks_runtime import build_hooks_runtime
from latticeai.runtime.persistence_runtime import build_persistence_runtime
from latticeai.runtime.runtime_context import RuntimeContext
from latticeai.services.triggers import TRIGGER_HOOK_NAME


# ── the few dependencies that must not be real ───────────────────────────────
class _FakeGraph:
    """A knowledge graph stand-in: the real one needs a store and embeddings."""

    def stats(self) -> Dict[str, int]:
        return {"total_nodes": 7, "total_edges": 3}

    def get_node(self, node_id: str) -> None:
        return None


class _FakeRouter:
    """The LLM router without MLX: only the surface the phases actually read."""

    current_model_id = "mlx-community/gemma"
    loaded_model_ids = ["mlx-community/gemma"]

    async def generate(self, message, context="", max_tokens=1024, temperature=0.1):
        return "answer"

    async def generate_as(self, *args, **kwargs):
        return "answer"

    async def load_model(self, *args, **kwargs):
        return "loaded"

    def unload_all(self) -> None:
        return None

    def unload_idle_models(self, seconds: int) -> List[str]:
        return []

    def model_memory_policy(self) -> Dict[str, Any]:
        return {"max_local_models": 1, "loaded_count": 1, "last_used": {}}

    def detected_cloud_models(self) -> List[Dict[str, str]]:
        return []


def _config(root: Path, **overrides: str) -> Config:
    """A Config built from an explicit mapping — never the real environment."""
    env = {
        "LATTICEAI_DATA_DIR": str(root / "data"),
        "LATTICEAI_STATIC_DIR": str(root / "static"),
        "LATTICEAI_ENABLE_GRAPH": "false",
        "LATTICEAI_ENABLE_TELEGRAM": "false",
        "LATTICEAI_REQUIRE_AUTH": "false",
        "LATTICEAI_AUTOLOAD_MODELS": "false",
        "LATTICEAI_MODE": "local",
    }
    env.update(overrides)
    return Config.from_env(env)


def _base_context(root: Path, monkeypatch, **overrides: str) -> RuntimeContext:
    """Everything the late phases read, built the way the real build builds it.

    Phases 2 and 3 run for real (they are cheap and own the paths the late
    phases write into). Phase 4's durable stores are constructed through their
    own seams against ``root`` so the workspace store, hooks registry and
    portability services are the real classes; only the graph, the embedder,
    the model router and the history readers are stand-ins.
    """
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LATTICEAI_PLUGINS_DIR", str(root / "plugins"))
    # The agent workspace is a process-global path resolved at import time;
    # without this the phase's ensure_agent_root() would write into the repo.
    monkeypatch.setattr(latticeai_tools, "AGENT_ROOT", root / "agent_workspace")

    ctx = RuntimeContext(_config(root, **overrides))
    phase_config(ctx)
    phase_identity(ctx)

    graph = _FakeGraph()
    hooks = build_hooks_runtime(
        data_dir=ctx.DATA_DIR,
        enable_graph=ctx.ENABLE_GRAPH,
        knowledge_graph_getter=lambda: graph,
    )
    persistence = build_persistence_runtime(
        data_dir=ctx.DATA_DIR,
        base_dir=ctx.BASE_DIR,
        enable_graph=ctx.ENABLE_GRAPH,
        knowledge_graph=graph,
        hooks_registry=hooks["HOOKS_REGISTRY"],
        history_file=ctx.HISTORY_FILE,
        conversations=SimpleNamespace(),
        user_id_for_email=ctx.user_id_for_email,
        audit=lambda *args, **kwargs: None,
    )
    ctx.set(**hooks)
    ctx.set(**{name: value for name, value in persistence.items() if name != "PLUGINS_DIR"})
    ctx.set(
        # phase 4 publishes these as thin delegations to latticeai.core.audit.
        classify_sensitive_message=classify_sensitive_message,
        build_sensitivity_report=build_sensitivity_report,
        build_admin_audit_report=build_admin_audit_report,
        _bytes_match_extension=bytes_match_extension,
        EMBEDDER=SimpleNamespace(as_dict=lambda: {"requested_provider": "hash"}),
        KNOWLEDGE_GRAPH=graph,
        CONVERSATIONS=SimpleNamespace(),
        save_to_history=lambda *args, **kwargs: None,
        get_history=lambda *args, **kwargs: [],
        conversation_title=lambda *args, **kwargs: "",
        group_history_conversations=lambda *args, **kwargs: [],
        get_conversation_messages=lambda *args, **kwargs: [],
        clear_history=lambda *args, **kwargs: None,
        clear_conversation=lambda *args, **kwargs: None,
        _history_allowed_workspaces_for=lambda *args, **kwargs: None,
        _history_include_legacy_global=lambda *args, **kwargs: False,
        _require_graph=lambda *args, **kwargs: None,
        _workspace_graph=lambda: graph if ctx.ENABLE_GRAPH else None,
        # phase 5 products
        model_router=_FakeRouter(),
        gardener=SimpleNamespace(),
        CHAT_SERVICE=SimpleNamespace(),
    )
    return ctx


@pytest.fixture
def late_context(tmp_path, monkeypatch):
    """Factory for pre-late-phase contexts, each with its own directory tree.

    The teardown stops any scheduler thread ``phase_platform_features``
    started, so a test never leaves a background loop running in the session.
    """
    built: List[RuntimeContext] = []

    def make(**overrides: str) -> RuntimeContext:
        ctx = _base_context(tmp_path / f"run{len(built)}", monkeypatch, **overrides)
        built.append(ctx)
        return ctx

    yield make

    for ctx in built:
        trigger_service = getattr(ctx, "TRIGGER_SERVICE", None)
        if trigger_service is not None:
            trigger_service.stop()


# ── running the phases in their contracted order ─────────────────────────────
def _through_web(ctx: RuntimeContext) -> RuntimeContext:
    phase_web(ctx)
    return ctx


def _through_services(ctx: RuntimeContext) -> RuntimeContext:
    phase_services(_through_web(ctx))
    return ctx


def _through_foundation_routes(ctx: RuntimeContext) -> RuntimeContext:
    phase_foundation_routes(_through_services(ctx))
    return ctx


def _through_platform_features(ctx: RuntimeContext) -> RuntimeContext:
    phase_platform_features(_through_foundation_routes(ctx))
    return ctx


def _through_interaction(ctx: RuntimeContext) -> RuntimeContext:
    phase_interaction(_through_platform_features(ctx))
    return ctx


def _flat_routes(routes: Any) -> list:
    # fastapi >= 0.140 wraps an included router in an opaque entry whose flat
    # ``path`` is None but which keeps the real APIRouter on ``original_router``
    # (this repo's routers bake their full paths, so no prefix re-joining is
    # needed). Older fastapi returns the APIRoutes directly; handle both.
    out = []
    for route in routes:
        out.append(route)
        original = getattr(route, "original_router", None)
        if original is not None:
            out.extend(_flat_routes(original.routes))
    return out


def _paths(ctx: RuntimeContext) -> set:
    return {getattr(route, "path", "") for route in _flat_routes(ctx.app.routes)}


def _router_paths(router: Any) -> set:
    return {getattr(route, "path", "") for route in _flat_routes(router.routes)}


def _has_route(ctx: RuntimeContext, path: str, method: str) -> bool:
    return any(
        getattr(route, "path", "") == path
        and method in (getattr(route, "methods", None) or set())
        for route in _flat_routes(ctx.app.routes)
    )


# ── phase 6: web shell, model runtime, foundation routers ────────────────────
def test_web_phase_builds_the_app_shell_and_the_model_runtime_service(late_context):
    ctx = _through_web(late_context())

    assert isinstance(ctx.app, FastAPI)
    assert ctx.app.title == "Lattice AI Server (local)"
    assert ctx.app.version == ctx.APP_VERSION
    assert "/static" in _paths(ctx)
    # This phase *builds* the foundation routers; mounting them is the next
    # phase's job, so their routes are on the routers and not yet on the app.
    assert {"/", "/status", "/local/sysinfo"} <= _router_paths(ctx.STATIC_ROUTES.router)
    assert {"/login", "/logout"} <= _router_paths(ctx.auth_router)
    assert {"/admin/policies"} <= _router_paths(ctx.admin_router)
    assert "/login" not in _paths(ctx)
    assert callable(ctx._spawn) and callable(ctx.lifespan)
    # The phase carried the app's own configuration into the model runtime.
    features = ctx.model_runtime_service.runtime_features()
    assert features["mode"] == "local"
    assert features["data_dir"] == str(ctx.DATA_DIR)
    assert features["model_memory_policy"]["max_local_models"] == 1
    # ensure_agent_root() ran against the redirected root, not the repo.
    assert latticeai_tools.AGENT_ROOT.is_dir()


def test_web_phase_reports_a_disabled_graph_instead_of_failing(late_context):
    ctx = _through_web(late_context())

    assert ctx._graph_stats_safe() == {"disabled": True}


def test_web_phase_graph_stats_closure_reads_the_graph_when_it_is_enabled(late_context):
    ctx = _through_web(late_context(LATTICEAI_ENABLE_GRAPH="true"))

    assert ctx._graph_stats_safe() == {"total_nodes": 7, "total_edges": 3}


def test_web_phase_publishes_a_no_store_ui_response_and_an_open_invite_gate(
    late_context,
):
    ctx = _through_web(late_context())
    page = ctx.STATIC_DIR / "index.html"
    page.write_text("<h1>wp36</h1>", encoding="utf-8")

    response = ctx.ui_file_response(page)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    # With no invite gate configured every request is already authorized.
    assert ctx.invite_authorized(SimpleNamespace(cookies={})) is True


def test_web_phase_invite_gate_refuses_a_request_without_a_signed_claim(late_context):
    ctx = _through_web(
        late_context(
            LATTICEAI_INVITE_GATE_ENABLED="true",
            LATTICEAI_INVITE_CODE="letmein",
            LATTICEAI_INVITE_COOKIE_SECRET="s3cret",
        )
    )

    assert ctx.invite_authorized(SimpleNamespace(cookies={})) is False


def test_web_phase_security_helpers_derive_uploads_from_the_real_audit_log(
    late_context,
):
    ctx = _through_web(late_context())
    ctx.append_audit_event(
        "document_upload",
        filename="notes.pdf",
        user_email="a@example.com",
        ext=".pdf",
        bytes=12,
    )

    events = ctx._security_audit_events_safe()
    files = ctx._security_list_uploaded_files()

    assert [event["event_type"] for event in events] == ["document_upload"]
    assert files[0]["file_id"] == "notes.pdf"
    assert files[0]["user_email"] == "a@example.com"
    assert files[0]["sensitivity"] == "none"
    # The hardening closure answers from the config the phase was given.
    hardening = ctx._product_hardening_status()
    assert {"backup", "device_identity", "privacy", "storage"} <= set(hardening)


# ── phase 8: mounting the foundation routers ─────────────────────────────────
def test_foundation_routes_phase_mounts_auth_admin_and_workspace_routers(late_context):
    ctx = late_context()
    before = _paths(_through_services(ctx))

    phase_foundation_routes(ctx)

    added = _paths(ctx) - before
    assert {
        "/login",
        "/logout",
        "/admin/policies",
        "/invitations",
        "/admin/security/overview",
        "/workspace/os",
    } <= added
    # The workspace router is built *here* from the AppContext phase 7 made.
    assert ctx.app.state.context is ctx.app_context


def test_foundation_routes_phase_serves_the_mounted_surfaces(late_context):
    ctx = _through_foundation_routes(late_context())
    client = TestClient(ctx.app)

    workspace = client.get("/workspace/os")
    sso = client.get("/auth/sso/config")
    policies = client.get("/admin/policies")

    assert workspace.status_code == 200
    assert workspace.json()["active_workspace"] == "personal"
    assert sso.status_code == 200 and sso.json()["enabled"] is False
    assert policies.status_code == 200 and policies.json()["policies"]


# ── phase 9: platform features ───────────────────────────────────────────────
def test_platform_features_phase_publishes_the_automation_runtime(late_context):
    ctx = _through_platform_features(late_context())

    assert ctx.PLATFORM.llm_available() is True
    assert ctx.app.state.run_executor is ctx.RUN_EXECUTOR
    # reconcile_startup() ran against the real workspace store: nothing was
    # interrupted because this store was created moments ago.
    assert ctx.app.state.run_reconciliation == {"count": 0, "interrupted": []}
    assert ctx.REVIEW_QUEUE is ctx._automation_runtime["REVIEW_QUEUE"]
    assert ctx.AGENT_RUNTIME is ctx._automation_runtime["AGENT_RUNTIME"]
    # Proposal-first mutation: the agent loop's governor is the same service
    # the Review Center approves through.
    assert ctx.CHAT_AGENT_RUNTIME.deps.change_governor is ctx.CHANGE_PROPOSALS


def test_platform_features_phase_binds_the_trigger_and_builtin_hook_runners(
    late_context,
):
    ctx = late_context()
    _through_foundation_routes(ctx)
    assert ctx.HOOKS_REGISTRY.has_runner("builtin:redact-secrets") is False

    phase_platform_features(ctx)

    trigger_hook = next(
        hook
        for hook in ctx.HOOKS_REGISTRY.list()["hooks"]
        if hook["name"] == TRIGGER_HOOK_NAME
    )
    assert ctx.HOOKS_REGISTRY.has_runner(trigger_hook["id"]) is True
    assert ctx.HOOKS_REGISTRY.has_runner("builtin:redact-secrets") is True
    assert ctx.HOOKS_REGISTRY.has_runner("builtin:sensitive-data-guard") is True


def test_platform_features_phase_builds_services_that_answer(late_context):
    ctx = _through_platform_features(late_context())

    voice = ctx.VOICE_CAPTURE.status()
    evidence = ctx.EVIDENCE_ACTIONS_SERVICE.actions_for(
        question="이 문서 요약해줘", source_ids=["node:missing"]
    )

    # The graph is off, so the phase gave the capture service no pipeline —
    # and the service says so rather than pretending it can store a memo.
    assert voice["capture"] is False
    assert voice["transcription"] is False
    assert ".m4a" in voice["supported_extensions"]
    # The evidence service reads nodes through the graph handle the phase
    # passed it; this fake resolves nothing, so no action is offered.
    assert evidence["missing"] == ["node:missing"]
    assert evidence["actions"] == []
    assert ctx.CHANGE_PROPOSALS.counts() == {"pending": 0}


def test_platform_features_phase_registers_working_feature_routers(late_context):
    ctx = _through_platform_features(late_context())
    client = TestClient(ctx.app)

    assert {
        "/plugins/registry",
        "/workflows/api/definitions",
        "/agents/api/runs",
        "/marketplace/templates",
        "/realtime/feed",
        "/api/automation/suggestions",
        "/api/admin/funnel-metrics",
        "/api/command/briefing",
        "/api/proposals",
        "/api/evidence/actions",
        "/api/capture/voice",
        "/api/projects",
    } <= _paths(ctx)

    created = client.post("/api/projects", json={"title": "wp36 loop", "goal": "cover"})
    listed = client.get("/api/projects")

    assert created.status_code == 200
    assert listed.status_code == 200
    assert [row["title"] for row in listed.json()["projects"]] == ["wp36 loop"]
    assert listed.json()["count"] == 1
    # The store the router writes through is the one the phase published.
    assert ctx.PROJECT_SESSIONS.get(created.json()["id"]) is not None


# ── phase 10: interaction routers ────────────────────────────────────────────
def test_interaction_phase_publishes_the_model_runtime_stage(late_context):
    ctx = _through_interaction(late_context())

    assert ctx.model_runtime.router is ctx.model_router
    assert ctx.model_runtime.runtime_service is ctx.model_runtime_service
    assert ctx.model_runtime.is_public is False
    assert ctx.model_runtime.service.health_base(
        version=ctx.APP_VERSION, mode="local"
    ) == {
        "status": "ok",
        "version": ctx.APP_VERSION,
        "mode": "local",
        "platform": "AI Workspace OS",
    }


def test_interaction_phase_registers_the_chat_search_tool_and_brain_routers(
    late_context,
):
    ctx = late_context()
    before = _paths(_through_platform_features(ctx))

    phase_interaction(ctx)

    added = _paths(ctx) - before
    # ``GET /chat`` is the static shell page the web phase already built, so
    # the chat router shows up as a new *method* on an existing path.
    assert _has_route(ctx, "/chat", "POST")
    assert {
        "/health",
        "/models",
        "/agent",
        "/api/search/hybrid",
        "/tools/read_file",
        "/api/hooks",
        "/api/memory/recall",
        "/api/brain/health",
        "/agents/api/registry",
        "/automation/reviews",
        "/api/browser/read-url",
        "/api/knowledge-graph/export",
        "/network/peers",
        "/garden/tree",
        "/setup/scan",
    } <= added


def test_runtime_features_endpoint_answers_through_the_registered_router(late_context):
    ctx = _through_interaction(late_context())

    response = TestClient(ctx.app).get("/runtime_features")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "local"
    assert payload["data_dir"] == str(ctx.DATA_DIR)
    assert payload["graph_enabled"] is False
    assert payload["security"]["require_auth"] is False
    # The answer came through the model runtime service phase 6 built, which
    # is holding the router phase 5 would have produced.
    assert payload["model_memory_policy"] == ctx.model_router.model_memory_policy()
