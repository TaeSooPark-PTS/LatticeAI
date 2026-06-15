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
    brain_memory: Any = None
    chat_agent_runtime: Any = None
    gardener: Any = None
    hooks: Any = None
    realtime_bus: Any = None
    capability_registry: Any = None

    # ── auth / session callables ──────────────────────────────────────────
    require_user: Optional[Callable[..., str]] = None
    require_admin: Optional[Callable[..., tuple]] = None
    get_current_user: Optional[Callable[..., Optional[str]]] = None
    load_users: Optional[Callable[[], dict]] = None
    get_user_role: Optional[Callable[..., str]] = None
    enforce_rate_limit: Optional[Callable[..., None]] = None

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
