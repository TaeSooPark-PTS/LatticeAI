from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Tuple

from latticeai.core.embedding_providers.autodetect import (
    AUTO_PROVIDER,
    Detection,
    detect_embedder,
    resolve_auto_provider,
)

_FALLBACK_NAMES = {"", "hash", "local", "fallback"}


def build_embedder_runtime(
    *,
    config: Any,
    profile: Mapping[str, Any],
    resolve_embedder: Callable[..., Any],
    detect: Optional[Callable[..., Detection]] = None,
) -> Any:

    provider, model, dim = _configured(config, profile)
    detection = (detect or detect_embedder)(
        configured_provider=provider, configured_model=model
    )
    provider, model, dim = resolve_auto_provider(provider, model, dim, detection)

    resolved = resolve_embedder(
        provider,
        model=model,
        base_url=config.embedding_base_url,
        api_key=config.embedding_api_key,
        dim=dim,
        timeout=config.embedding_timeout,
        extra={"target": config.embedding_custom_target},
        probe=provider not in _FALLBACK_NAMES,
    )
    try:
        resolved.detected = detection
    except AttributeError:
        pass
    return resolved


def _configured(config: Any, profile: Mapping[str, Any]) -> Tuple[str, str, int]:
    provider = config.embedding_provider
    model = config.embedding_model or str(profile.get("model") or "")
    dim = config.embedding_dim or int(profile.get("dimensions") or 0)
    if config.embedding_profile and provider in _FALLBACK_NAMES | {AUTO_PROVIDER}:
        provider = str(profile.get("provider") or provider)
    return provider, model, dim


__all__ = ["build_embedder_runtime"]
