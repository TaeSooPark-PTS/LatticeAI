"""Embedder resolution for the AI-Worker build.

``build_brain_runtime`` used to open the knowledge-graph store and the
conversation store. Rust owns every durable write after v11.6.0 §Wave 2.5, so
what is left of "the Brain" inside the worker is the one port the compute seams
need: something that turns text into a vector.

The resolution rules are the product's, unchanged — a named profile supplies
model/dimensions, explicit configuration overrides it, and a provider that
cannot be reached degrades to the offline hash embedder while recording what
was requested.
"""

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
    """Resolve the configured embedding provider once.

    The resolution order is the product's, unchanged, with one addition at the
    end: detection. What is on this machine is *always* looked up (so
    ``GET /api/embeddings/status`` can say "a real embedder is right there"),
    and it changes the resolution only when the operator asked for it with
    ``LATTICEAI_EMBEDDING_PROVIDER=auto``. The finding rides back on the
    resolved object as ``detected`` — an added attribute, so every existing
    reader of ``ResolvedEmbedder`` is untouched.
    """

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
    except AttributeError:  # pragma: no cover - a stand-in without __dict__
        pass
    return resolved


def _configured(config: Any, profile: Mapping[str, Any]) -> Tuple[str, str, int]:
    """``(provider, model, dim)`` from configuration and the named profile."""
    provider = config.embedding_provider
    model = config.embedding_model or str(profile.get("model") or "")
    dim = config.embedding_dim or int(profile.get("dimensions") or 0)
    if config.embedding_profile and provider in _FALLBACK_NAMES | {AUTO_PROVIDER}:
        provider = str(profile.get("provider") or provider)
    return provider, model, dim


__all__ = ["build_embedder_runtime"]
