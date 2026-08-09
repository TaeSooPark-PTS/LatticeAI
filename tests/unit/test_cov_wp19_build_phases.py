"""wp19: the build phases' degraded branches and the closures they publish.

``tests/unit/test_runtime_context.py`` proves the *order* of the phases, and it
does so in a sandboxed subprocess — so none of the phase bodies is measured in
process, and none of the closures a phase publishes is ever called there.

These tests run individual phases against a hand-built
:class:`~latticeai.runtime.runtime_context.RuntimeContext`. That is the honest
unit for a phase: the context is exactly the interface a phase reads and
writes, and a closure resolves ``ctx`` at *call* time, which is the property
the decomposition exists to preserve. Dependencies a phase happens to
construct on the way (the knowledge graph, the persistence registries, the
embedding provider) are swapped at their own module so nothing here touches a
GPU, a network, or a directory outside ``tmp_path`` — every one of them has its
own tests. What is asserted is what the phase itself decides: which branch it
takes when a dependency is missing, and what its closures return.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

from latticeai.core.config import Config
from latticeai.runtime import build_phases
from latticeai.runtime.build_phases import (
    phase_brain,
    phase_config,
    phase_domain,
    phase_identity,
    phase_platform,
    phase_services,
)
from latticeai.runtime.runtime_context import RuntimeContext


# ── shared fixtures / builders ───────────────────────────────────────────────
def _config(tmp_path: Path, **overrides: str) -> Config:
    """A Config built from an explicit mapping — never the real environment."""
    env = {
        "LATTICEAI_DATA_DIR": str(tmp_path / "data"),
        "LATTICEAI_STATIC_DIR": str(tmp_path / "static"),
        "LATTICEAI_ENABLE_GRAPH": "false",
        "LATTICEAI_ENABLE_TELEGRAM": "false",
        "LATTICEAI_REQUIRE_AUTH": "false",
        "LATTICEAI_MODE": "local",
    }
    env.update(overrides)
    return Config.from_env(env)


def _identity_ctx(tmp_path: Path) -> RuntimeContext:
    """A context carrying everything phases 2 and 3 publish."""
    ctx = RuntimeContext(_config(tmp_path))
    phase_config(ctx)
    phase_identity(ctx)
    return ctx


class _FakeEmbedder:
    dim = 384
    requested = "openai"
    detail = "connection refused"

    def __init__(self, fell_back: bool = True) -> None:
        self.fell_back = fell_back

    def as_dict(self) -> Dict[str, Any]:
        return {"requested_provider": self.requested, "fell_back": self.fell_back}


class _RecordingConversations:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def append(self, item: Dict[str, Any]) -> None:
        self.rows.append(item)


# ── phase 1: platform ────────────────────────────────────────────────────────
def test_platform_phase_pins_mlx_to_the_gpu_device(monkeypatch, capsys):
    """A fake ``mlx`` keeps the hardware branch executable on every platform."""
    selected: List[Any] = []
    gpu = object()
    core = ModuleType("mlx.core")
    core.gpu = gpu
    core.set_default_device = selected.append
    package = ModuleType("mlx")
    package.core = core
    monkeypatch.setitem(sys.modules, "mlx", package)
    monkeypatch.setitem(sys.modules, "mlx.core", core)

    ctx = RuntimeContext()
    phase_platform(ctx)

    assert ctx.mx is core
    assert selected == [gpu]
    assert "MLX Metal context initialized" in capsys.readouterr().out


def test_platform_phase_degrades_when_mlx_cannot_be_imported(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mlx", None)
    monkeypatch.setitem(sys.modules, "mlx.core", None)

    ctx = RuntimeContext()
    phase_platform(ctx)

    assert ctx.mx is None
    assert "MLX Metal context unavailable" in capsys.readouterr().out


# ── phase 2: config ──────────────────────────────────────────────────────────
def test_config_phase_lays_out_a_private_data_directory(tmp_path):
    ctx = RuntimeContext(_config(tmp_path))

    phase_config(ctx)

    assert ctx.DATA_DIR == tmp_path / "data"
    assert ctx.DATA_DIR.is_dir()
    assert ctx.USERS_FILE == ctx.DATA_DIR / "users.json"
    assert ctx.AUDIT_FILE == ctx.DATA_DIR / "audit_log.json"
    assert ctx.SSO_FILE == ctx.DATA_DIR / "sso_config.json"
    assert ctx.VPC_FILE == ctx.DATA_DIR / "vpc_config.json"
    assert ctx.APP_MODE == "local"
    assert ctx.APP_VERSION
    assert ctx.produced_by["CONFIG"] == "config"
    if sys.platform != "win32":
        assert ctx.DATA_DIR.stat().st_mode & 0o777 == 0o700


def test_config_phase_survives_a_filesystem_that_refuses_chmod(monkeypatch, tmp_path):
    """A mounted volume that ignores POSIX modes must not stop the build."""

    def refuse(self, mode, **kwargs):
        raise OSError("operation not permitted")

    monkeypatch.setattr(Path, "chmod", refuse)
    ctx = RuntimeContext(_config(tmp_path))

    phase_config(ctx)

    assert ctx.DATA_DIR.is_dir()
    assert ctx.STATIC_DIR == tmp_path / "static"


def test_config_phase_runs_with_no_keyring_installed(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "keyring", None)
    ctx = RuntimeContext(_config(tmp_path))

    phase_config(ctx)

    assert ctx.keyring is None


# ── phase 3: identity closures ───────────────────────────────────────────────
def test_load_users_reports_a_failed_identity_migration_and_still_returns(
    monkeypatch, tmp_path, caplog
):
    def refuse(path, email_to_id):
        raise RuntimeError("graph locked")

    monkeypatch.setattr(
        "latticeai.core.users.migrate_knowledge_graph_identity", refuse
    )
    ctx = _identity_ctx(tmp_path)
    ctx.USERS_FILE.write_text(
        '{"a@example.com": {"id": "user:1", "password": "x"}, "broken": "not-a-dict"}',
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        users = ctx.load_users()

    # The malformed row is dropped by the users-file migration, not here.
    assert set(users) == {"a@example.com"}
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "knowledge graph identity migration skipped" in messages
    # WORKSPACE_OS belongs to a later phase, so the closure's late binding is
    # what makes the second migration best-effort too.
    assert "workspace identity migration skipped" in messages


def test_save_users_round_trips_through_the_users_file(tmp_path):
    ctx = _identity_ctx(tmp_path)

    ctx.save_users({"b@example.com": {"id": "user:2", "nickname": "Bee"}})

    assert ctx.USERS_FILE.exists()
    assert ctx.load_users()["b@example.com"]["nickname"] == "Bee"


def test_user_id_for_email_reads_the_id_stored_for_that_user(tmp_path):
    ctx = _identity_ctx(tmp_path)
    ctx.save_users({"c@example.com": {"id": "user:stored"}})

    assert ctx.user_id_for_email("c@example.com") == "user:stored"


def test_verify_and_migrate_password_checks_an_already_hashed_secret(tmp_path):
    from latticeai.core.security import hash_password

    ctx = _identity_ctx(tmp_path)
    stored = hash_password("correct horse")
    users = {"d@example.com": {"id": "user:4", "password": stored}}
    ctx.save_users(users)

    assert ctx.verify_and_migrate_password("d@example.com", "correct horse", stored, users)
    assert not ctx.verify_and_migrate_password("d@example.com", "wrong", stored, users)


def test_verify_and_migrate_password_upgrades_a_plaintext_secret(tmp_path):
    ctx = _identity_ctx(tmp_path)
    audited: List[Dict[str, Any]] = []
    ctx.set(append_audit_event=lambda event, **payload: audited.append({event: payload}))
    users = {"e@example.com": {"id": "user:5", "password": "plaintext"}}
    ctx.save_users(users)

    assert ctx.verify_and_migrate_password("e@example.com", "plaintext", "plaintext", users)

    stored_now = ctx.load_users()["e@example.com"]["password"]
    assert stored_now != "plaintext"
    assert ":" in stored_now and len(stored_now) > 64
    assert audited == [{"password_migrated_from_plaintext": {"user_email": "e@example.com"}}]
    # A wrong password neither migrates nor authenticates.
    assert not ctx.verify_and_migrate_password("e@example.com", "nope", "plaintext", users)


def test_password_migration_survives_an_unwritable_audit_log(tmp_path, caplog):
    ctx = _identity_ctx(tmp_path)

    def refuse(event, **payload):
        raise OSError("audit log read-only")

    ctx.set(append_audit_event=refuse)
    users = {"f@example.com": {"id": "user:6", "password": "plaintext"}}
    ctx.save_users(users)

    with caplog.at_level(logging.WARNING):
        assert ctx.verify_and_migrate_password(
            "f@example.com", "plaintext", "plaintext", users
        )

    assert "audit log failed on password migration" in caplog.text
    assert ctx.load_users()["f@example.com"]["password"] != "plaintext"


def test_redact_secret_text_masks_secrets_before_they_are_logged(tmp_path):
    ctx = _identity_ctx(tmp_path)

    redacted = ctx.redact_secret_text("api_key=sk-abcdefghijklmnopqrstuvwx")

    assert "sk-abcdefghijklmnopqrstuvwx" not in redacted
    assert "REDACTED" in redacted


def test_check_rate_limit_refuses_the_call_over_the_allowance(monkeypatch, tmp_path):
    # A private window dict: the per-IP ledger is process-global.
    monkeypatch.setattr("latticeai.core.security._ip_rate_windows", {})
    ctx = _identity_ctx(tmp_path)

    ctx._check_rate_limit("203.0.113.7", "wp19-probe", 1, 60.0)
    with pytest.raises(HTTPException) as raised:
        ctx._check_rate_limit("203.0.113.7", "wp19-probe", 1, 60.0)

    assert raised.value.status_code == 429


def test_enforce_rate_limit_binds_the_configured_switch(monkeypatch, tmp_path):
    """The closure exists to carry ``rate_limit_enabled`` to every caller."""
    calls: List[Any] = []
    monkeypatch.setattr(
        "latticeai.core.security.enforce_rate_limit",
        lambda email, bucket, *, enabled=True: calls.append((email, bucket, enabled)),
    )
    ctx = RuntimeContext(_config(tmp_path))
    phase_config(ctx)
    phase_identity(ctx)

    ctx.enforce_rate_limit("j@example.com", "chat")

    assert calls == [("j@example.com", "chat", ctx._RATE_LIMIT_ENABLED)]


def test_session_runtime_closures_share_one_store(tmp_path):
    from latticeai.runtime.bootstrap import build_session_runtime

    runtime = build_session_runtime(
        user_id_resolver=lambda email: "user:99" if email == "k@example.com" else None
    )

    token = runtime["create_session"]("k@example.com")
    assert runtime["get_session_email"](token) == "k@example.com"
    assert runtime["get_session_user_id"](token) == "user:99"

    runtime["invalidate_session"](token)
    assert runtime["get_session_email"](token) is None
    assert runtime["_SESSION_TTL"] == 60 * 60 * 24

    # An unknown email falls back to the email itself as the subject.
    other = runtime["create_session"]("l@example.com")
    assert runtime["get_session_user_id"](other) == "l@example.com"


def test_client_ip_honours_a_forwarded_header_only_behind_a_trusted_proxy(
    monkeypatch, tmp_path
):
    import ipaddress

    ctx = _identity_ctx(tmp_path)
    request = SimpleNamespace(
        client=SimpleNamespace(host="198.51.100.4"),
        headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
    )

    monkeypatch.setattr("latticeai.core.security._trusted_proxies", [])
    assert ctx._client_ip(request) == "198.51.100.4"

    monkeypatch.setattr(
        "latticeai.core.security._trusted_proxies",
        [ipaddress.ip_network("198.51.100.0/24")],
    )
    assert ctx._client_ip(request) == "203.0.113.9"


# ── phase 4: brain ───────────────────────────────────────────────────────────
def _patch_brain_builders(
    monkeypatch,
    *,
    embedder: _FakeEmbedder,
    profile_error: bool,
    embedder_calls: List[Dict[str, Any]],
):
    """Swap the phase's heavy constructors; each has its own tests."""
    if profile_error:
        def profile(name):
            raise ValueError(f"unknown embedding profile: {name!r}")
    else:
        def profile(name):
            return {"provider": "ollama", "model": "bge-m3", "dimensions": 1024}

    def resolve(provider, **kwargs):
        embedder_calls.append({"provider": provider, **kwargs})
        return embedder

    monkeypatch.setattr(
        "latticeai.core.embedding_providers.resolve_embedding_profile", profile
    )
    monkeypatch.setattr(
        "latticeai.core.embedding_providers.resolve_embedder", resolve
    )
    # The embed dimension is process-global; the real value must survive.
    monkeypatch.setattr("lattice_brain.graph.schema.set_embed_dim", lambda dim: None)
    monkeypatch.setattr(
        "lattice_brain.storage.storage_from_env",
        lambda env, data_dir=None: SimpleNamespace(name="storage"),
    )
    monkeypatch.setattr(
        "latticeai.runtime.brain_runtime.build_brain_runtime",
        lambda **kwargs: {
            "KNOWLEDGE_GRAPH": SimpleNamespace(
                stats=lambda: {"total_nodes": 3, "total_edges": 2}
            ),
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
                "REALTIME_BUS",
                "WORKSPACE_OS",
                "WORKSPACE_SERVICE",
                "INVITATION_STORE",
                "PLUGIN_REGISTRY",
                "TEMPLATE_CATALOG",
                "AGENT_REGISTRY",
                "MEMORY_SERVICE",
                "BRAIN_INTELLIGENCE",
                "AUTOMATION_INTELLIGENCE",
                "INGESTION_PIPELINE",
                "DEVICE_IDENTITY",
                "KG_PORTABILITY",
                "FUNNEL_METRICS",
            )
        },
    )
    monkeypatch.setattr(
        "latticeai.runtime.history_runtime.build_history_query_runtime",
        lambda **kwargs: {
            name: (lambda *a, **k: None)
            for name in (
                "_history_allowed_workspaces_for",
                "_history_include_legacy_global",
                "get_history",
                "conversation_title",
                "group_history_conversations",
                "get_conversation_messages",
                "clear_history",
                "clear_conversation",
            )
        },
    )


def _brain_ctx(
    monkeypatch, tmp_path, *, embedder=None, profile_error=False, embedder_calls=None
):
    embedder = embedder or _FakeEmbedder(fell_back=False)
    _patch_brain_builders(
        monkeypatch,
        embedder=embedder,
        profile_error=profile_error,
        embedder_calls=embedder_calls if embedder_calls is not None else [],
    )
    ctx = RuntimeContext(
        _config(
            tmp_path,
            LATTICEAI_ENABLE_GRAPH="true",
            LATTICEAI_EMBEDDING_PROFILE="bge-m3",
        )
    )
    phase_config(ctx)
    phase_identity(ctx)
    phase_brain(ctx)
    return ctx


def test_brain_phase_degrades_when_the_configured_profile_is_unknown(
    monkeypatch, tmp_path, caplog
):
    embedder = _FakeEmbedder(fell_back=True)
    with caplog.at_level(logging.WARNING):
        ctx = _brain_ctx(
            monkeypatch,
            tmp_path.joinpath("a"),
            embedder=embedder,
            profile_error=True,
        )

    assert ctx.EMBEDDING_PROFILE == {}
    assert ctx.EMBEDDER is embedder
    assert "Embedding profile ignored" in caplog.text
    # Falling back is reported, not hidden: requested provider + failure detail.
    assert "Embedding provider openai unavailable: connection refused" in caplog.text


def test_brain_phase_lets_the_profile_override_the_default_hash_provider(
    monkeypatch, tmp_path
):
    calls: List[Dict[str, Any]] = []
    ctx = _brain_ctx(monkeypatch, tmp_path, embedder_calls=calls)

    assert ctx.EMBEDDING_PROFILE["provider"] == "ollama"
    # The configured provider is the default "hash"; a named profile replaces
    # it (and only then is the provider probed for reachability).
    assert calls[0]["provider"] == "ollama"
    assert calls[0]["model"] == "bge-m3"
    assert calls[0]["dim"] == 1024
    assert calls[0]["probe"] is True
    assert ctx.STORAGE_ENGINE.name == "storage"
    assert ctx.KNOWLEDGE_GRAPH.stats() == {"total_nodes": 3, "total_edges": 2}


def test_save_to_history_redacts_audits_and_stores_one_turn(monkeypatch, tmp_path):
    ctx = _brain_ctx(monkeypatch, tmp_path)
    conversations = _RecordingConversations()
    ingested: List[Any] = []
    audited: List[Dict[str, Any]] = []
    ctx.set(
        CONVERSATIONS=conversations,
        append_audit_event=lambda event, **payload: audited.append({event: payload}),
        INGESTION_PIPELINE=SimpleNamespace(
            ingest=lambda item, user_email=None: ingested.append((item, user_email))
        ),
    )

    ctx.save_to_history(
        "user",
        "my key is api_key=sk-abcdefghijklmnopqrstuvwx",
        user_email="g@example.com",
        conversation_id="conv-1",
        workspace_id="ws-1",
    )

    stored = conversations.rows[0]
    assert "sk-abcdefghijklmnopqrstuvwx" not in stored["content"]
    assert stored["conversation_id"] == "conv-1"
    assert stored["workspace_id"] == "ws-1"
    assert list(audited[0]) == ["chat_message"]
    assert audited[0]["chat_message"]["user_email"] == "g@example.com"
    # The Brain grows through the ingestion pipeline, not a direct graph write.
    assert ingested and ingested[0][1] == "g@example.com"


def test_sensitivity_helpers_classify_a_single_turn_and_a_history(monkeypatch, tmp_path):
    ctx = _brain_ctx(monkeypatch, tmp_path)

    single = ctx.classify_sensitive_message(
        {"role": "user", "content": "주민등록번호 900101-1234567"}, 0
    )
    report = ctx.build_sensitivity_report(
        [
            {"role": "user", "content": "주민등록번호 900101-1234567"},
            {"role": "user", "content": "안녕하세요"},
        ]
    )

    assert single["risk_fields"]
    assert single["sensitivity"] != "none"
    assert report["summary"]["risky_messages"] == 1


def test_admin_audit_report_carries_graph_stats_when_the_graph_answers(
    monkeypatch, tmp_path
):
    ctx = _brain_ctx(monkeypatch, tmp_path)

    report = ctx.build_admin_audit_report(
        {"h@example.com": {"id": "user:8"}},
        audit_events=[
            {"event_type": "chat_message", "role": "user", "user_email": "h@example.com"}
        ],
    )

    assert report["summary"]["graph_nodes"] == 3
    assert report["summary"]["graph_edges"] == 2
    assert any(row["email"] == "h@example.com" for row in report["per_user"])


def test_admin_audit_report_omits_graph_stats_when_the_graph_fails(
    monkeypatch, tmp_path
):
    ctx = _brain_ctx(monkeypatch, tmp_path)

    def explode():
        raise RuntimeError("graph unavailable")

    ctx.set(KNOWLEDGE_GRAPH=SimpleNamespace(stats=explode))

    report = ctx.build_admin_audit_report({}, audit_events=[])

    # A graph that cannot answer leaves the report without graph counters
    # rather than failing the whole admin surface.
    assert "graph_nodes" not in report["summary"]
    assert report["per_user"] == []


def test_require_graph_refuses_when_the_knowledge_graph_is_off(monkeypatch, tmp_path):
    ctx = _brain_ctx(monkeypatch, tmp_path)

    assert ctx._require_graph() is None
    assert ctx._workspace_graph() is ctx.KNOWLEDGE_GRAPH

    ctx.set(ENABLE_GRAPH=False)
    assert ctx._workspace_graph() is None
    with pytest.raises(HTTPException) as raised:
        ctx._require_graph()

    assert raised.value.status_code == 404
    assert "LATTICEAI_ENABLE_GRAPH" in raised.value.detail


def test_bytes_match_extension_is_published_as_a_content_sniffer(
    monkeypatch, tmp_path
):
    ctx = _brain_ctx(monkeypatch, tmp_path)

    assert ctx._bytes_match_extension(b"%PDF-1.7 ...", ".pdf") is True
    assert ctx._bytes_match_extension(b"not a pdf", ".pdf") is False


# ── phase 5: domain ──────────────────────────────────────────────────────────
def _domain_ctx(monkeypatch, import_vault) -> RuntimeContext:
    """Run ``phase_domain`` over a garden whose vault import behaves as given."""
    router = SimpleNamespace(name="router")
    registered: List[Any] = []

    class _Gardener:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def import_vault(self):
            return import_vault()

    monkeypatch.setattr("latticeai.models.router.LLMRouter", lambda: router)
    monkeypatch.setattr("lattice_brain.graph.runtime.set_llm_router", registered.append)
    monkeypatch.setattr(
        "latticeai.services.tool_dispatch.configure_tool_dispatch",
        lambda **kwargs: None,
    )
    monkeypatch.setattr("latticeai.services.p_reinforce.PReinforceGardener", _Gardener)
    monkeypatch.setattr(
        "latticeai.services.chat_service.ChatService",
        lambda **kwargs: SimpleNamespace(kwargs=kwargs),
    )

    ctx = RuntimeContext()
    ctx.set(
        ENABLE_GRAPH=True,
        INGESTION_PIPELINE=object(),
        KNOWLEDGE_GRAPH=object(),
        WORKSPACE_OS=object(),
        load_users=dict,
        get_user_role=lambda email, users=None: "user",
        get_history=lambda *a, **k: [],
        save_to_history=lambda *a, **k: None,
        get_history_user=lambda *a, **k: {},
    )
    phase_domain(ctx)
    ctx.set(_wp19_router_registrations=registered)
    return ctx


def test_domain_phase_survives_a_failing_garden_vault_import(monkeypatch, caplog):
    def explode():
        raise OSError("vault directory unreadable")

    with caplog.at_level(logging.WARNING):
        ctx = _domain_ctx(monkeypatch, explode)

    assert "garden vault import skipped: vault directory unreadable" in caplog.text
    assert ctx._wp19_router_registrations == [ctx.model_router], (
        "the graph runtime must be given the same router the phase built"
    )
    assert ctx.gardener.kwargs["knowledge_graph"] is ctx.KNOWLEDGE_GRAPH
    assert ctx.CHAT_SERVICE.kwargs["store"] is ctx.WORKSPACE_OS


def test_domain_phase_reports_notes_the_garden_could_not_ingest(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        _domain_ctx(monkeypatch, lambda: {"imported": 4, "failed": 2})

    assert "2 notes failed to ingest" in caplog.text


def test_domain_phase_stays_quiet_when_the_vault_imports_cleanly(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        ctx = _domain_ctx(monkeypatch, lambda: {"imported": 4, "failed": 0})

    assert "garden vault import" not in caplog.text
    assert ctx.produced_by["gardener"] == "domain"


# ── phase 7: services payload closures ───────────────────────────────────────
_HISTORY = [
    {"role": "user", "content": "안녕", "workspace_id": "ws-1", "conversation_id": "c1"},
    {"role": "assistant", "content": "반가워요", "workspace_id": "ws-1", "conversation_id": "c1"},
    {"role": "user", "content": "다른 방", "workspace_id": "ws-2", "conversation_id": "c2"},
]


def _services_ctx(monkeypatch, tmp_path, *, require_auth: bool) -> RuntimeContext:
    monkeypatch.setattr(
        "latticeai.runtime.context_runtime.build_context_runtime",
        lambda **kwargs: {
            "SEARCH_SERVICE": object(),
            "BRAIN_MEMORY": object(),
            "CONTEXT_ASSEMBLER": object(),
            "ARTIFACT_LEDGER": object(),
            "_scoped_hybrid_search": lambda *a, **k: [],
        },
    )
    monkeypatch.setattr(
        "latticeai.runtime.chat_wiring.build_chat_agent_runtime_from_context",
        lambda **kwargs: SimpleNamespace(deps=SimpleNamespace(), kwargs=kwargs),
    )

    ctx = RuntimeContext()
    noop = lambda *args, **kwargs: None  # noqa: E731 — a placeholder dependency
    ctx.set(
        APP_MODE="local",
        DEFAULT_HOST="127.0.0.1",
        DEFAULT_PORT=4825,
        REQUIRE_AUTH=require_auth,
        ENABLE_GRAPH=True,
        ENABLE_TELEGRAM=False,
        ALLOW_LOCAL_MODELS=True,
        STATIC_DIR=tmp_path / "static",
        DATA_DIR=tmp_path / "data",
        BASE_DIR=tmp_path,
        CONFIG=SimpleNamespace(embedding_profile="bge-m3"),
        EMBEDDER=_FakeEmbedder(fell_back=True),
        PUBLIC_MODEL="openai:gpt-4o-mini",
        LOCAL_MODEL="mlx-community/gemma",
        LOCAL_DRAFT_MODEL="mlx-community/gemma-draft",
        model_router=SimpleNamespace(
            current_model_id="mlx-community/gemma",
            loaded_model_ids=["mlx-community/gemma"],
        ),
        PLATFORM=SimpleNamespace(allowed_scopes=lambda user: {"ws-1"}),
        get_history=lambda *args, **kwargs: list(_HISTORY),
        gardener=object(),
        INGESTION_PIPELINE=object(),
        MEMORY_SERVICE=object(),
        _workspace_graph=lambda: None,
        _spawn=noop,
        clear_history=noop,
        append_audit_event=noop,
        HOOKS_REGISTRY=object(),
        WORKSPACE_OS=object(),
        WORKSPACE_SERVICE=object(),
        KNOWLEDGE_GRAPH=object(),
        LOCAL_KG_WATCHER=None,
        CHAT_SERVICE=object(),
        REALTIME_BUS=object(),
        FUNNEL_METRICS=object(),
        require_user=noop,
        require_admin=noop,
        get_current_user=noop,
        load_users=dict,
        get_user_role=lambda email, users=None: "user",
        enforce_rate_limit=noop,
        _history_allowed_workspaces_for=noop,
        get_audit_log=list,
        get_history_user=noop,
        save_to_history=noop,
        clear_conversation=noop,
        group_history_conversations=noop,
        get_conversation_messages=noop,
        conversation_title=noop,
        _require_graph=noop,
        _graph_stats_safe=noop,
        local_sysinfo=noop,
        redact_secret_text=str,
        ui_file_response=noop,
        app=SimpleNamespace(state=SimpleNamespace()),
    )
    phase_services(ctx)
    return ctx


def test_services_phase_publishes_the_workspace_payload_closures(
    monkeypatch, tmp_path
):
    ctx = _services_ctx(monkeypatch, tmp_path, require_auth=False)

    assert ctx._workspace_settings_payload() == {
        "mode": "local",
        "host": "127.0.0.1",
        "port": 4825,
        "require_auth": False,
        "enable_graph": True,
        "allow_local_models": True,
        "static_dir": str(tmp_path / "static"),
        "data_dir": str(tmp_path / "data"),
    }
    assert ctx._workspace_models_payload() == {
        "current_model": "mlx-community/gemma",
        "loaded_models": ["mlx-community/gemma"],
        "public_model": "openai:gpt-4o-mini",
        "local_model": "mlx-community/gemma",
        "local_draft_model": "mlx-community/gemma-draft",
    }
    # The AppContext is the object the routers actually receive.
    assert ctx.app.state.context is ctx.app_context
    assert ctx.app_context.workspace_settings is ctx._workspace_settings_payload
    assert ctx.on_chat_message is None, "telegram is off, so no mirror is registered"


def test_embedding_info_reports_the_active_provider_and_the_catalogue(
    monkeypatch, tmp_path
):
    ctx = _services_ctx(monkeypatch, tmp_path, require_auth=False)

    info = ctx._embedding_info()

    assert info["requested_provider"] == "openai"
    assert info["fell_back"] is True
    assert info["profile"] == "bge-m3"
    assert "hash" in info["available_providers"]
    assert isinstance(info["profiles"], list) and info["profiles"]


def test_allowed_workspaces_is_unscoped_without_auth_and_scoped_with_it(
    monkeypatch, tmp_path
):
    open_ctx = _services_ctx(monkeypatch, tmp_path / "open", require_auth=False)
    assert open_ctx._allowed_workspaces_for("i@example.com") is None

    auth_ctx = _services_ctx(monkeypatch, tmp_path / "auth", require_auth=True)
    # No caller identified → no scoping decision to make.
    assert auth_ctx._allowed_workspaces_for(None) is None
    assert auth_ctx._allowed_workspaces_for("i@example.com") == {"ws-1"}


def test_recent_chat_context_renders_the_conversation_for_the_prompt(
    monkeypatch, tmp_path
):
    ctx = _services_ctx(monkeypatch, tmp_path, require_auth=False)

    rendered = ctx._recent_chat_context(limit=2, workspace_id="ws-1")

    assert rendered == "user: 안녕\nassistant: 반가워요"
    assert "다른 방" not in rendered, "another workspace must not leak into the prompt"


# ── the exported build order ─────────────────────────────────────────────────
def test_every_exported_phase_is_part_of_the_build_order():
    ordered = {phase.__name__ for phase in build_phases.BUILD_PHASES}
    exported = set(build_phases.__all__) - {"BUILD_PHASES"}

    assert exported == ordered
