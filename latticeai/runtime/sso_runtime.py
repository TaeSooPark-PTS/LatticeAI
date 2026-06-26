"""SSO / OIDC runtime assembly extracted from legacy app factory closure.

Provides cache and discovery helper so that the giant _build in app_factory
stays smaller. Behavior and names preserved exactly for the compat namespace.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def build_sso_runtime(
    *,
    get_sso_settings: Callable[[], Dict[str, Any]],
    logging: Any,
) -> Dict[str, Any]:
    """Return SSO-related names for the legacy runtime namespace.

    Includes:
      SSO_* config (caller supplies from security_runtime)
      _sso_discovery_cache / _sso_discovery_cache_url / _sso_states
      async _get_sso_discovery()
    """
    _sso_discovery_cache: Optional[Dict] = None
    _sso_discovery_cache_url: str = ""
    _sso_states: Dict[str, float] = {}

    async def _get_sso_discovery() -> Optional[Dict]:
        nonlocal _sso_discovery_cache, _sso_discovery_cache_url
        settings = get_sso_settings()
        discovery_url = settings.get("discovery_url", "")
        if _sso_discovery_cache and _sso_discovery_cache_url == discovery_url:
            return _sso_discovery_cache
        if not discovery_url:
            return None
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient() as c:
                r = await c.get(discovery_url, timeout=10)
                r.raise_for_status()
                _sso_discovery_cache = r.json()
                _sso_discovery_cache_url = discovery_url
        except Exception as e:
            logging.warning("SSO discovery failed: %s", e)
            return None
        return _sso_discovery_cache

    return {
        "_sso_discovery_cache": _sso_discovery_cache,
        "_sso_discovery_cache_url": _sso_discovery_cache_url,
        "_sso_states": _sso_states,
        "_get_sso_discovery": _get_sso_discovery,
    }
