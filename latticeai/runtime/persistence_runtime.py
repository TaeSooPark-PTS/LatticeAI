"""Persistence/service assembly seams for app startup.

This module owns the durable local stores and services that sit between the
Brain runtime and API routers. Imports stay inside the function so importing
``latticeai.app_factory`` remains side-effect free.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def build_persistence_runtime(
    *,
    data_dir: Any,
    base_dir: Any,
    enable_graph: bool,
    knowledge_graph: Any,
    hooks_registry: Any,
    history_file: Any,
    conversations: Any,
    user_id_for_email: Callable[[Optional[str]], Optional[str]],
    audit: Callable[[str, Dict[str, Any], Optional[str]], None],
) -> Dict[str, Any]:
    """Construct workspace, plugin, memory, ingestion, and portability services."""

    import os
    from pathlib import Path

    from lattice_brain.graph.identity import DeviceIdentity
    from lattice_brain.ingestion import IngestionPipeline
    from lattice_brain.portability import KGPortabilityService
    from latticeai.core.agent_registry import AgentRegistry
    from latticeai.core.invitations import InvitationStore
    from latticeai.core.marketplace import TemplateCatalog
    from latticeai.core.plugins import PluginRegistry
    from latticeai.core.realtime import RealtimeBus
    from latticeai.core.workspace_os import WorkspaceOSStore
    from latticeai.services.automation_intelligence import AutomationIntelligenceService
    from latticeai.services.brain_intelligence import BrainIntelligenceService
    from latticeai.services.memory_service import MemoryService
    from latticeai.services.workspace_service import WorkspaceService

    realtime_bus = RealtimeBus()
    workspace_os = WorkspaceOSStore(data_dir, event_sink=realtime_bus)
    workspace_service = WorkspaceService(workspace_os, resolve_user_id=user_id_for_email)
    invitation_store = InvitationStore(data_dir / "invitations.json")

    plugins_dir = Path(os.getenv("LATTICEAI_PLUGINS_DIR") or (base_dir / "plugins"))
    plugin_registry = PluginRegistry(plugins_dir, store=workspace_os)
    template_catalog = TemplateCatalog()
    agent_registry = AgentRegistry(data_dir / "agent_registry.json")

    memory_service = MemoryService(
        store=workspace_os,
        data_dir=data_dir,
        knowledge_graph=knowledge_graph,
        enable_graph=enable_graph,
        history_file=history_file,
        conversation_store=conversations,
    )
    brain_intelligence = BrainIntelligenceService(
        knowledge_graph=knowledge_graph,
        memory_service=memory_service,
        enable_graph=enable_graph,
    )
    automation_intelligence = AutomationIntelligenceService(
        conversation_store=conversations,
        knowledge_graph=knowledge_graph,
        store=workspace_os,
        enable_graph=enable_graph,
    )
    ingestion_pipeline = IngestionPipeline(
        knowledge_graph,
        hooks=hooks_registry,
        enable_graph=enable_graph,
        audit=audit,
    )
    device_identity = DeviceIdentity(data_dir)
    kg_portability = KGPortabilityService(
        knowledge_graph=knowledge_graph,
        data_dir=data_dir,
        enable_graph=enable_graph,
        device_identity=device_identity,
    )

    return {
        "REALTIME_BUS": realtime_bus,
        "WORKSPACE_OS": workspace_os,
        "WORKSPACE_SERVICE": workspace_service,
        "INVITATION_STORE": invitation_store,
        "PLUGINS_DIR": plugins_dir,
        "PLUGIN_REGISTRY": plugin_registry,
        "TEMPLATE_CATALOG": template_catalog,
        "AGENT_REGISTRY": agent_registry,
        "MEMORY_SERVICE": memory_service,
        "BRAIN_INTELLIGENCE": brain_intelligence,
        "AUTOMATION_INTELLIGENCE": automation_intelligence,
        "INGESTION_PIPELINE": ingestion_pipeline,
        "DEVICE_IDENTITY": device_identity,
        "KG_PORTABILITY": kg_portability,
    }
