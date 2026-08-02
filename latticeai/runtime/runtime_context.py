"""The assembly state the build phases share.

``app_factory._build`` was one 1,300-line function. That was not an accident:
its sections genuinely depend on each other, and several of them close over
names bound *further down* — ``save_to_history`` calls ``append_audit_event``,
which is built ~150 lines later. Inside a single function that works, because a
closure resolves a free variable when it is called, not when it is defined.
Any naive extraction breaks exactly there (10.3.0 hit this and documented it).

``RuntimeContext`` is what makes the extraction safe. It is a mutable object
that every phase writes into and reads from, so a closure created in phase 2
that calls ``ctx.append_audit_event`` still resolves it at call time — the same
late binding the single function had, now with the shared state named instead
of implicit.

Two properties keep it honest:

* **Attributes are declared, not defaulted.** Reading one before its phase has
  run raises ``AttributeError`` naming the attribute, rather than silently
  yielding ``None`` and failing somewhere unrelated.
* **The phase order is a contract.** ``tests/unit/test_runtime_context.py``
  fixes which phase produces which attribute, so reordering the phases in a way
  that breaks a dependency fails a test instead of production.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set


class RuntimeContext:
    """Mutable state carried across the ordered application build phases."""

    # ── phase 1: platform ────────────────────────────────────────────────
    mx: Any

    # ── phase 2: configuration and paths ─────────────────────────────────
    config_arg: Any
    config_runtime: Any
    security_runtime: Any
    CONFIG: Any
    APP_VERSION: str
    APP_MODE: str
    IS_PUBLIC_MODE: bool
    DEFAULT_HOST: str
    DEFAULT_PORT: int
    ENABLE_TELEGRAM: bool
    ENABLE_GRAPH: bool
    AUTOLOAD_MODELS: bool
    MODEL_IDLE_UNLOAD_SECONDS: Any
    ALLOW_MODEL_DOWNLOADS: bool
    MODEL_DOWNLOAD_TIMEOUT: Any
    ALLOW_LOCAL_MODELS: bool
    REQUIRE_AUTH: bool
    ALLOW_PLAINTEXT_API_KEYS: bool
    CORS_ALLOW_NETWORK: bool
    CORS_EXTRA_ORIGINS: Any
    PUBLIC_MODEL: str
    LOCAL_MODEL: Any
    LOCAL_DRAFT_MODEL: Any
    OPEN_REGISTRATION: bool
    SSO_DISCOVERY_URL: Any
    SSO_CLIENT_ID: Any
    SSO_CLIENT_SECRET: Any
    SSO_REDIRECT_URI: Any
    SSO_PROVIDER_NAME: Any
    INVITE_CODE: Any
    INVITE_COOKIE_SECRET: Any
    INVITE_GATE_ENABLED: bool
    SECURE_COOKIES: bool
    BASE_DIR: Any
    DATA_DIR: Any
    STATIC_DIR: Any
    USERS_FILE: Any
    HISTORY_FILE: Any
    VPC_FILE: Any
    AUDIT_FILE: Any
    SSO_FILE: Any
    keyring: Any

    # ── phase 3: identity, audit, history ────────────────────────────────
    load_mcp_installs: Any
    mcp_public_item: Any
    recommend_mcps: Any
    install_mcp: Any
    _SESSION_TTL: Any
    _session_store: Any
    create_session: Any
    get_session_email: Any
    invalidate_session: Any
    load_users: Any
    save_users: Any
    user_id_for_email: Any
    verify_and_migrate_password: Any
    redact_secret_text: Any
    get_audit_log: Any
    append_audit_event: Any
    classify_sensitive_message: Any
    build_sensitivity_report: Any
    build_admin_audit_report: Any
    get_user_role: Any
    _extract_bearer_token: Any
    get_current_user: Any
    require_user: Any
    require_admin: Any
    public_user: Any
    _RATE_LIMIT_ENABLED: bool
    enforce_rate_limit: Any
    _check_rate_limit: Any
    _client_ip: Any
    _bytes_match_extension: Any
    _host_is_loopback: Any
    get_history_user: Any
    get_user_api_key: Any
    set_user_api_key: Any
    get_sso_settings: Any
    public_sso_config: Any
    save_sso_config: Any
    _get_sso_discovery: Any
    _sso_states: Any
    _sso_env_defaults: Any
    load_vpc_config: Any
    save_vpc_config: Any

    # ── phase 4: brain, persistence ──────────────────────────────────────
    EMBEDDING_PROFILE: Any
    EMBEDDER: Any
    STORAGE_ENGINE: Any
    brain_runtime: Any
    KNOWLEDGE_GRAPH: Any
    CONVERSATIONS: Any
    save_to_history: Any
    HOOKS_REGISTRY: Any
    LOCAL_KG_WATCHER: Any
    REALTIME_BUS: Any
    WORKSPACE_OS: Any
    WORKSPACE_SERVICE: Any
    INVITATION_STORE: Any
    PLUGIN_REGISTRY: Any
    TEMPLATE_CATALOG: Any
    AGENT_REGISTRY: Any
    MEMORY_SERVICE: Any
    BRAIN_INTELLIGENCE: Any
    AUTOMATION_INTELLIGENCE: Any
    INGESTION_PIPELINE: Any
    DEVICE_IDENTITY: Any
    KG_PORTABILITY: Any
    FUNNEL_METRICS: Any
    get_history: Any
    conversation_title: Any
    group_history_conversations: Any
    get_conversation_messages: Any
    clear_history: Any
    clear_conversation: Any
    _history_allowed_workspaces_for: Any
    _history_include_legacy_global: Any
    _require_graph: Any
    _workspace_graph: Any

    # ── phase 5: domain services ─────────────────────────────────────────
    model_router: Any
    gardener: Any
    CHAT_SERVICE: Any
    SEARCH_SERVICE: Any
    BRAIN_MEMORY: Any
    CONTEXT_ASSEMBLER: Any
    ARTIFACT_LEDGER: Any
    _scoped_hybrid_search: Any
    CHAT_AGENT_RUNTIME: Any
    on_chat_message: Any
    _recent_chat_context: Any
    _workspace_settings_payload: Any
    _workspace_models_payload: Any
    _embedding_info: Any
    _allowed_workspaces_for: Any
    app_context: Any

    # ── phase 6: web app and foundation routers ──────────────────────────
    _spawn: Any
    lifespan: Any
    app: Any
    model_runtime_service: Any
    STATIC_ROUTES: Any
    ui_file_response: Any
    local_sysinfo: Any
    invite_authorized: Any
    auth_router: Any
    admin_router: Any
    invitations_router: Any
    security_router: Any
    _graph_stats_safe: Any
    _product_hardening_status: Any
    _security_audit_events_safe: Any
    _security_list_uploaded_files: Any

    # ── phase 7: platform features ───────────────────────────────────────
    _llm_generate_sync: Any
    PLATFORM: Any
    _automation_runtime: Any
    REVIEW_QUEUE: Any
    TRIGGER_SERVICE: Any
    AGENT_RUNTIME: Any
    RUN_EXECUTOR: Any
    COMMAND_CENTER: Any
    CHANGE_PROPOSALS: Any
    EVIDENCE_ACTIONS_SERVICE: Any
    VOICE_CAPTURE: Any
    PROJECT_SESSIONS: Any

    # ── phase 8: interaction routers ─────────────────────────────────────
    model_runtime: Any

    # ── legacy compatibility surface (phase 9 reads these) ───────────────
    legacy: Dict[str, Any]

    def __init__(self, config: Any = None) -> None:
        self.config_arg = config
        self.legacy = {}
        self._produced: Dict[str, str] = {}
        self._phase: str = "<none>"

    # ── phase bookkeeping ────────────────────────────────────────────────
    def enter(self, phase: str) -> None:
        """Mark which phase subsequent ``set`` calls belong to."""
        self._phase = phase

    def set(self, **values: Any) -> None:
        """Publish values produced by the current phase.

        Recording the producing phase is what lets the ordering test assert a
        contract instead of a comment.
        """
        for name, value in values.items():
            setattr(self, name, value)
            self._produced.setdefault(name, self._phase)

    def adopt(self, stage: Any, *names: str) -> None:
        """Publish selected keys of a runtime-stage mapping onto the context."""
        self.set(**{name: stage[name] for name in names})

    def require(self, name: str) -> Any:
        """Read an attribute, failing with the phase contract if it is unset.

        The bare ``AttributeError`` is already specific; this adds which phase
        was supposed to have produced the value, which is the actual question
        when a phase gets reordered.
        """
        try:
            return getattr(self, name)
        except AttributeError:
            producer = self._produced.get(name)
            hint = f" (produced by phase {producer!r})" if producer else ""
            raise RuntimeError(
                f"RuntimeContext.{name} was read before it was built{hint}"
            ) from None

    @property
    def produced_by(self) -> Dict[str, str]:
        """Attribute name → the phase that published it, for the order test."""
        return dict(self._produced)

    def names(self) -> Set[str]:
        return set(self._produced)

    def phases_run(self) -> List[str]:
        seen: List[str] = []
        for phase in self._produced.values():
            if phase not in seen:
                seen.append(phase)
        return seen

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RuntimeContext phase={self._phase!r} names={len(self._produced)}>"


__all__ = ["RuntimeContext"]
