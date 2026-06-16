"""Review, browser, portability, network, garden, and setup tail wiring."""

from __future__ import annotations

from typing import Any


def register_tail_runtime_routers(
    *,
    app: Any,
    create_review_queue_router: Any,
    register_review_and_brain_tail_routers: Any,
    build_brain_network: Any,
    **kwargs: Any,
) -> Any:
    return register_review_and_brain_tail_routers(
        app,
        create_review_queue_router=create_review_queue_router,
        build_brain_network=build_brain_network,
        **kwargs,
    )
