"""Security runtime assembly for app startup."""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict

from latticeai.runtime.stages import RuntimeStage

if TYPE_CHECKING:
    from latticeai.core.config import Config


_SECURITY_SECRETS_FILE = "security_secrets.json"


@dataclass(frozen=True)
class SecurityRuntime(RuntimeStage):
    SSO_DISCOVERY_URL: str
    SSO_CLIENT_ID: str
    SSO_CLIENT_SECRET: str
    SSO_REDIRECT_URI: str
    SSO_PROVIDER_NAME: str
    RATE_LIMIT_ENABLED: bool
    OPEN_REGISTRATION: bool
    INVITE_CODE: str
    INVITE_COOKIE_SECRET: str
    INVITE_GATE_ENABLED: bool
    SECURE_COOKIES: bool


def _stored_security_secrets(data_dir: Path) -> Dict[str, str]:
    path = data_dir / _SECURITY_SECRETS_FILE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        # A corrupt secret store invalidates old invite cookies, but must never
        # make startup fall back to a shared or predictable signing key.
        logging.warning("invite-gate secret store is unreadable; rotating secrets: %s", exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: str(value)
        for key, value in payload.items()
        if key in {"invite_code", "invite_cookie_secret"} and value
    }


def _resolve_invite_gate_secrets(config: "Config") -> tuple[str, str]:
    """Resolve stable, per-install invitation secrets.

    No file is created while the invitation gate is disabled.  Once enabled,
    missing values are generated with the OS CSPRNG and persisted atomically
    with mode 0600 so restarts do not invalidate links or signed cookies.
    Explicit environment values remain authoritative and are not copied to the
    local secret file unnecessarily.
    """

    if not config.invite_gate_enabled:
        return config.invite_code, config.invite_cookie_secret

    from latticeai.core.io_utils import atomic_write_json

    data_dir = Path(config.data_dir)
    stored = _stored_security_secrets(data_dir)
    invite_code = config.invite_code or stored.get("invite_code") or secrets.token_urlsafe(24)
    cookie_secret = (
        config.invite_cookie_secret
        or stored.get("invite_cookie_secret")
        or secrets.token_urlsafe(48)
    )

    persisted = dict(stored)
    if not config.invite_code:
        persisted["invite_code"] = invite_code
    if not config.invite_cookie_secret:
        persisted["invite_cookie_secret"] = cookie_secret
    if persisted != stored:
        atomic_write_json(data_dir / _SECURITY_SECRETS_FILE, persisted)
    return invite_code, cookie_secret


def build_security_runtime(config: "Config") -> SecurityRuntime:
    """Build auth/security-derived runtime settings from the central config."""

    from latticeai.core.security import configure_trusted_proxies

    configure_trusted_proxies(config.trusted_proxies)
    invite_code, invite_cookie_secret = _resolve_invite_gate_secrets(config)
    secure_cookies = bool(config.is_public or config.network_exposed)
    if secure_cookies:
        logging.warning(
            "Public/non-loopback mode: authentication and Secure cookies are forced; "
            "serve Lattice AI through HTTPS/TLS or browser sessions will be rejected."
        )
    if config.invite_gate_enabled and not config.invite_code:
        logging.warning(
            "No LATTICEAI_INVITE_CODE was configured; generated a private per-install "
            "invite code in %s.",
            Path(config.data_dir) / _SECURITY_SECRETS_FILE,
        )
    return SecurityRuntime(
        SSO_DISCOVERY_URL=config.sso_discovery_url,
        SSO_CLIENT_ID=config.sso_client_id,
        SSO_CLIENT_SECRET=config.sso_client_secret,
        SSO_REDIRECT_URI=config.sso_redirect_uri,
        SSO_PROVIDER_NAME=config.sso_provider_name,
        RATE_LIMIT_ENABLED=config.rate_limit_enabled,
        OPEN_REGISTRATION=config.open_registration,
        INVITE_CODE=invite_code,
        INVITE_COOKIE_SECRET=invite_cookie_secret,
        INVITE_GATE_ENABLED=config.invite_gate_enabled,
        SECURE_COOKIES=secure_cookies,
    )


__all__ = ["SecurityRuntime", "build_security_runtime"]
