from __future__ import annotations

from typing import Any, Callable, Dict, Optional

_DEFAULT_SESSION_TTL = 60 * 60 * 24


def build_session_runtime(
    *,
    user_id_resolver: Callable[[Optional[str]], Optional[str]],
    ttl_seconds: int = _DEFAULT_SESSION_TTL,
) -> Dict[str, Any]:

    from latticeai.core.sessions import SessionStore

    session_store = SessionStore(ttl_seconds=ttl_seconds)

    def get_session_email(token: str) -> Optional[str]:
        return session_store.get_email(token)

    return {
        "_session_store": session_store,
        "get_session_email": get_session_email,
    }
