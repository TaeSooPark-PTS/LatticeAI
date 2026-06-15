"""v6 app-factory decomposition — early assembly order baseline.

Freezes mount/static/auth/session registration order before step-3
construct/mount reorder. Step 3 must preserve this prefix exactly; compare
against this snapshot before merging any router reorder.
"""

from __future__ import annotations

import importlib
from typing import Iterable, List, Tuple

import pytest

RouteEntry = Tuple[str, str, str]

# Captured from feat/v6-app-factory-decomposition step-2 (pre reorder).
EARLY_MOUNT_STATIC_AUTH_SESSION_ORDER: List[RouteEntry] = [
    ("mount", "/static", "static"),
    ("mount", "/icons", "icons"),
    ("route", "/", "GET"),
    ("route", "/account", "GET"),
    ("route", "/manifest.json", "GET"),
    ("route", "/favicon.ico", "GET"),
    ("route", "/sw.js", "GET"),
    ("route", "/chat", "GET"),
    ("route", "/app", "GET"),
    ("route", "/admin", "GET"),
    ("route", "/status", "GET"),
    ("route", "/local/sysinfo", "GET"),
    ("route", "/register", "POST"),
    ("route", "/login", "POST"),
    ("route", "/auth/sso/config", "GET"),
    ("route", "/auth/sso/login", "GET"),
    ("route", "/auth/sso/callback", "GET"),
    ("route", "/logout", "POST"),
    ("route", "/account/change-password", "POST"),
    ("route", "/account/profile", "PATCH"),
    ("route", "/account/profile", "GET"),
]


@pytest.fixture(scope="module")
def app():
    return importlib.import_module("server").app


def _iter_route_entries(routes: Iterable, *, prefix: str = "") -> List[RouteEntry]:
    entries: List[RouteEntry] = []
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            entries.extend(_iter_route_entries(getattr(original_router, "routes", []), prefix=prefix))
            continue
        kind = type(route).__name__
        path = getattr(route, "path", "")
        full_path = f"{prefix}{path}".replace("//", "/") if prefix else path
        if kind == "Mount":
            entries.append(("mount", full_path, getattr(route, "name", "") or ""))
            continue
        if getattr(route, "methods", None) is not None:
            methods = sorted(
                method
                for method in (getattr(route, "methods", None) or set())
                if method not in {"HEAD", "OPTIONS"}
            )
            entries.append(("route", full_path, ",".join(methods)))
            continue
        if hasattr(route, "routes"):
            entries.extend(_iter_route_entries(route.routes, prefix=full_path))
    return entries


def _early_assembly_prefix(entries: List[RouteEntry]) -> List[RouteEntry]:
    start = next(
        (index for index, entry in enumerate(entries) if entry == ("mount", "/static", "static")),
        None,
    )
    assert start is not None, "missing /static mount — assembly prefix drifted"
    prefix = entries[start : start + len(EARLY_MOUNT_STATIC_AUTH_SESSION_ORDER)]
    return prefix


def test_early_mount_static_auth_session_order_is_frozen(app):
    entries = _iter_route_entries(app.routes)
    current = _early_assembly_prefix(entries)
    assert current == EARLY_MOUNT_STATIC_AUTH_SESSION_ORDER, (
        "early assembly order changed before step-3 reorder; "
        f"expected {EARLY_MOUNT_STATIC_AUTH_SESSION_ORDER}, got {current}"
    )
