"""Security runtime assembly for app startup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from latticeai.core.config import Config


def build_security_runtime(config: "Config") -> Dict[str, Any]:
    """Build auth/security-derived runtime settings from the central config."""

    from latticeai.core.security import configure_trusted_proxies

    configure_trusted_proxies(config.trusted_proxies)
    return {
        "SSO_DISCOVERY_URL": config.sso_discovery_url,
        "SSO_CLIENT_ID": config.sso_client_id,
        "SSO_CLIENT_SECRET": config.sso_client_secret,
        "SSO_REDIRECT_URI": config.sso_redirect_uri,
        "SSO_PROVIDER_NAME": config.sso_provider_name,
        "RATE_LIMIT_ENABLED": config.rate_limit_enabled,
        "OPEN_REGISTRATION": config.open_registration,
        "INVITE_CODE": config.invite_code,
        "INVITE_GATE_ENABLED": config.invite_gate_enabled,
    }
