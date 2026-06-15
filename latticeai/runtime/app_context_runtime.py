"""Application dependency context assembly for app startup."""

from __future__ import annotations

from typing import Any


def build_app_context(**deps: Any) -> Any:
    """Construct the typed dependency context consumed by API routers."""

    from latticeai.services.app_context import AppContext

    return AppContext(**deps)
