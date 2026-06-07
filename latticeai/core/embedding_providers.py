"""Provider-backed embeddings for Lattice AI retrieval.

The knowledge graph stores dense vectors keyed by ``(embedding_model,
embedding_dim)`` and only ever compares vectors that share those keys
(``knowledge_graph.vector_search``). That contract means the *embedder* can be
swapped behind a single interface as long as every implementation agrees on:

* ``model_id`` / ``dim`` — the index identity (a change forces a re-index, which
  ``index_status`` already reports as ``stale``/``needs_reindex``);
* ``encode`` / ``decode`` — the on-disk float32 codec (shared by all providers);
* ``embed`` returns an **L2-normalized** vector, so ``similarity`` is a plain dot
  product and equals cosine similarity regardless of provider.

This module defines that :class:`EmbeddingProvider` interface and five concrete
implementations:

1. :class:`HashEmbeddingProvider`  — deterministic, offline, always-available
   fallback (wraps the legacy :class:`~latticeai.core.local_embeddings.LocalEmbeddingModel`).
2. :class:`MLXEmbeddingProvider`   — local Apple-Silicon embedding models.
3. :class:`OllamaEmbeddingProvider` — a local/remote Ollama server.
4. :class:`OpenAICompatibleEmbeddingProvider` — any ``/v1/embeddings`` endpoint
   (OpenAI, LM Studio, vLLM, llama.cpp, Together, …).
5. :class:`CustomEmbeddingProvider` — a user-supplied dotted callable.

:func:`resolve_embedder` builds the configured provider and, when that provider
is unavailable, degrades to the hash fallback while *reporting* the requested
vs. active provider — nothing is silently faked.
"""

from __future__ import annotations

import importlib
import math
import os
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from latticeai.core.local_embeddings import DEFAULT_EMBEDDING_DIM, LocalEmbeddingModel


class EmbeddingUnavailable(RuntimeError):
    """Raised when a configured provider cannot produce an embedding.

    Callers in the hot path (``vector_search``) translate this into a clear
    503/"provider unavailable" rather than a misleading empty result.
    """


# Best-known output dimensionality for common embedding models, so the index
# identity is stable before the first (possibly remote) call. A configured
# ``dim`` always wins; an unknown model falls back to a one-time live probe.
_KNOWN_DIMS = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "all-minilm-l6-v2": 384,
    "bge-small-en": 384,
    "bge-base-en": 768,
    "bge-large-en": 1024,
    "gte-small": 384,
    "gte-base": 768,
    "gte-large": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


def _guess_dim(model: str, default: int) -> int:
    key = str(model or "").split("/")[-1].strip().lower()
    key = key.split(":")[0]
    return _KNOWN_DIMS.get(key, default)


def _l2_normalize(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(v) * float(v) for v in vector))
    if norm <= 0:
        return [float(v) for v in vector]
    return [float(v) / norm for v in vector]


class EmbeddingProvider:
    """Interface every embedder implements.

    Subclasses must set ``model_id`` and ``dim`` and implement
    :meth:`embed_batch`; the rest (single embed, codec, similarity) is shared.
    """

    #: stable identity stored alongside every vector — change ⇒ re-index
    model_id: str = ""
    #: vector dimensionality
    dim: int = DEFAULT_EMBEDDING_DIM
    #: short provider kind ("hash" | "mlx" | "ollama" | "openai" | "custom")
    provider: str = "hash"
    #: "fallback" (hash) | "production" (real semantic model)
    grade: str = "production"

    # ── required ──────────────────────────────────────────────────────────
    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError

    # ── derived (shared) ──────────────────────────────────────────────────
    def embed(self, text: str) -> List[float]:
        result = self.embed_batch([text])
        return result[0] if result else [0.0] * self.dim

    def encode(self, vector: Iterable[float]) -> bytes:
        values = [float(v) for v in vector]
        return struct.pack(f"<{len(values)}f", *values)

    def decode(self, payload: bytes, dim: Optional[int] = None) -> List[float]:
        if not payload:
            return []
        count = int(dim or self.dim)
        if len(payload) != count * 4:
            count = len(payload) // 4
        return list(struct.unpack(f"<{count}f", payload[: count * 4]))

    def similarity(self, left: Iterable[float], right: Iterable[float]) -> float:
        return float(sum(a * b for a, b in zip(left, right)))

    # ── observability ─────────────────────────────────────────────────────
    def health(self) -> Dict[str, Any]:
        """Return ``{status, detail}``; status ∈ ok | unavailable."""
        return {"status": "ok", "detail": "ready"}

    def metadata(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_id,
            "model_id": self.model_id,
            "dim": self.dim,
            "grade": self.grade,
        }


# ── 1. Hash (offline fallback) ────────────────────────────────────────────────
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


# ── shared base for remote/model-backed providers ─────────────────────────────
@dataclass
class _RemoteConfig:
    model: str
    base_url: str = ""
    api_key: str = ""
    dim: int = DEFAULT_EMBEDDING_DIM
    timeout: float = 30.0
    extra: Dict[str, Any] = field(default_factory=dict)


class _NetworkEmbeddingProvider(EmbeddingProvider):
    """Common machinery for providers that call a model/server to embed."""

    def __init__(self, cfg: _RemoteConfig):
        self._cfg = cfg
        self.dim = int(cfg.dim or DEFAULT_EMBEDDING_DIM)

    # subclasses implement the raw call
    def _embed_raw(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        clean = [str(t or "")[:50_000] for t in texts]
        if not clean:
            return []
        vectors = self._embed_raw(clean)
        out: List[List[float]] = []
        for vec in vectors:
            vec = [float(x) for x in (vec or [])]
            if vec:
                # lock the index identity to the true model dimensionality
                self.dim = len(vec)
            out.append(_l2_normalize(vec) if vec else [0.0] * self.dim)
        return out


# ── 2. MLX (local Apple-Silicon model) ────────────────────────────────────────
class MLXEmbeddingProvider(_NetworkEmbeddingProvider):
    provider = "mlx"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self.model_id = f"mlx:{cfg.model}:{self.dim}"
        self._encoder = None

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
                out.append([float(x) for x in vec.tolist()])
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


# ── 3. Ollama ─────────────────────────────────────────────────────────────────
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


# ── 4. OpenAI-compatible (/v1/embeddings) ─────────────────────────────────────
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


# ── 5. Custom (user-supplied callable) ────────────────────────────────────────
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
        self._fn = None

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
        prov = HashEmbeddingProvider(dim=int(dim or DEFAULT_EMBEDDING_DIM))
        return ResolvedEmbedder(prov, "hash", "hash", False, prov.health(), "deterministic local fallback")

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


__all__ = [
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "HashEmbeddingProvider",
    "MLXEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "CustomEmbeddingProvider",
    "ResolvedEmbedder",
    "build_embedding_provider",
    "resolve_embedder",
    "PROVIDER_TYPES",
]
