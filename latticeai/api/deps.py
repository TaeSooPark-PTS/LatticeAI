"""Shared API dependency type aliases.

Routers receive concrete callables from ``server_app`` at assembly time. Keeping
the aliases here avoids app imports inside router modules and gives future
router splits a single dependency vocabulary.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

RequireUser = Callable[[Any], str]
RequireAdmin = Callable[[Any], tuple[str, Dict]]
AuditAppender = Callable[..., None]

