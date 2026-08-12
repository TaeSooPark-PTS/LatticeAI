"""The five text embedders, their factory, and the resolution that never fails.

Hash (offline fallback), MLX, Ollama, OpenAI-compatible and a user-supplied
dotted callable. ``build_embedding_provider`` constructs one by name without
ever making a network call; ``resolve_embedder`` probes it and degrades to the
hash fallback while *reporting* requested vs. active, so a down provider is
never quietly presented as live.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from lattice_brain.embeddings import DEFAULT_EMBEDDING_DIM, LocalEmbeddingModel

from .base import (
    EmbeddingProvider,
    EmbeddingUnavailable,
    _guess_dim,
    _NetworkEmbeddingProvider,
    _RemoteConfig,
)


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic feature-hashing embedder — no network, always available."""

    provider = "hash"
    grade = "fallback"

    def __init__(self, dim: int = DEFAULT_EMBEDDING_DIM):
        self._model = LocalEmbeddingModel(dim=dim)
        self.dim = self._model.dim
        self.model_id = self._model.model_id

    def embed(self, text: str) -> List[float]:
        return self._model.embed(text)  # already L2-normalized

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._model.embed(t) for t in texts]

    def health(self) -> Dict[str, Any]:
        return {"status": "ok", "detail": "deterministic local fallback"}


def _as_float_list(value: Any) -> List[Any]:
    """A pooled embedding row as a flat list.

    ``mx.array.tolist()`` is typed as scalar-or-nested; at this call site the
    array is always 1-D, so a scalar would be a bug worth surfacing.
    """
    if isinstance(value, (int, float)):
        raise EmbeddingUnavailable("MLX embedding produced a scalar, not a vector")
    return list(value)


class MLXEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "mlx"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        if not cfg.dim:
            self.dim = _guess_dim(cfg.model, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"mlx:{cfg.model}:{self.dim}"
        self._encoder: Optional[Tuple[str, Any, Any]] = None

    def _load(self):
        if self._encoder is not None:
            return self._encoder
        try:  # optional dependency; only imported when this provider is used
            from mlx_embeddings.utils import load as mlx_load  # type: ignore

            model, tokenizer = mlx_load(self._cfg.model)
            self._encoder = ("mlx_embeddings", model, tokenizer)
            return self._encoder
        except Exception as exc:  # pragma: no cover - environment dependent
            raise EmbeddingUnavailable(f"MLX embedding model unavailable: {exc}") from exc

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        kind, model, tokenizer = self._load()
        try:
            import mlx.core as mx  # type: ignore

            out: List[List[float]] = []
            for text in texts:
                ids = tokenizer.encode(text)
                tokens = mx.array([ids])
                result = model(tokens)
                pooled = result[0] if isinstance(result, (tuple, list)) else result
                vec = mx.mean(pooled, axis=1)[0] if pooled.ndim == 3 else pooled[0]
                out.append([float(x) for x in _as_float_list(vec.tolist())])
            return out
        except EmbeddingUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - environment dependent
            raise EmbeddingUnavailable(f"MLX embedding failed: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"MLX model {self._cfg.model} loaded"}
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)}


class OllamaEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "ollama"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._base = (cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
        if not cfg.dim:
            self.dim = _guess_dim(cfg.model, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"ollama:{cfg.model}:{self.dim}"

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        try:
            import httpx

            with httpx.Client(timeout=self._cfg.timeout) as client:
                # /api/embed supports batching; fall back to /api/embeddings.
                resp = client.post(
                    f"{self._base}/api/embed",
                    json={"model": self._cfg.model, "input": list(texts)},
                )
                if resp.status_code == 404:
                    for text in texts:
                        r = client.post(
                            f"{self._base}/api/embeddings",
                            json={"model": self._cfg.model, "prompt": text},
                        )
                        r.raise_for_status()
                        out.append(r.json().get("embedding") or [])
                    return out
                resp.raise_for_status()
                data = resp.json()
                return data.get("embeddings") or [data.get("embedding") or []]
        except Exception as exc:
            raise EmbeddingUnavailable(f"Ollama embedding failed: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        try:
            import httpx

            with httpx.Client(timeout=min(self._cfg.timeout, 5.0)) as client:
                r = client.get(f"{self._base}/api/tags")
                r.raise_for_status()
            return {"status": "ok", "detail": f"Ollama reachable at {self._base}"}
        except Exception as exc:
            return {"status": "unavailable", "detail": f"Ollama unreachable: {exc}"}


class OpenAICompatibleEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "openai"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        if not cfg.dim:
            self.dim = _guess_dim(cfg.model, DEFAULT_EMBEDDING_DIM)
        self.model_id = f"openai:{cfg.model}:{self.dim}"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"
        return headers

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        try:
            import httpx

            with httpx.Client(timeout=self._cfg.timeout) as client:
                r = client.post(
                    f"{self._base}/embeddings",
                    headers=self._headers(),
                    json={"model": self._cfg.model, "input": list(texts)},
                )
                r.raise_for_status()
                rows = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
                return [row.get("embedding") or [] for row in rows]
        except Exception as exc:
            raise EmbeddingUnavailable(f"OpenAI-compatible embedding failed: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        try:
            self._embed_raw(["ping"])
            return {"status": "ok", "detail": f"{self._base} reachable"}
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)}


class CustomEmbeddingProvider(_NetworkEmbeddingProvider):
    """Loads a dotted ``module:callable`` (or ``module.callable``).

    The callable receives ``List[str]`` and returns ``List[List[float]]``.
    Configured via ``LATTICEAI_EMBEDDING_CUSTOM_TARGET``.
    """

    provider = "custom"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._target_ref = str(cfg.extra.get("target") or os.getenv("LATTICEAI_EMBEDDING_CUSTOM_TARGET", ""))
        self.model_id = f"custom:{cfg.model or self._target_ref or 'callable'}:{self.dim}"
        self._fn: Optional[Callable[..., Any]] = None

    def _load(self):
        if self._fn is not None:
            return self._fn
        ref = self._target_ref
        if not ref:
            raise EmbeddingUnavailable("custom embedding target not configured (LATTICEAI_EMBEDDING_CUSTOM_TARGET)")
        module_name, _, attr = ref.replace(":", ".").rpartition(".")
        if not module_name:
            raise EmbeddingUnavailable(f"invalid custom embedding target: {ref}")
        try:
            module = importlib.import_module(module_name)
            self._fn = getattr(module, attr)
            return self._fn
        except Exception as exc:
            raise EmbeddingUnavailable(f"custom embedding target unavailable: {exc}") from exc

    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        fn = self._load()
        try:
            return list(fn(list(texts)))
        except Exception as exc:
            raise EmbeddingUnavailable(f"custom embedding failed: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"custom target {self._target_ref} loaded"}
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)}


# ── factory + resolution ──────────────────────────────────────────────────────
PROVIDER_TYPES = ("hash", "mlx", "ollama", "openai", "custom")


def build_embedding_provider(
    provider: str,
    *,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    dim: int = 0,
    timeout: float = 30.0,
    extra: Optional[Dict[str, Any]] = None,
) -> EmbeddingProvider:
    """Construct a provider by name. Never makes a network call."""
    kind = str(provider or "hash").strip().lower()
    if kind in {"", "hash", "local", "fallback"}:
        return HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))
    cfg = _RemoteConfig(
        model=model,
        base_url=base_url,
        api_key=api_key,
        dim=int(dim or 0),
        timeout=float(timeout or 30.0),
        extra=dict(extra or {}),
    )
    if kind == "mlx":
        return MLXEmbeddingProvider(cfg)
    if kind == "ollama":
        return OllamaEmbeddingProvider(cfg)
    if kind in {"openai", "openai-compatible", "openai_compatible"}:
        return OpenAICompatibleEmbeddingProvider(cfg)
    if kind == "custom":
        return CustomEmbeddingProvider(cfg)
    raise ValueError(f"unknown embedding provider: {provider!r} (expected one of {PROVIDER_TYPES})")


@dataclass
class ResolvedEmbedder:
    provider: EmbeddingProvider
    requested: str
    active: str
    fell_back: bool
    health: Dict[str, Any]
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "requested_provider": self.requested,
            "active_provider": self.active,
            "fell_back": self.fell_back,
            "health": self.health,
            "detail": self.detail,
            **self.provider.metadata(),
        }


def resolve_embedder(
    provider: str = "",
    *,
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    dim: int = 0,
    timeout: float = 30.0,
    extra: Optional[Dict[str, Any]] = None,
    probe: bool = True,
) -> ResolvedEmbedder:
    """Build the requested provider, degrading to hash if it is unavailable.

    Local-first guarantee: the app always gets a working embedder. When the
    requested provider is unreachable we return the hash fallback but record
    ``fell_back=True`` and the failing health detail so the UI shows it as
    *Unavailable* — the system never pretends a down provider is live.
    """
    requested = str(provider or "hash").strip().lower() or "hash"
    if requested in {"hash", "local", "fallback", ""}:
        hash_prov = HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))
        return ResolvedEmbedder(
            hash_prov, "hash", "hash", False, hash_prov.health(), "deterministic local fallback"
        )

    try:
        prov = build_embedding_provider(
            requested, model=model, base_url=base_url, api_key=api_key, dim=dim, timeout=timeout, extra=extra
        )
    except Exception as exc:
        fallback = HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))
        return ResolvedEmbedder(
            fallback, requested, "hash", True,
            {"status": "unavailable", "detail": str(exc)},
            f"could not construct {requested}; using hash fallback",
        )

    if probe:
        try:
            health = prov.health()
        except Exception as exc:  # provider health must never crash startup
            health = {"status": "unavailable", "detail": str(exc)}
    else:
        health = {"status": "unknown", "detail": "not probed"}
    if probe and health.get("status") != "ok":
        fallback = HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))
        return ResolvedEmbedder(
            fallback, requested, "hash", True, health,
            f"{requested} unavailable ({health.get('detail', '')}); using hash fallback",
        )
    return ResolvedEmbedder(prov, requested, prov.provider, False, health, "")
