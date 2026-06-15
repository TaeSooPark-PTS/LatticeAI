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
