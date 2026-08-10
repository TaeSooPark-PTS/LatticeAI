"""Persistence/service assembly seams for app startup.

This module owns the durable local stores and services that sit between the
Brain runtime and API routers. Imports stay inside the function so importing
``latticeai.app_factory`` remains side-effect free.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from latticeai.core.quiet import quiet


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
    from latticeai.services.funnel_metrics import FunnelMetricsService
    from latticeai.services.memory_service import MemoryService
    from latticeai.services.multimodal_ports import (
        build_multimodal_ports,
        multimodal_enabled,
    )
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
    # UX funnel metrics (backlog #16): cheap JSON counters under the data dir.
    # The ingestion pipeline's audit seam is wrapped so every successful
    # ingest bumps the funnel (and starts the TTFV clock) without touching
    # lattice_brain internals.
    funnel_metrics = FunnelMetricsService(Path(data_dir) / "funnel_metrics.json")

    def _funnel_audit(action, detail, user):
        """Best-effort sinks for a landed ingest, then the real audit.

        Both sinks are isolated separately: one failing must not cost the
        other, and neither may cost the ingest that is already persisted.
        """
        if action == "kg_ingest":
            try:
                funnel_metrics.record_ingest(
                    duplicate=bool((detail or {}).get("duplicate"))
                )
            except Exception:  # noqa: BLE001 — metrics must never break ingestion
                quiet()
            # v11.1.0: the same seam drives synthesis. The pipeline audits an
            # ingest only after the graph write landed, so ``status: "ok"`` is
            # a fact about this call site rather than a guess; ``duplicate``
            # travels in the detail and is what keeps a re-import from pushing
            # the Brain toward a pass it has nothing new to say in.
            try:
                brain_intelligence.note_ingest(
                    {"status": "ok", **(detail or {})}, user_email=user
                )
            except Exception:  # noqa: BLE001 — synthesis must never break ingestion
                quiet()
        audit(action, detail, user)

    # Multi-modal capture (v11.1.0): off unless the user turned it on, and
    # even then only as capable as the models actually present. With nothing
    # configured this resolves to an empty bundle without importing or loading
    # anything.
    multimodal_ports = build_multimodal_ports()
    ingestion_pipeline = IngestionPipeline(
        knowledge_graph,
        hooks=hooks_registry,
        enable_graph=enable_graph,
        audit=_funnel_audit,
        allow_multimodal=multimodal_enabled(),
        multimodal=multimodal_ports,
    )
    # The local folder scanner reads images through the same ports, so a
    # caption in the Evidence panel and a caption on a scanned screenshot come
    # from one model or from none. A disabled/absent graph has nothing to
    # attach them to, and a store that refuses new attributes is not a reason
    # to fail startup.
    try:
        knowledge_graph.multimodal_ports = multimodal_ports
    except AttributeError:
        quiet()
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
        "MULTIMODAL_PORTS": multimodal_ports,
        "DEVICE_IDENTITY": device_identity,
        "KG_PORTABILITY": kg_portability,
        "FUNNEL_METRICS": funnel_metrics,
    }
