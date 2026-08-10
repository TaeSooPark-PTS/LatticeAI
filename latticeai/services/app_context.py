"""Application dependency context for router assembly.

``latticeai.app_factory.create_app`` builds one ``AppContext`` per app and
hands it to router factories, replacing the historical 25-30-kwarg closure
wiring. Every field defaults to ``None``-ish so tests can construct a context
carrying only the dependencies a router actually touches.

Fields are grouped by the consumer that motivated them; routers must treat the
context as read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class AppContext:
    # ── core configuration / paths ────────────────────────────────────────
    config: Any = None
    data_dir: Optional[Path] = None
    static_dir: Optional[Path] = None
    base_dir: Optional[Path] = None
    skills_dir: Optional[Path] = None

    # ── singletons ────────────────────────────────────────────────────────
    model_router: Any = None
    workspace_store: Any = None
    workspace_service: Any = None
    knowledge_graph: Any = None
    local_kg_watcher: Any = None
    chat_service: Any = None
    context_assembler: Any = None
    # Re-search loop (v9.9.6): conversation-scoped ledger of just-written
    # artifacts, so a follow-up turn sees them before indexing catches up.
    artifact_ledger: Any = None
    brain_memory: Any = None
    # Unified ingestion gateway (lattice_brain.ingestion.IngestionPipeline);
    # None when the knowledge graph is disabled.
    ingestion_pipeline: Any = None
    chat_agent_runtime: Any = None
    gardener: Any = None
    hooks: Any = None
    realtime_bus: Any = None
    capability_registry: Any = None
    # UX funnel metrics (latticeai.services.funnel_metrics); None in tests
    # that don't observe the funnel — every increment site is nil-safe.
    funnel_metrics: Any = None

    # ── auth / session callables ──────────────────────────────────────────
    require_user: Optional[Callable[..., str]] = None
    require_admin: Optional[Callable[..., tuple]] = None
    get_current_user: Optional[Callable[..., Optional[str]]] = None
    load_users: Optional[Callable[[], dict]] = None
    get_user_role: Optional[Callable[..., str]] = None
    enforce_rate_limit: Optional[Callable[..., None]] = None
    allowed_workspaces_for: Optional[Callable[..., Any]] = None

    # ── audit / history callables ─────────────────────────────────────────
    append_audit_event: Optional[Callable[..., None]] = None
    get_audit_log: Optional[Callable[[], list]] = None
    get_history: Optional[Callable[[], list]] = None
    get_history_user: Optional[Callable[..., dict]] = None
    save_to_history: Optional[Callable[..., None]] = None
    clear_history: Optional[Callable[..., dict]] = None
    clear_conversation: Optional[Callable[..., dict]] = None
    group_history_conversations: Optional[Callable[..., list]] = None
    get_conversation_messages: Optional[Callable[..., list]] = None
    conversation_title: Optional[Callable[..., str]] = None

    # ── knowledge graph access ────────────────────────────────────────────
    enable_graph: bool = False
    require_graph: Optional[Callable[[], None]] = None
    workspace_graph: Optional[Callable[[], Any]] = None
    graph_stats: Optional[Callable[[], dict]] = None

    # ── review center ─────────────────────────────────────────────────────
    # ``ReviewQueueService``, reached through a provider like
    # ``workspace_graph`` above: the queue is built in a later runtime phase
    # than this context, so a value captured here would be ``None`` forever.
    # Call it per request; ``None`` means no Review Center is wired (tests,
    # headless helpers) and the callers stage nothing rather than writing.
    review_queue: Optional[Callable[[], Any]] = None

    # ── workspace payload providers / skills ──────────────────────────────
    workspace_models: Optional[Callable[[], dict]] = None
    workspace_settings: Optional[Callable[[], dict]] = None
    scan_environment: Optional[Callable[[], Any]] = None
    local_sysinfo: Optional[Callable[..., Any]] = None
    get_recommendations: Optional[Callable[..., Any]] = None
    fetch_skills_marketplace: Optional[Callable[..., Any]] = None
    install_skill: Optional[Callable[..., Any]] = None
    remove_skill_directory: Optional[Callable[..., dict]] = None
    redact_secret_text: Optional[Callable[[str], str]] = None
    ui_file_response: Optional[Callable[..., Any]] = None

    # ── models ────────────────────────────────────────────────────────────
    public_model: str = ""
    local_model: str = ""

    # ── integrations ──────────────────────────────────────────────────────
    # Fired as on_chat_message(role, text, source) after a chat exchange is
    # persisted; ``None`` means no external chat mirror is registered. The
    # telegram bridge subscribes here only when ENABLE_TELEGRAM is truthy.
    on_chat_message: Optional[Callable[..., None]] = None

    def require(self, field: str) -> Any:
        """Return a dependency this router cannot operate without.

        Every field defaults to ``None`` so a test can build a context holding
        only what it exercises. A router that binds an absent dependency used
        to discover it as ``TypeError: 'NoneType' object is not callable``
        inside a request handler, far from the wiring mistake. Binding through
        ``require`` moves that to router construction with the field named.

        It is also what makes the binding statically honest: the declared type
        is ``Optional[...]``, so a type checker reports every call site as
        ``"None" not callable`` — 105 of them in the workspace router alone.
        ``require`` states the precondition once instead.
        """
        if field not in self.__dataclass_fields__:
            raise AttributeError(f"AppContext has no field {field!r}")
        value = getattr(self, field)
        if value is None:
            raise RuntimeError(
                f"AppContext.{field} is required by this router but was not provided"
            )
        return value
