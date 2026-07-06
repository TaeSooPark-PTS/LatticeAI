"""SSO / OIDC config + discovery seam extracted from the app factory.

Owns the whole SSO surface that used to be inline in ``app_factory._build``:
the env-default resolution, the ``sso_config.json`` load/save, the public
(secret-stripped) view, and the OIDC discovery document cache.

Unlike the previous split (config inline in the factory + a separate discovery
cache in ``sso_runtime``), the discovery cache now lives in the *same* closure
as ``save_sso_config``, so saving a new SSO config actually invalidates the
cached discovery document. The old inline ``save_sso_config`` reassigned a
factory-local cache variable that ``_get_sso_discovery`` never read, so the
invalidation was a silent no-op — this consolidation fixes that.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def build_sso_config_runtime(
    *,
    sso_file: Path,
    discovery_url: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    provider_name: str,
    logging: Any,
) -> Dict[str, Any]:
    """Return the SSO config helpers + discovery accessor as a name → value dict."""

    # Single shared discovery cache: save_sso_config and _get_sso_discovery both
    # close over it, so a config change is observed on the next discovery fetch.
    _discovery_cache: Dict[str, Any] = {"data": None, "url": ""}
    _sso_states: Dict[str, float] = {}

    def _sso_env_defaults() -> Dict[str, object]:
        return {
            "enabled": bool(discovery_url and client_id and client_secret),
            "provider_name": provider_name,
            "discovery_url": discovery_url,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "scopes": "openid email profile",
        }

    def load_sso_config() -> Dict[str, object]:
        config = _sso_env_defaults()
        if sso_file.exists():
            try:
                data = json.loads(sso_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    config.update({k: v for k, v in data.items() if v is not None})
            except Exception as e:
                logging.warning("load_sso_config failed (using env/defaults): %s", e)
        config["provider_name"] = str(config.get("provider_name") or "SSO")
        config["discovery_url"] = str(config.get("discovery_url") or "")
        config["client_id"] = str(config.get("client_id") or "")
        config["client_secret"] = str(config.get("client_secret") or "")
        config["redirect_uri"] = str(config.get("redirect_uri") or redirect_uri)
        config["scopes"] = str(config.get("scopes") or "openid email profile")
        config["enabled"] = bool(config.get("enabled")) and bool(
            config["discovery_url"] and config["client_id"] and config["client_secret"]
        )
        return config

    def get_sso_settings() -> Dict[str, object]:
        return load_sso_config()

    def public_sso_config(config: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        cfg = config or get_sso_settings()
        return {
            "enabled": bool(cfg.get("enabled")),
            "provider_name": cfg.get("provider_name") or "",
            "discovery_url": cfg.get("discovery_url") or "",
            "client_id": cfg.get("client_id") or "",
            "redirect_uri": cfg.get("redirect_uri") or redirect_uri,
            "scopes": cfg.get("scopes") or "openid email profile",
            "secret_configured": bool(cfg.get("client_secret")),
        }

    def save_sso_config(update: Dict[str, object]) -> Dict[str, object]:
        current = load_sso_config()
        if update.get("client_secret") == "":
            update.pop("client_secret", None)
        current.update({k: v for k, v in update.items() if v is not None})
        current["enabled"] = bool(current.get("enabled")) and bool(
            current.get("discovery_url") and current.get("client_id") and current.get("client_secret")
        )
        sso_file.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        _discovery_cache["data"] = None
        _discovery_cache["url"] = ""
        return current

    async def _get_sso_discovery() -> Optional[Dict]:
        settings = get_sso_settings()
        url = settings.get("discovery_url", "")
        if _discovery_cache["data"] and _discovery_cache["url"] == url:
            return _discovery_cache["data"]
        if not url:
            return None
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient() as c:
                r = await c.get(url, timeout=10)
                r.raise_for_status()
                _discovery_cache["data"] = r.json()
                _discovery_cache["url"] = url
        except Exception as e:
            logging.warning("SSO discovery failed: %s", e)
            return None
        return _discovery_cache["data"]

    return {
        "_sso_env_defaults": _sso_env_defaults,
        "load_sso_config": load_sso_config,
        "get_sso_settings": get_sso_settings,
        "public_sso_config": public_sso_config,
        "save_sso_config": save_sso_config,
        "_get_sso_discovery": _get_sso_discovery,
        "_sso_states": _sso_states,
    }


__all__ = ["build_sso_config_runtime"]
