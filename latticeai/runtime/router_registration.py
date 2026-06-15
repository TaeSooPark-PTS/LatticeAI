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
