"""Typed router assembly contexts for app-factory decomposition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolRouterContext:
    """Runtime dependencies for direct tool, upload, MCP, and KG routes."""

    config: Any
    ingestion_pipeline: Any
    data_dir: Path
    static_dir: Path
    model_router: Any
    require_user: Any
    require_admin: Any
    get_current_user: Any
    clear_history: Any
    append_audit_event: Any
    enforce_rate_limit: Any
    bytes_match_extension: Any
    classify_sensitive_message: Any
    save_to_history: Any
    enable_graph: bool
    knowledge_graph: Any
    require_graph: Any
    local_kg_watcher: Any
    load_mcp_installs: Any
    recommend_mcps: Any
    install_mcp: Any
    mcp_public_item: Any
    hooks: Any = None
    # Resolves a caller email to their allowed workspace set (None = no scoping,
    # i.e. single-user / no-auth mode). Threaded to the knowledge-graph router so
    # its read endpoints enforce the same workspace boundary as /api/search.
    allowed_workspaces_for: Any = None


@dataclass(frozen=True)
class InteractionRouterContext:
    """Runtime dependencies for chat/search/tools/hooks/memory route assembly."""

    chat_context: Any
    search_service: Any
    allowed_workspaces_for: Any
    require_user: Any
    embedding_info: Any
    tool_context: ToolRouterContext
    hooks: Any
    agent_registry: Any
    memory_service: Any
    platform: Any
