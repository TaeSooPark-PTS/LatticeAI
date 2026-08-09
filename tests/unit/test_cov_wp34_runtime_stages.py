"""Coverage for the small composition-root phases (wp34).

Each phase here is a builder that returns closures the app factory wires into
other subsystems.  The closures are the part that never ran under test, so the
tests reach them through the object the real builder produced (the pipeline's
audit sink, the agent runtime's memory ingest, the VPC helpers) rather than by
re-declaring them.
"""

from __future__ import annotations

import json

import pytest

from latticeai.runtime.automation_runtime import build_automation_runtime
from latticeai.runtime.chat_wiring import maybe_build_telegram_chat_mirror
from latticeai.runtime.namespace_runtime import (
    RuntimeBundle,
    build_runtime_namespace,
)
from latticeai.runtime.network_config_runtime import build_vpc_runtime
from latticeai.runtime.persistence_runtime import build_persistence_runtime
from latticeai.runtime.platform_runtime_wiring import (
    build_platform_automation_runtime,
)
from latticeai.runtime.security_runtime import (
    SecurityRuntime,
    _stored_security_secrets,
)


def _security_stage() -> SecurityRuntime:
    return SecurityRuntime(
        SSO_DISCOVERY_URL="",
        SSO_CLIENT_ID="",
        SSO_CLIENT_SECRET="",
        SSO_REDIRECT_URI="",
        SSO_PROVIDER_NAME="local",
        RATE_LIMIT_ENABLED=False,
        OPEN_REGISTRATION=True,
        INVITE_CODE="code",
        INVITE_COOKIE_SECRET="secret",
        INVITE_GATE_ENABLED=False,
        SECURE_COOKIES=False,
    )


# ── stages.RuntimeStage mapping surface ──────────────────────────────────────


def test_runtime_stage_is_a_mapping_over_its_dataclass_fields():
    stage = _security_stage()

    assert len(stage) == 11
    assert list(stage)[0] == "SSO_DISCOVERY_URL"
    assert dict(stage)["SSO_PROVIDER_NAME"] == "local"
    assert stage["INVITE_CODE"] == "code"


def test_runtime_stage_rejects_unknown_keys():
    with pytest.raises(KeyError):
        _security_stage()["NOT_A_FIELD"]


# ── security_runtime secret store ────────────────────────────────────────────


def test_corrupt_security_secret_store_rotates_instead_of_reusing(tmp_path):
    (tmp_path / "security_secrets.json").write_text("{not json", encoding="utf-8")

    assert _stored_security_secrets(tmp_path) == {}


def test_non_object_security_secret_store_is_ignored(tmp_path):
    (tmp_path / "security_secrets.json").write_text("[1, 2, 3]", encoding="utf-8")

    assert _stored_security_secrets(tmp_path) == {}


def test_security_secret_store_keeps_only_known_non_empty_keys(tmp_path):
    (tmp_path / "security_secrets.json").write_text(
        json.dumps({"invite_code": "abc", "invite_cookie_secret": "", "other": "x"}),
        encoding="utf-8",
    )

    assert _stored_security_secrets(tmp_path) == {"invite_code": "abc"}


# ── namespace_runtime export policy ──────────────────────────────────────────


def _bundle() -> RuntimeBundle:
    return RuntimeBundle(
        app="app",
        CONFIG="config",
        KNOWLEDGE_GRAPH="kg",
        INGESTION_PIPELINE="pipeline",
        AGENT_RUNTIME="agent",
        HOOKS_REGISTRY="hooks",
        REVIEW_QUEUE="queue",
        AGENT_REGISTRY="registry",
        model_router="router",
        build_runtime="build",
        get_shared_runtime="shared",
        create_app="create",
        config_runtime="config-stage",
        security_runtime="security-stage",
        brain_runtime="brain-stage",
        model_runtime="model-stage",
        router_bundle="router-stage",
    )


def test_runtime_bundle_exposes_its_five_typed_stages():
    assert _bundle().stages == {
        "config": "config-stage",
        "security": "security-stage",
        "brain": "brain-stage",
        "models": "model-stage",
        "routers": "router-stage",
    }


def test_unapproved_legacy_export_is_refused():
    with pytest.raises(ValueError, match="unapproved runtime exports"):
        build_runtime_namespace(
            runtime_bundle=_bundle(),
            legacy_exports={"_secret_backdoor": object()},
        )


def test_bundle_values_win_over_legacy_exports_and_none_values_are_dropped():
    exported = build_runtime_namespace(
        runtime_bundle={"hash_password": "from-bundle", "app": "app"},
        legacy_exports={"hash_password": "from-legacy", "verify_password": None},
    )

    assert exported["hash_password"] == "from-bundle"
    assert "verify_password" not in exported
    assert exported["_RUNTIME_BUNDLE"] == {"hash_password": "from-bundle", "app": "app"}


# ── network_config_runtime VPC helpers ───────────────────────────────────────


class _RecordingLogging:
    def __init__(self):
        self.warnings = []

    def warning(self, *args):
        self.warnings.append(args)


def test_vpc_config_defaults_when_no_file_exists(tmp_path):
    runtime = build_vpc_runtime(vpc_file=tmp_path / "vpc.json", logging=_RecordingLogging())

    loaded = runtime["load_vpc_config"]()

    assert loaded["provider"] == "AWS"
    assert loaded is not runtime["DEFAULT_VPC_CONFIG"]


def test_vpc_config_round_trips_through_save_and_load(tmp_path):
    vpc_file = tmp_path / "vpc.json"
    runtime = build_vpc_runtime(vpc_file=vpc_file, logging=_RecordingLogging())

    config = dict(runtime["DEFAULT_VPC_CONFIG"])
    config["region"] = "eu-west-1"
    runtime["save_vpc_config"](config)

    assert config["updated_at"]
    stored = json.loads(vpc_file.read_text(encoding="utf-8"))
    assert stored["region"] == "eu-west-1"

    loaded = runtime["load_vpc_config"]()
    assert loaded["region"] == "eu-west-1"
    assert loaded["cidr_block"] == runtime["DEFAULT_VPC_CONFIG"]["cidr_block"]


def test_unreadable_vpc_file_falls_back_to_defaults(tmp_path):
    vpc_file = tmp_path / "vpc.json"
    vpc_file.write_text("{broken", encoding="utf-8")
    log = _RecordingLogging()
    runtime = build_vpc_runtime(vpc_file=vpc_file, logging=log)

    assert runtime["load_vpc_config"]()["provider"] == "AWS"
    assert log.warnings, "a corrupt VPC profile must be reported, not silently ignored"


# ── platform_runtime_wiring / automation_runtime seams ───────────────────────


class _Store:
    def __init__(self):
        self.upserts = []

    def upsert_memory(self, **kwargs):
        if kwargs.get("content") == "boom":
            raise RuntimeError("memory backend down")
        self.upserts.append(kwargs)
        return {"id": "mem-1"}


class _ModelRouter:
    current_model_id = "local:test"

    def __init__(self):
        self.calls = []

    async def generate(self, message, *, context="", max_tokens=1024, temperature=0.1):
        self.calls.append((message, context, max_tokens, temperature))
        return "generated answer"


def _automation(tmp_path, store):
    return build_platform_automation_runtime(
        model_router=_ModelRouter(),
        workspace_store=store,
        workspace_service=object(),
        plugin_registry=object(),
        get_current_user=lambda _request: None,
        workspace_graph=lambda: None,
        workspace_scope_from_request=lambda _request: None,
        get_tool_permission=lambda *_a, **_k: {"requires_approval": False},
        hooks=None,
        agent_registry=None,
        data_dir=tmp_path,
        append_audit_event=lambda *_a, **_k: None,
        memory_service=None,
    )


def test_sync_llm_bridge_runs_the_async_router_and_returns_text(tmp_path):
    runtime = _automation(tmp_path, _Store())

    answer = runtime["_llm_generate_sync"]("hello", context="ctx", max_tokens=16, temperature=0.5)

    assert answer == "generated answer"
    assert runtime["PLATFORM"].llm_available() is True


def test_agent_memory_ingest_delegates_to_the_store_and_never_raises(tmp_path):
    store = _Store()
    runtime = build_automation_runtime(
        store=store,
        platform=type(
            "P",
            (),
            {
                "build_orchestrator": staticmethod(lambda *_a, **_k: object()),
                "build_workflow_runners": staticmethod(lambda *_a, **_k: {}),
                "run_workflow_by_id": staticmethod(lambda *_a, **_k: {}),
            },
        )(),
        data_dir=tmp_path,
        workspace_graph=lambda: None,
        append_audit_event=lambda *_a, **_k: None,
        hooks=None,
    )
    ingest = runtime["AGENT_RUNTIME"]._memory_ingest

    assert ingest(content="remembered", kind="long_term") == {"id": "mem-1"}
    assert store.upserts == [{"content": "remembered", "kind": "long_term"}]
    assert ingest(content="boom") is None


# ── persistence_runtime funnel audit wrapper ─────────────────────────────────


class _Hooks:
    def dispatch(self, *_args, **_kwargs):
        return None


def _persistence(tmp_path, audit):
    return build_persistence_runtime(
        data_dir=tmp_path,
        base_dir=tmp_path,
        enable_graph=False,
        knowledge_graph=None,
        hooks_registry=_Hooks(),
        history_file=tmp_path / "chat_history.json",
        conversations=None,
        user_id_for_email=lambda email: f"user:{email}" if email else None,
        audit=audit,
    )


def test_ingest_audit_bumps_the_funnel_and_still_forwards_the_event(tmp_path):
    seen = []
    runtime = _persistence(tmp_path, lambda action, detail, user: seen.append((action, detail, user)))

    funnel_audit = runtime["INGESTION_PIPELINE"]._audit
    funnel_audit("kg_ingest", {"duplicate": False}, "owner@example.com")
    funnel_audit("kg_ingest", {"duplicate": True}, "owner@example.com")
    funnel_audit("other_event", {}, None)

    snapshot = runtime["FUNNEL_METRICS"].snapshot()
    assert snapshot["counters"]["ingest_completions"] == 2, "only kg_ingest events count"
    assert snapshot["firsts"]["first_ingest_at"], "the first ingest starts the TTFV clock"
    assert [event[0] for event in seen] == ["kg_ingest", "kg_ingest", "other_event"]


def test_funnel_failure_never_breaks_ingestion_audit(tmp_path):
    seen = []
    runtime = _persistence(tmp_path, lambda action, detail, user: seen.append(action))

    def _explode(**_kwargs):
        raise RuntimeError("metrics file locked")

    runtime["FUNNEL_METRICS"].record_ingest = _explode
    runtime["INGESTION_PIPELINE"]._audit("kg_ingest", {"duplicate": False}, None)

    assert seen == ["kg_ingest"]


# ── chat_wiring telegram mirror ──────────────────────────────────────────────


def test_telegram_mirror_is_absent_when_the_integration_is_disabled():
    assert maybe_build_telegram_chat_mirror(enable_telegram=False, spawn=lambda *a, **k: None) is None


def test_telegram_mirror_spawns_a_named_broadcast(monkeypatch):
    import latticeai.integrations.telegram_bot as telegram_bot

    monkeypatch.setattr(
        telegram_bot,
        "broadcast_web_chat",
        lambda role, text: ("broadcast", role, text),
    )
    spawned = []
    mirror = maybe_build_telegram_chat_mirror(
        enable_telegram=True,
        spawn=lambda awaitable, name=None: spawned.append((awaitable, name)),
    )

    mirror("assistant", "안녕하세요", "web")

    assert spawned == [(("broadcast", "assistant", "안녕하세요"), "telegram_broadcast")]
