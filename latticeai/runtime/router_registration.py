"""Router registration helpers for app-factory decomposition.

The first router extraction step keeps router construction at the existing
call sites and centralizes only the registration operation. This preserves the
exact include order while creating a narrow seam for the later
``register_routers(app, deps)`` extraction.
"""

from __future__ import annotations

from typing import Any


def register_router(app: Any, router: Any) -> Any:
    """Include one router and return it for optional caller-side bookkeeping."""

    app.include_router(router)
    return router


def register_routers(app: Any, *routers: Any) -> tuple[Any, ...]:
    """Include routers in the given order and return them unchanged."""

    for router in routers:
        register_router(app, router)
    return routers


def register_foundation_routers(
    app: Any,
    *,
    static_router: Any,
    auth_router: Any,
    admin_router: Any,
    invitations_router: Any,
    security_router: Any,
    workspace_router: Any,
) -> tuple[Any, ...]:
    """Register early static/auth/admin/security/workspace routers in order."""

    return register_routers(
        app,
        static_router,
        auth_router,
        admin_router,
        invitations_router,
        security_router,
        workspace_router,
    )


def register_platform_feature_routers(
    app: Any,
    *,
    create_plugins_router: Any,
    plugin_registry: Any,
    require_user: Any,
    require_admin: Any,
    append_audit_event: Any,
    platform: Any,
    ui_file_response: Any,
    static_dir: Any,
    create_workflow_designer_router: Any,
    store: Any,
    get_current_user: Any,
    workspace_graph: Any,
    hooks: Any,
    run_executor: Any,
    trigger_service: Any,
    create_agents_router: Any,
    agent_runtime: Any,
    create_marketplace_router: Any,
    template_catalog: Any,
    create_realtime_router: Any,
    realtime_bus: Any,
) -> tuple[Any, ...]:
    """Register plugin/workflow/agent/marketplace/realtime routes in order."""

    return register_routers(
        app,
        create_plugins_router(
            registry=plugin_registry,
            require_user=require_user,
            require_admin=require_admin,
            append_audit_event=append_audit_event,
            register_skill=platform.register_plugin_skill,
            plugin_runners_factory=lambda: platform.plugin_capability_runners(None, None),
            ui_file_response=ui_file_response,
            static_dir=static_dir,
        ),
        create_workflow_designer_router(
            store=store,
            require_user=require_user,
            get_current_user=get_current_user,
            gate_read=platform.gate_read,
            gate_write=platform.gate_write,
            workspace_graph=workspace_graph,
            build_runners=platform.build_workflow_runners,
            append_audit_event=append_audit_event,
            ui_file_response=ui_file_response,
            static_dir=static_dir,
            hooks=hooks,
            run_executor=run_executor,
            trigger_service=trigger_service,
        ),
        create_agents_router(
            store=store,
            orchestrator_factory=platform.build_orchestrator,
            require_user=require_user,
            get_current_user=get_current_user,
            gate_read=platform.gate_read,
            gate_write=platform.gate_write,
            workspace_graph=workspace_graph,
            append_audit_event=append_audit_event,
            ui_file_response=ui_file_response,
            static_dir=static_dir,
            agent_runtime=agent_runtime,
            run_executor=run_executor,
        ),
        create_marketplace_router(
            store=store,
            catalog=template_catalog,
            require_user=require_user,
            gate_read=platform.gate_read,
            gate_write=platform.gate_write,
            workspace_graph=workspace_graph,
        ),
        create_realtime_router(
            bus=realtime_bus,
            require_user=require_user,
            get_current_user=get_current_user,
            allowed_scopes=platform.allowed_scopes,
            ui_file_response=ui_file_response,
            static_dir=static_dir,
        ),
    )


def register_health_and_model_routers(
    app: Any,
    *,
    create_health_router: Any,
    model_service: Any,
    engine_status: Any,
    get_current_user: Any,
    require_auth: bool,
    app_version: str,
    app_mode: str,
    create_models_router: Any,
    model_router: Any,
    require_user: Any,
    load_users: Any,
    get_user_role: Any,
    install_engine: Any,
    verify_cloud_models: Any,
    normalize_local_model_request: Any,
    download_hf_model: Any,
    prepare_and_load_model: Any,
    prepare_and_load_model_stream: Any,
    sse_event: Any,
    ensure_ollama_server: Any,
    local_binary: Any,
    filter_lower_family_versions: Any,
    list_compat_profiles: Any,
    set_user_api_key: Any,
    engine_model_catalog: Any,
    model_engine_aliases: Any,
    cloud_verify_ttl_seconds: int,
    is_public_mode: bool,
    allow_local_models: bool,
) -> tuple[Any, ...]:
    """Register health and model management routes in legacy order."""

    return register_routers(
        app,
        create_health_router(
            model_service=model_service,
            engine_status=engine_status,
            get_current_user=get_current_user,
            require_auth=require_auth,
            app_version=app_version,
            app_mode=app_mode,
        ),
        create_models_router(
            model_router=model_router,
            require_user=require_user,
            get_current_user=get_current_user,
            load_users=load_users,
            get_user_role=get_user_role,
            install_engine=install_engine,
            verify_cloud_models=verify_cloud_models,
            normalize_local_model_request=normalize_local_model_request,
            download_hf_model=download_hf_model,
            prepare_and_load_model=prepare_and_load_model,
            prepare_and_load_model_stream=prepare_and_load_model_stream,
            sse_event=sse_event,
            ensure_ollama_server=ensure_ollama_server,
            local_binary=local_binary,
            engine_status=engine_status,
            filter_lower_family_versions=filter_lower_family_versions,
            list_compat_profiles=list_compat_profiles,
            set_user_api_key=set_user_api_key,
            engine_model_catalog=engine_model_catalog,
            model_engine_aliases=model_engine_aliases,
            cloud_verify_ttl_seconds=cloud_verify_ttl_seconds,
            is_public_mode=is_public_mode,
            allow_local_models=allow_local_models,
            require_auth=require_auth,
        ),
    )


def register_interaction_routers(
    app: Any,
    *,
    create_chat_router: Any,
    context: Any,
    create_search_router: Any,
    search_service: Any,
    allowed_workspaces_for: Any,
    require_user: Any,
    embedding_info: Any,
    create_tools_router: Any,
    ingestion_pipeline: Any,
    config: Any,
    data_dir: Any,
    static_dir: Any,
    model_router: Any,
    require_admin: Any,
    get_current_user: Any,
    clear_history: Any,
    append_audit_event: Any,
    enforce_rate_limit: Any,
    bytes_match_extension: Any,
    classify_sensitive_message: Any,
    save_to_history: Any,
    enable_graph: bool,
    knowledge_graph: Any,
    require_graph: Any,
    local_kg_watcher: Any,
    load_mcp_installs: Any,
    recommend_mcps: Any,
    install_mcp: Any,
    mcp_public_item: Any,
    hooks: Any,
    create_hooks_router: Any,
    create_agent_registry_router: Any,
    agent_registry: Any,
    create_memory_router: Any,
    memory_service: Any,
    platform: Any,
) -> tuple[Any, ...]:
    """Register chat/search/tools/hooks/registry/memory routes in order."""

    return register_routers(
        app,
        create_chat_router(context),
        create_search_router(
            service=search_service,
            allowed_workspaces_for=allowed_workspaces_for,
            require_user=require_user,
            embedding_info=embedding_info,
        ),
        create_tools_router(
            ingestion_pipeline=ingestion_pipeline,
            config=config,
            data_dir=data_dir,
            static_dir=static_dir,
            model_router=model_router,
            require_user=require_user,
            require_admin=require_admin,
            get_current_user=get_current_user,
            clear_history=clear_history,
            append_audit_event=append_audit_event,
            enforce_rate_limit=enforce_rate_limit,
            bytes_match_extension=bytes_match_extension,
            classify_sensitive_message=classify_sensitive_message,
            save_to_history=save_to_history,
            enable_graph=enable_graph,
            knowledge_graph=knowledge_graph,
            require_graph=require_graph,
            local_kg_watcher=local_kg_watcher,
            load_mcp_installs=load_mcp_installs,
            recommend_mcps=recommend_mcps,
            install_mcp=install_mcp,
            mcp_public_item=mcp_public_item,
            hooks=hooks,
        ),
        create_hooks_router(
            registry=hooks,
            require_user=require_user,
            append_audit_event=append_audit_event,
        ),
        create_agent_registry_router(
            registry=agent_registry,
            require_user=require_user,
            append_audit_event=append_audit_event,
        ),
        create_memory_router(
            service=memory_service,
            require_user=require_user,
            get_current_user=get_current_user,
            gate_read=platform.gate_read,
            gate_write=platform.gate_write,
            append_audit_event=append_audit_event,
        ),
    )


def register_review_and_brain_tail_routers(
    app: Any,
    *,
    create_review_queue_router: Any,
    review_queue: Any,
    require_user: Any,
    gate_read: Any,
    gate_write: Any,
    run_review_item: Any,
    append_audit_event: Any,
    create_browser_router: Any,
    ingestion_pipeline: Any,
    create_portability_router: Any,
    kg_portability: Any,
    require_admin: Any,
    build_brain_network: Any,
    device_identity: Any,
    data_dir: Any,
    create_network_router: Any,
    create_garden_router: Any,
    gardener: Any,
    create_setup_router: Any,
    model_router: Any,
) -> Any:
    """Register the final review/browser/brain tail routes in legacy order."""

    register_routers(
        app,
        create_review_queue_router(
            service=review_queue,
            require_user=require_user,
            gate_read=gate_read,
            gate_write=gate_write,
            run_review_item=run_review_item,
            append_audit_event=append_audit_event,
        ),
        create_browser_router(
            pipeline=ingestion_pipeline,
            require_user=require_user,
        ),
        create_portability_router(
            service=kg_portability,
            require_user=require_user,
            require_admin=require_admin,
        ),
    )
    brain_network = build_brain_network(
        identity=device_identity,
        portability=kg_portability,
        data_dir=data_dir,
    )
    register_routers(
        app,
        create_network_router(
            network=brain_network,
            identity=device_identity,
            require_user=require_user,
        ),
        create_garden_router(gardener=gardener, require_user=require_user),
        create_setup_router(model_router=model_router, require_user=require_user),
    )
    return brain_network
