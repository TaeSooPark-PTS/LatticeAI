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

from typing import Any, Callable, Mapping


def build_embedder_runtime(
    *,
    config: Any,
    profile: Mapping[str, Any],
    resolve_embedder: Callable[..., Any],
) -> Any:
    """Resolve the configured embedding provider once."""

    provider = config.embedding_provider
    model = config.embedding_model or str(profile.get("model") or "")
    dim = config.embedding_dim or int(profile.get("dimensions") or 0)
    if config.embedding_profile and provider in {"", "hash", "local", "fallback"}:
        provider = str(profile.get("provider") or provider)

    return resolve_embedder(
        provider,
        model=model,
        base_url=config.embedding_base_url,
        api_key=config.embedding_api_key,
        dim=dim,
        timeout=config.embedding_timeout,
        extra={"target": config.embedding_custom_target},
        probe=provider not in {"", "hash", "local", "fallback"},
    )


__all__ = ["build_embedder_runtime"]
