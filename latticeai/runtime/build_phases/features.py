"""Phases 9-10: platform features and the interaction routers.

The tail of the build order — everything that mounts routers on the finished
application. Split out of the single ``build_phases`` module in v11.3.0.

Every heavy import lives *inside* a phase, never at module scope.
"""

from __future__ import annotations

from latticeai.runtime.runtime_context import RuntimeContext


# ── phase 7: platform features ───────────────────────────────────────────────
def phase_platform_features(ctx: RuntimeContext) -> None:
    """Workspace platform, automation, review queue, command centre, proposals."""
    ctx.enter("platform_features")

    from latticeai.api.agents import create_agents_router
    from latticeai.api.automation_intelligence import (
        create_automation_intelligence_router,
    )
    from latticeai.api.change_proposals import create_change_proposals_router
    from latticeai.api.chronicle import create_chronicle_router
    from latticeai.api.command_center import create_command_center_router
    from latticeai.api.evidence_actions import create_evidence_actions_router
    from latticeai.api.funnel_metrics import create_funnel_metrics_router
    from latticeai.api.marketplace import create_marketplace_router
    from latticeai.api.plugins import create_plugins_router
    from latticeai.api.project_sessions import create_project_sessions_router
    from latticeai.api.realtime import create_realtime_router
    from latticeai.api.voice_capture import create_voice_capture_router
    from latticeai.api.workflow_designer import create_workflow_designer_router
    from latticeai.api.workspace import _workspace_scope_from_request
    from latticeai.core.project_sessions import ProjectSessionStore
    from latticeai.runtime.hooks_runtime import (
        bind_builtin_hook_runners,
        bind_trigger_hook_runner,
    )
    from latticeai.runtime.platform_runtime_wiring import (
        build_platform_automation_runtime,
    )
    from latticeai.runtime.router_registration import (
        register_platform_feature_routers,
    )
    from latticeai.services.change_proposals import ChangeProposalService
    from latticeai.services.chronicle import ChronicleService
    from latticeai.services.command_center import CommandCenterService
    from latticeai.services.evidence_actions import EvidenceActionService
    from latticeai.services.tool_dispatch import get_tool_permission
    from latticeai.services.voice_capture import VoiceCaptureService
    from latticeai.tools import resolve_workspace_path

    # v2 Agentic Workspace Platform: cross-system wiring.
    platform_automation_runtime = build_platform_automation_runtime(
        model_router=ctx.model_router,
        workspace_store=ctx.WORKSPACE_OS,
        workspace_service=ctx.WORKSPACE_SERVICE,
        plugin_registry=ctx.PLUGIN_REGISTRY,
        get_current_user=ctx.get_current_user,
        workspace_graph=ctx._workspace_graph,
        workspace_scope_from_request=_workspace_scope_from_request,
        get_tool_permission=get_tool_permission,
        hooks=ctx.HOOKS_REGISTRY,
        agent_registry=ctx.AGENT_REGISTRY,
        data_dir=ctx.DATA_DIR,
        append_audit_event=ctx.append_audit_event,
        memory_service=ctx.MEMORY_SERVICE,
        tz_name=getattr(ctx.CONFIG, "timezone", None),
    )
    ctx.adopt(
        platform_automation_runtime,
        "_llm_generate_sync",
        "PLATFORM",
        "_automation_runtime",
        "REVIEW_QUEUE",
        "TRIGGER_SERVICE",
        "AGENT_RUNTIME",
        "RUN_EXECUTOR",
    )
    bind_trigger_hook_runner(
        registry=ctx.HOOKS_REGISTRY, trigger_service=ctx.TRIGGER_SERVICE
    )
    ctx.app.state.run_executor = ctx.RUN_EXECUTOR
    ctx.app.state.run_reconciliation = ctx.RUN_EXECUTOR.reconcile_startup()
    ctx.TRIGGER_SERVICE.start()

    bind_builtin_hook_runners(
        registry=ctx.HOOKS_REGISTRY,
        append_audit_event=ctx.append_audit_event,
        get_tool_permission=get_tool_permission,
        classify_sensitive_message=ctx.classify_sensitive_message,
    )

    register_platform_feature_routers(
        ctx.app,
        create_plugins_router=create_plugins_router,
        plugin_registry=ctx.PLUGIN_REGISTRY,
        require_user=ctx.require_user,
        require_admin=ctx.require_admin,
        append_audit_event=ctx.append_audit_event,
        platform=ctx.PLATFORM,
        ui_file_response=ctx.ui_file_response,
        static_dir=ctx.STATIC_DIR,
        create_workflow_designer_router=create_workflow_designer_router,
        store=ctx.WORKSPACE_OS,
        get_current_user=ctx.get_current_user,
        workspace_graph=ctx._workspace_graph,
        hooks=ctx.HOOKS_REGISTRY,
        run_executor=ctx.RUN_EXECUTOR,
        trigger_service=ctx.TRIGGER_SERVICE,
        create_agents_router=create_agents_router,
        agent_runtime=ctx.AGENT_RUNTIME,
        create_marketplace_router=create_marketplace_router,
        template_catalog=ctx.TEMPLATE_CATALOG,
        create_realtime_router=create_realtime_router,
        realtime_bus=ctx.REALTIME_BUS,
    )

    ctx.app.include_router(
        create_automation_intelligence_router(
            service=ctx.AUTOMATION_INTELLIGENCE,
            store=ctx.WORKSPACE_OS,
            require_user=ctx.require_user,
            gate_read=ctx.PLATFORM.gate_read,
            gate_write=ctx.PLATFORM.gate_write,
            append_audit_event=ctx.append_audit_event,
            workspace_graph=ctx._workspace_graph,
            run_executor=ctx.RUN_EXECUTOR,
            review_queue=ctx.REVIEW_QUEUE,
        )
    )
    # UX funnel metrics (backlog #16): admin-only runtime counters.
    ctx.app.include_router(
        create_funnel_metrics_router(
            service=ctx.FUNNEL_METRICS,
            require_admin=ctx.require_admin,
        )
    )

    ctx.set(
        COMMAND_CENTER=CommandCenterService(
            conversation_store=ctx.CONVERSATIONS,
            knowledge_graph=ctx.KNOWLEDGE_GRAPH,
            store=ctx.WORKSPACE_OS,
            search_service=ctx.SEARCH_SERVICE,
            brain_intelligence=ctx.BRAIN_INTELLIGENCE,
            automation_intelligence=ctx.AUTOMATION_INTELLIGENCE,
            review_queue=ctx.REVIEW_QUEUE,
            enable_graph=ctx.ENABLE_GRAPH,
        )
    )
    ctx.app.include_router(
        create_command_center_router(
            service=ctx.COMMAND_CENTER,
            require_user=ctx.require_user,
            gate_read=ctx.PLATFORM.gate_read,
        )
    )

    # Brain Chronicle (v11.3.0): the bitemporal columns v11.1.0 started
    # writing, read back as a timeline. Pure reads over the Brain's own
    # storage — no schema of its own, nothing to write, nothing to migrate.
    ctx.set(
        CHRONICLE=ChronicleService(
            knowledge_graph=ctx.KNOWLEDGE_GRAPH,
            conversations=ctx.CONVERSATIONS,
            enable_graph=ctx.ENABLE_GRAPH,
        )
    )
    ctx.app.include_router(
        create_chronicle_router(
            service=ctx.CHRONICLE,
            require_user=ctx.require_user,
            gate_read=ctx.PLATFORM.gate_read,
        )
    )

    ctx.set(
        CHANGE_PROPOSALS=ChangeProposalService(
            review_queue=ctx.REVIEW_QUEUE,
            resolve_path=resolve_workspace_path,
            audit=ctx.append_audit_event,
        )
    )
    # Proposal-first mutations: the agent loop consults the governor so
    # additive creates run with minimal friction while changes and deletions
    # of existing files are staged for review instead of applied.
    ctx.CHAT_AGENT_RUNTIME.deps.change_governor = ctx.CHANGE_PROPOSALS
    ctx.app.include_router(
        create_change_proposals_router(
            service=ctx.CHANGE_PROPOSALS,
            require_user=ctx.require_user,
            gate_read=ctx.PLATFORM.gate_read,
            gate_write=ctx.PLATFORM.gate_write,
        )
    )

    # Evidence → action (v9.9.6): an answer's citations become ready-to-send,
    # evidence-scoped follow-ups. Deterministic composition only — execution
    # stays on the chat/file-generation path.
    ctx.set(
        EVIDENCE_ACTIONS_SERVICE=EvidenceActionService(
            node_reader=getattr(ctx.KNOWLEDGE_GRAPH, "get_node", None)
        )
    )
    ctx.app.include_router(
        create_evidence_actions_router(
            service=ctx.EVIDENCE_ACTIONS_SERVICE,
            require_user=ctx.require_user,
            allowed_workspaces_for=ctx._allowed_workspaces_for,
        )
    )

    # Voice memo capture (v9.9.7): the shortest path from a thought to the
    # Brain. Transcription is an optional local port — absent, the memo is
    # still stored and the response says it is not searchable. v11.1.0 shares
    # that one port with multi-modal ingestion, so a memo and a scanned
    # recording can never disagree about whether this machine can hear.
    ctx.set(
        VOICE_CAPTURE=VoiceCaptureService(
            pipeline=ctx.INGESTION_PIPELINE if ctx.ENABLE_GRAPH else None,
            transcriber=ctx.MULTIMODAL_PORTS.transcriber,
        )
    )
    ctx.app.include_router(
        create_voice_capture_router(
            service=ctx.VOICE_CAPTURE,
            require_user=ctx.require_user,
            gate_write=ctx.PLATFORM.gate_write,
            append_audit_event=ctx.append_audit_event,
        )
    )

    # Multi-turn project loop (v9.9.6): work spanning several runs keeps its
    # files, open TODOs, and last honest verification in one project session.
    ctx.set(PROJECT_SESSIONS=ProjectSessionStore(ctx.DATA_DIR / "project_sessions"))
    ctx.app.include_router(
        create_project_sessions_router(
            store=ctx.PROJECT_SESSIONS,
            require_user=ctx.require_user,
            gate_read=ctx.PLATFORM.gate_read,
            gate_write=ctx.PLATFORM.gate_write,
        )
    )


# ── phase 8: interaction routers ─────────────────────────────────────────────
def phase_interaction(ctx: RuntimeContext) -> None:
    """Model, chat, search, tools, hooks, memory and brain tail routers."""
    ctx.enter("interaction")

    from fastapi import HTTPException

    from latticeai.api.agent_registry import create_agent_registry_router
    from latticeai.api.brain_intelligence import create_brain_intelligence_router
    from latticeai.api.browser import create_browser_router
    from latticeai.api.chat import create_chat_router
    from latticeai.api.garden import create_garden_router
    from latticeai.api.health import create_health_router
    from latticeai.api.hooks import create_hooks_router
    from latticeai.api.memory import create_memory_router
    from latticeai.api.models import create_models_router
    from latticeai.api.network import create_network_router
    from latticeai.api.portability import create_portability_router
    from latticeai.api.review_queue import create_review_queue_router
    from latticeai.api.search import create_search_router
    from latticeai.api.setup import create_setup_router
    from latticeai.api.tools import create_tools_router
    from latticeai.core.model_compat import (
        list_cached_profiles as _list_compat_profiles,
    )
    from latticeai.runtime.chat_wiring import build_interaction_contexts
    from latticeai.runtime.model_wiring import register_model_runtime_routers
    from latticeai.runtime.platform_services_runtime import build_brain_network
    from latticeai.runtime.review_wiring import build_review_run_now_runner
    from latticeai.runtime.router_registration import (
        register_health_and_model_routers,
        register_interaction_routers,
        register_review_and_brain_tail_routers,
    )
    from latticeai.services.model_runtime import (
        CLOUD_VERIFY_TTL_SECONDS,
        ENGINE_MODEL_CATALOG,
        MODEL_ENGINE_ALIASES,
        download_hf_model,
        ensure_ollama_server,
        filter_lower_family_versions,
        local_binary,
        normalize_local_model_request,
        sse_event,
    )

    service = ctx.model_runtime_service
    ctx.set(
        model_runtime=register_model_runtime_routers(
            app=ctx.app,
            create_health_router=create_health_router,
            create_models_router=create_models_router,
            register_health_and_model_routers=register_health_and_model_routers,
            model_router=ctx.model_router,
            runtime_service=service,
            runtime_features=service.runtime_features,
            is_public_mode=ctx.IS_PUBLIC_MODE,
            engine_status=service.engine_status,
            get_current_user=ctx.get_current_user,
            require_auth=ctx.REQUIRE_AUTH,
            app_version=ctx.APP_VERSION,
            app_mode=ctx.APP_MODE,
            require_user=ctx.require_user,
            require_admin=ctx.require_admin,
            load_users=ctx.load_users,
            get_user_role=ctx.get_user_role,
            install_engine=service.install_engine,
            verify_cloud_models=service.verify_cloud_models,
            normalize_local_model_request=normalize_local_model_request,
            download_hf_model=download_hf_model,
            prepare_and_load_model=service.prepare_and_load_model,
            prepare_and_load_model_stream=service.prepare_and_load_model_stream,
            sse_event=sse_event,
            ensure_ollama_server=ensure_ollama_server,
            local_binary=local_binary,
            filter_lower_family_versions=filter_lower_family_versions,
            list_compat_profiles=_list_compat_profiles,
            set_user_api_key=ctx.set_user_api_key,
            engine_model_catalog=ENGINE_MODEL_CATALOG,
            model_engine_aliases=MODEL_ENGINE_ALIASES,
            cloud_verify_ttl_seconds=CLOUD_VERIFY_TTL_SECONDS,
            allow_local_models=ctx.ALLOW_LOCAL_MODELS,
        )
    )

    _tool_router_context, interaction_router_context = build_interaction_contexts(
        config=ctx.CONFIG,
        ingestion_pipeline=ctx.INGESTION_PIPELINE,
        data_dir=ctx.DATA_DIR,
        static_dir=ctx.STATIC_DIR,
        model_router=ctx.model_router,
        require_user=ctx.require_user,
        require_admin=ctx.require_admin,
        get_current_user=ctx.get_current_user,
        clear_history=ctx.clear_history,
        append_audit_event=ctx.append_audit_event,
        enforce_rate_limit=ctx.enforce_rate_limit,
        bytes_match_extension=ctx._bytes_match_extension,
        classify_sensitive_message=ctx.classify_sensitive_message,
        save_to_history=ctx.save_to_history,
        enable_graph=ctx.ENABLE_GRAPH,
        knowledge_graph=ctx.KNOWLEDGE_GRAPH,
        require_graph=ctx._require_graph,
        local_kg_watcher=ctx.LOCAL_KG_WATCHER,
        load_mcp_installs=ctx.load_mcp_installs,
        recommend_mcps=ctx.recommend_mcps,
        install_mcp=ctx.install_mcp,
        mcp_public_item=ctx.mcp_public_item,
        hooks=ctx.HOOKS_REGISTRY,
        workspace_service=ctx.WORKSPACE_SERVICE,
        chat_context=ctx.app_context,
        search_service=ctx.SEARCH_SERVICE,
        allowed_workspaces_for=ctx._allowed_workspaces_for,
        embedding_info=ctx._embedding_info,
        agent_registry=ctx.AGENT_REGISTRY,
        memory_service=ctx.MEMORY_SERVICE,
        platform=ctx.PLATFORM,
        active_model_getter=lambda: ctx.model_router.current_model_id or "",
        brain_intelligence=ctx.BRAIN_INTELLIGENCE,
    )
    register_interaction_routers(
        ctx.app,
        interaction_context=interaction_router_context,
        create_chat_router=create_chat_router,
        create_search_router=create_search_router,
        create_tools_router=create_tools_router,
        create_hooks_router=create_hooks_router,
        create_agent_registry_router=create_agent_registry_router,
        create_memory_router=create_memory_router,
        create_brain_intelligence_router=create_brain_intelligence_router,
    )

    register_review_and_brain_tail_routers(
        ctx.app,
        create_review_queue_router=create_review_queue_router,
        review_queue=ctx.REVIEW_QUEUE,
        require_user=ctx.require_user,
        gate_read=ctx.PLATFORM.gate_read,
        gate_write=ctx.PLATFORM.gate_write,
        run_review_item=build_review_run_now_runner(ctx.PLATFORM, HTTPException),
        append_audit_event=ctx.append_audit_event,
        # Approving a change_proposal from the Review Center applies the
        # staged content through the same service the agent governor uses.
        change_proposals=ctx.CHANGE_PROPOSALS,
        create_browser_router=create_browser_router,
        ingestion_pipeline=ctx.INGESTION_PIPELINE,
        workspace_service=ctx.WORKSPACE_SERVICE,
        create_portability_router=create_portability_router,
        kg_portability=ctx.KG_PORTABILITY,
        require_admin=ctx.require_admin,
        build_brain_network=build_brain_network,
        device_identity=ctx.DEVICE_IDENTITY,
        data_dir=ctx.DATA_DIR,
        create_network_router=create_network_router,
        create_garden_router=create_garden_router,
        gardener=ctx.gardener,
        create_setup_router=create_setup_router,
        model_router=ctx.model_router,
        knowledge_graph=ctx.KNOWLEDGE_GRAPH,
    )
