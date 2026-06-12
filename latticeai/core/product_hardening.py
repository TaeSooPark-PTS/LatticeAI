"""Product hardening and privacy status helpers.

These helpers are read-only and must not perform network probes. They describe
the local-first startup posture and distinguish available credentials from
enabled outbound communication.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from latticeai.core.config import Config


def _bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _present(env: Mapping[str, str], *keys: str) -> bool:
    return any(bool(str(env.get(key) or "").strip()) for key in keys)


def external_integration_status(
    config: Config,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    if env is None:
        env = os.environ
    telegram_credentials = _present(env, "LATTICEAI_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    brain_network_auto_push = _bool(env, "LATTICEAI_BRAIN_NETWORK_AUTO_PUSH", default=False)
    updater_enabled = _bool(env, "LATTICEAI_ENABLE_UPDATES", default=False)
    model_downloads_enabled = _bool(env, "LATTICEAI_ALLOW_MODEL_DOWNLOADS", default=False) or bool(config.autoload_models)
    docker_auto_start = _bool(env, "LATTICEAI_DOCKER_AUTO_START", default=False)
    external_connectors_enabled = _bool(env, "LATTICEAI_ENABLE_EXTERNAL_CONNECTORS", default=False)
    postgres_enabled = config.storage_engine == "postgres" and bool(config.postgres_dsn)
    return {
        "local_only_default": default_startup_local_only(config, env=env),
        "integrations": {
            "telegram": {
                "enabled": bool(config.enable_telegram),
                "credential_present": telegram_credentials,
                "opt_in_required": True,
                "automatic_egress": bool(config.enable_telegram),
                "detail": (
                    "enabled by LATTICEAI_ENABLE_TELEGRAM"
                    if config.enable_telegram
                    else "disabled; token presence alone does not start Telegram"
                ),
            },
            "brain_network": {
                "enabled": brain_network_auto_push,
                "credential_present": False,
                "opt_in_required": True,
                "automatic_egress": brain_network_auto_push,
                "detail": "peer pushes are user/admin initiated; no automatic peer sync by default",
            },
            "updates": {
                "enabled": updater_enabled,
                "credential_present": False,
                "opt_in_required": True,
                "automatic_egress": updater_enabled,
                "detail": "desktop updater checks are disabled unless LATTICEAI_ENABLE_UPDATES is true",
            },
            "model_downloads": {
                "enabled": model_downloads_enabled,
                "credential_present": _present(env, "HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN"),
                "opt_in_required": True,
                "automatic_egress": bool(config.autoload_models),
                "detail": "model downloads require an explicit load/autoload setting",
            },
            "docker": {
                "enabled": docker_auto_start,
                "credential_present": False,
                "opt_in_required": True,
                "automatic_egress": docker_auto_start,
                "detail": "Docker setup requires explicit runtime consent; auto-start is disabled by default",
            },
            "postgres": {
                "enabled": postgres_enabled,
                "credential_present": bool(config.postgres_dsn),
                "opt_in_required": True,
                "automatic_egress": postgres_enabled,
                "detail": "Postgres scale mode is used only when storage engine and DSN are explicitly configured",
            },
            "external_connectors": {
                "enabled": external_connectors_enabled,
                "credential_present": _present(
                    env,
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "GITHUB_TOKEN",
                    "SLACK_BOT_TOKEN",
                    "DISCORD_BOT_TOKEN",
                ),
                "opt_in_required": True,
                "automatic_egress": external_connectors_enabled,
                "detail": "connector credentials are inert until the connector is explicitly enabled and invoked",
            },
        },
    }


def default_startup_local_only(
    config: Config,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    if env is None:
        env = os.environ
    local_embedding = config.embedding_provider in {"", "hash", "local", "fallback", "sqlite"}
    external = external_integration_status_no_recurse(config, env=env)
    return (
        not config.network_exposed
        and not config.cors_allow_network
        and not config.enable_telegram
        and not config.autoload_models
        and config.storage_engine == "sqlite"
        and local_embedding
        and not any(item["automatic_egress"] for item in external.values())
    )


def external_integration_status_no_recurse(
    config: Config,
    *,
    env: Mapping[str, str],
) -> Dict[str, Dict[str, Any]]:
    return {
        "brain_network": {"automatic_egress": _bool(env, "LATTICEAI_BRAIN_NETWORK_AUTO_PUSH", default=False)},
        "updates": {"automatic_egress": _bool(env, "LATTICEAI_ENABLE_UPDATES", default=False)},
        "docker": {"automatic_egress": _bool(env, "LATTICEAI_DOCKER_AUTO_START", default=False)},
        "postgres": {"automatic_egress": config.storage_engine == "postgres" and bool(config.postgres_dsn)},
        "external_connectors": {"automatic_egress": _bool(env, "LATTICEAI_ENABLE_EXTERNAL_CONNECTORS", default=False)},
    }


def build_product_hardening_status(
    *,
    config: Config,
    portability: Any = None,
    device_identity: Any = None,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    if env is None:
        env = os.environ
    storage = {"available": False}
    backup = {"available": False}
    if portability is not None and getattr(portability, "available", lambda: False)():
        storage = portability.storage_status()
        backup = portability.backup_health()
    identity = {}
    if device_identity is not None:
        identity = device_identity.describe()
    data_dir = Path(config.data_dir)
    return {
        "version": "4.3.0",
        "startup": {
            "local_only_default": default_startup_local_only(config, env=env),
            "host": config.host,
            "port": config.port,
            "network_exposed": config.network_exposed,
            "auth_required": config.require_auth,
            "cors_network_allowed": config.cors_allow_network,
        },
        "desktop": {
            "sidecar_lifecycle": "managed",
            "restart_supported": True,
            "shutdown_supported": True,
            "updater": {
                "enabled": _bool(env, "LATTICEAI_ENABLE_UPDATES", default=False),
                "limitation": "No external update checks run unless explicitly enabled by policy.",
            },
        },
        "first_run": {
            "data_dir": str(data_dir),
            "data_dir_exists": data_dir.exists(),
            "python_available": shutil.which("python3") is not None or shutil.which("python") is not None,
            "docker_available": shutil.which("docker") is not None,
            "docker_required": False,
            "postgres_required": False,
        },
        "privacy": external_integration_status(config, env=env),
        "storage": storage,
        "backup": backup,
        "device_identity": identity,
        "permissions": {
            "export_requires_admin": True,
            "import_requires_admin": True,
            "restore_requires_admin": True,
            "destructive_restore_requires_confirmation": True,
            "workspace_isolation_enforced": True,
            "audit_log_visible_to_admin": True,
        },
        "failure_policy": {
            "archive_corruption": "fail_closed",
            "partial_archive": "fail_closed",
            "signature_mismatch": "fail_closed",
            "unsupported_version": "fail_closed",
            "missing_docker": "honest_unavailable",
            "missing_postgres": "honest_unavailable",
            "permission_denied": "honest_error",
        },
    }


__all__ = [
    "build_product_hardening_status",
    "default_startup_local_only",
    "external_integration_status",
]
