"""Session bootstrap runtime: session store and token helpers.

Extracted from ``app_factory._build`` as a composition seam. The session
token helpers stay closures over a single ``SessionStore`` so the factory
keeps one source of truth for token lifecycle. ``user_id_resolver`` is the
factory's ``user_id_for_email`` — injected so the store stays decoupled from
user persistence. Heavy imports stay inside the function so importing the
module has no side effects.
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

    def create_session(email: str) -> str:
        return session_store.create(user_id_resolver(email) or email, email=email)

    def get_session_email(token: str) -> Optional[str]:
        return session_store.get_email(token)

    def get_session_user_id(token: str) -> Optional[str]:
        return session_store.get_subject(token)

    def invalidate_session(token: str) -> None:
        session_store.invalidate(token)

    return {
        "_SESSION_TTL": ttl_seconds,
        "_session_store": session_store,
        "create_session": create_session,
        "get_session_email": get_session_email,
        "get_session_user_id": get_session_user_id,
        "invalidate_session": invalidate_session,
    }
