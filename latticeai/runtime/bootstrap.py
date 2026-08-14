"""Session bootstrap runtime: the token read the seam gate performs.

``lattice-auth`` owns the durable session: it issues the token, it persists it,
it invalidates it. The worker only ever *reads* one — ``require_user`` resolves
a bearer token or cookie to an email so a seam call can be attributed and rate
limited — so this seam is the store plus that single lookup.

``user_id_resolver`` is the factory's ``user_id_for_email``, injected so the
store stays decoupled from user persistence. Heavy imports stay inside the
function so importing the module has no side effects.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

_DEFAULT_SESSION_TTL = 60 * 60 * 24


def build_session_runtime(
    *,
    user_id_resolver: Callable[[Optional[str]], Optional[str]],
    ttl_seconds: int = _DEFAULT_SESSION_TTL,
) -> Dict[str, Any]:
    """Construct the session store and its token helper closures."""

    from latticeai.core.sessions import SessionStore

    session_store = SessionStore(ttl_seconds=ttl_seconds)

    def get_session_email(token: str) -> Optional[str]:
        return session_store.get_email(token)

    return {
        "_session_store": session_store,
        "get_session_email": get_session_email,
    }
