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

Vision seam (v11.1.0, Track 3)
------------------------------
Images join the same contract through :class:`VisionEmbeddingProvider`, with two
deliberate differences from the text side:

* **No fallback.** The hash embedder turns *text* into a real, if crude, cosine
  signal. There is no equivalent for pixels: hashing a file path produces a
  vector that says nothing about the picture, so an unavailable vision model is
  reported as unavailable (:class:`EmbeddingUnavailable` /
  ``ResolvedVisionEmbedder.available == False``) and the caller skips the
  embedding instead of storing a decoy.
* **A separate space by default.** A CLIP-family image vector is not comparable
  with a BGE text vector, so ``space == "image"`` means "index these apart and
  join them by late fusion". Only a genuinely shared-space model may declare
  ``space == "shared"`` (opt-in), and only then can a *text* query be scored
  against image vectors.

:class:`VisionCaptioner` is the matching seam for descriptions. Its default
implementation returns ``None``: a caption is what a vision-language model
said about an image, so with no VLM loaded there is no caption — never a
sentence assembled from the filename and passed off as one.
"""

from __future__ import annotations

import importlib
import math
import os
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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
    "bge-m3": 1024,
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
    "e5-large": 1024,
    "multilingual-e5-large": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


PRODUCTION_PROVIDER_PROFILES: Dict[str, Dict[str, Any]] = {
    "local:bge-m3": {
        "id": "local:bge-m3",
        "provider": "mlx",
        "model": "bge-m3",
        "dimensions": 1024,
        "grade": "production",
        "family": "local",
        "label": "BGE-M3 local",
        "detail": "Multilingual semantic embeddings for local retrieval.",
    },
    "local:nomic-embed-text": {
        "id": "local:nomic-embed-text",
        "provider": "ollama",
        "model": "nomic-embed-text",
        "dimensions": 768,
        "grade": "production",
        "family": "local",
        "label": "Nomic Embed Text local",
        "detail": "General-purpose local semantic embeddings.",
    },
    "local:e5-large": {
        "id": "local:e5-large",
        "provider": "mlx",
        "model": "e5-large",
        "dimensions": 1024,
        "grade": "production",
        "family": "local",
        "label": "E5 Large local",
        "detail": "High-recall local retrieval profile.",
    },
    "local:gte-large": {
        "id": "local:gte-large",
        "provider": "mlx",
        "model": "gte-large",
        "dimensions": 1024,
        "grade": "production",
        "family": "local",
        "label": "GTE Large local",
        "detail": "Large local semantic embedding profile.",
    },
    "ollama:nomic-embed-text": {
        "id": "ollama:nomic-embed-text",
        "provider": "ollama",
        "model": "nomic-embed-text",
        "dimensions": 768,
        "grade": "production",
        "family": "ollama",
        "label": "Ollama Nomic Embed Text",
        "detail": "Production semantic embeddings through Ollama.",
    },
    "ollama:mxbai-embed-large": {
        "id": "ollama:mxbai-embed-large",
        "provider": "ollama",
        "model": "mxbai-embed-large",
        "dimensions": 1024,
        "grade": "production",
        "family": "ollama",
        "label": "Ollama MXBAI Embed Large",
        "detail": "High-quality local semantic embeddings through Ollama.",
    },
    "ollama:bge-m3": {
        "id": "ollama:bge-m3",
        "provider": "ollama",
        "model": "bge-m3",
        "dimensions": 1024,
        "grade": "production",
        "family": "ollama",
        "label": "Ollama BGE-M3-compatible",
        "detail": "BGE-M3-compatible providers exposed through Ollama.",
    },
    "mlx:bge-m3": {
        "id": "mlx:bge-m3",
        "provider": "mlx",
        "model": "bge-m3",
        "dimensions": 1024,
        "grade": "production",
        "family": "mlx",
        "label": "MLX BGE-M3",
        "detail": "Apple Silicon optimized local embeddings.",
    },
    "openai:text-embedding-3-small": {
        "id": "openai:text-embedding-3-small",
        "provider": "openai",
        "model": "text-embedding-3-small",
        "dimensions": 1536,
        "grade": "production",
        "family": "openai-compatible",
        "label": "OpenAI-compatible small",
        "detail": "OpenAI-compatible /v1/embeddings endpoint.",
    },
    "openai:text-embedding-3-large": {
        "id": "openai:text-embedding-3-large",
        "provider": "openai",
        "model": "text-embedding-3-large",
        "dimensions": 3072,
        "grade": "production",
        "family": "openai-compatible",
        "label": "OpenAI-compatible large",
        "detail": "Highest-dimensional OpenAI-compatible embedding profile.",
    },
}


def embedding_provider_profiles() -> List[Dict[str, Any]]:
    return [dict(PRODUCTION_PROVIDER_PROFILES[key]) for key in sorted(PRODUCTION_PROVIDER_PROFILES)]


def resolve_embedding_profile(profile: str) -> Dict[str, Any]:
    if not profile:
        return {}
    key = str(profile).strip().lower()
    if key in PRODUCTION_PROVIDER_PROFILES:
        return dict(PRODUCTION_PROVIDER_PROFILES[key])
    raise ValueError(f"unknown embedding profile: {profile!r}")


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
    def _model_id_with_dim(self, dim: int) -> str:
        """This provider's ``model_id`` restated at ``dim``.

        Every provider spells its identity ``<kind>:<model>:<dim>``, so the
        numeric tail is what moves when a live call reveals the model's true
        width. An id without such a tail does not encode a dimension and is
        returned unchanged — the index keys on ``model_id`` *and* ``dim``, so
        nothing becomes ambiguous.
        """
        head, sep, tail = self.model_id.rpartition(":")
        if sep and tail.isdigit():
            return f"{head}:{dim}"
        return self.model_id

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
        # strict=True: a dimension mismatch means the two vectors came from
        # different embedding models. Truncating to the shorter one produces a
        # plausible-looking similarity that is meaningless — exactly the silent
        # wrongness this codebase keeps finding. Callers that can hit a model
        # swap already handle failure and fall back to lexical search.
        left_v, right_v = list(left), list(right)
        if len(left_v) != len(right_v):
            raise ValueError(
                f"embedding dimension mismatch: {len(left_v)} vs {len(right_v)}; "
                "the vector index was built with a different model"
            )
        return float(sum(a * b for a, b in zip(left_v, right_v, strict=True)))

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


def _as_float_list(value: Any) -> List[Any]:
    """A pooled embedding row as a flat list.

    ``mx.array.tolist()`` is typed as scalar-or-nested; at this call site the
    array is always 1-D, so a scalar would be a bug worth surfacing.
    """
    if isinstance(value, (int, float)):
        raise EmbeddingUnavailable("MLX embedding produced a scalar, not a vector")
    return list(value)


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
                # lock the index identity to the true model dimensionality —
                # the id carries that dimension, so it moves with it or the
                # vectors end up filed under a width they do not have
                self.dim = len(vec)
                self.model_id = self._model_id_with_dim(self.dim)
            out.append(_l2_normalize(vec) if vec else [0.0] * self.dim)
        return out


# ── 2. MLX (local Apple-Silicon model) ────────────────────────────────────────
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


# ── Vision (image) embedding seam ─────────────────────────────────────────────
#: CLIP ViT-B/32 width — the most common local image-embedding output.
DEFAULT_VISION_DIM = 512
#: Image vectors live in their own index and join text results by late fusion.
VISION_SPACE_IMAGE = "image"
#: A genuinely multimodal model (CLIP-style) can share the text space — opt-in.
VISION_SPACE_SHARED = "shared"
VISION_SPACES = (VISION_SPACE_IMAGE, VISION_SPACE_SHARED)
VISION_PROVIDER_TYPES = ("mlx", "custom")
VISION_TARGET_ENV = "LATTICEAI_VISION_EMBEDDING_TARGET"
VISION_CAPTION_TARGET_ENV = "LATTICEAI_VISION_CAPTION_TARGET"

_KNOWN_VISION_DIMS = {
    "clip-vit-base-patch32": 512,
    "clip-vit-base-patch16": 512,
    "clip-vit-large-patch14": 768,
    "siglip-base-patch16-224": 768,
    "siglip-large-patch16-384": 1024,
}


def _guess_vision_dim(model: str, default: int) -> int:
    key = str(model or "").split("/")[-1].strip().lower()
    key = key.split(":")[0]
    return _KNOWN_VISION_DIMS.get(key, default)


def _normalize_space(value: Any) -> str:
    space = str(value or "").strip().lower()
    return space if space in VISION_SPACES else VISION_SPACE_IMAGE


class VisionEmbeddingProvider(EmbeddingProvider):
    """Turns an image *file path* into a vector.

    Subclasses implement :meth:`embed_images`; everything else — the single
    ``embed_image``, L2 normalization, locking the index identity to the width
    the model actually returned, and the refusal to embed text outside a shared
    space — is shared here.
    """

    provider = "vision"
    grade = "production"
    #: ``"image"`` (own index, late fusion) or ``"shared"`` (same space as text)
    space: str = VISION_SPACE_IMAGE

    def __init__(self, cfg: _RemoteConfig):
        self._cfg = cfg
        self.dim = int(cfg.dim or DEFAULT_VISION_DIM)
        self.space = _normalize_space(cfg.extra.get("space"))

    # ── required ──────────────────────────────────────────────────────────
    def embed_images(self, paths: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError

    # ── derived (shared) ──────────────────────────────────────────────────
    @property
    def shares_text_space(self) -> bool:
        """True when a text query may be scored against these vectors."""
        return self.space == VISION_SPACE_SHARED

    def embed_image(self, path: str) -> List[float]:
        vectors = self.embed_images([path])
        if not vectors:
            raise EmbeddingUnavailable(f"{self.model_id} returned no vector for {path}")
        return vectors[0]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        """Text side of a multimodal model — only in a shared space.

        Scoring a BGE query vector against CLIP image vectors produces a number
        with no meaning, so the image-space default refuses rather than
        returning something a caller would rank on.
        """
        if not self.shares_text_space:
            raise EmbeddingUnavailable(
                f"{self.model_id} embeds images into a separate space; text "
                "queries reach image nodes through late fusion, not this index"
            )
        return self._normalize_rows(self._embed_texts_raw(texts))

    def _embed_texts_raw(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError

    def _normalize_rows(self, rows: Iterable[Any]) -> List[List[float]]:
        """L2-normalize, and lock the index identity to the real width."""
        out: List[List[float]] = []
        for row in rows:
            vec = [float(x) for x in (row or [])]
            if not vec:
                raise EmbeddingUnavailable(f"{self.model_id} produced an empty vector")
            self.dim = len(vec)
            self.model_id = self._model_id_with_dim(self.dim)
            out.append(_l2_normalize(vec))
        return out

    def metadata(self) -> Dict[str, Any]:
        data = super().metadata()
        data.update(
            {
                "modality": "image",
                "space": self.space,
                "shares_text_space": self.shares_text_space,
            }
        )
        return data


class MLXVisionEmbeddingProvider(VisionEmbeddingProvider):
    """Local CLIP-family image embedder loaded through ``mlx_clip``.

    Guarded import, opt-in, never a core dependency: the module must expose
    ``load(model)`` returning an encoder with ``encode_image(paths)`` and —
    for a shared space — ``encode_text(texts)``.
    """

    provider = "mlx-vision"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        if not cfg.dim:
            self.dim = _guess_vision_dim(cfg.model, DEFAULT_VISION_DIM)
        self.model_id = f"mlx-vision:{cfg.model}:{self.dim}"
        self._encoder: Optional[Any] = None

    def _load(self) -> Any:
        if self._encoder is not None:
            return self._encoder
        try:  # optional dependency; only imported when this provider is used
            import mlx_clip  # type: ignore

            self._encoder = mlx_clip.load(self._cfg.model)
        except Exception as exc:
            raise EmbeddingUnavailable(f"MLX vision model unavailable: {exc}") from exc
        return self._encoder

    def embed_images(self, paths: Sequence[str]) -> List[List[float]]:
        encoder = self._load()
        try:
            rows = encoder.encode_image(list(paths))
        except Exception as exc:
            raise EmbeddingUnavailable(f"MLX vision embedding failed: {exc}") from exc
        return self._normalize_rows(rows)

    def _embed_texts_raw(self, texts: Sequence[str]) -> List[List[float]]:
        encoder = self._load()
        encode_text = getattr(encoder, "encode_text", None)
        if not callable(encode_text):
            raise EmbeddingUnavailable(
                f"{self.model_id} has no text encoder, so it cannot back a shared space"
            )
        try:
            return list(encode_text(list(texts)))
        except Exception as exc:
            raise EmbeddingUnavailable(f"MLX vision text embedding failed: {exc}") from exc

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"MLX vision model {self._cfg.model} loaded"}
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)}


class CustomVisionEmbeddingProvider(VisionEmbeddingProvider):
    """A user-supplied ``module:callable`` that embeds image paths.

    The callable receives ``List[str]`` (paths) and returns
    ``List[List[float]]``. Configured via ``LATTICEAI_VISION_EMBEDDING_TARGET``.
    """

    provider = "custom-vision"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._target_ref = str(cfg.extra.get("target") or os.getenv(VISION_TARGET_ENV, ""))
        self.model_id = f"custom-vision:{cfg.model or self._target_ref or 'callable'}:{self.dim}"
        self._fn: Optional[Callable[..., Any]] = None

    def _load(self) -> Callable[..., Any]:
        if self._fn is not None:
            return self._fn
        self._fn = _load_dotted(self._target_ref, VISION_TARGET_ENV, "vision embedding")
        return self._fn

    def embed_images(self, paths: Sequence[str]) -> List[List[float]]:
        fn = self._load()
        try:
            rows = list(fn(list(paths)))
        except Exception as exc:
            raise EmbeddingUnavailable(f"custom vision embedding failed: {exc}") from exc
        return self._normalize_rows(rows)

    def _embed_texts_raw(self, texts: Sequence[str]) -> List[List[float]]:
        # A dotted image embedder is one callable over paths; a shared space
        # would need a second, text-side entry point this contract has no slot
        # for. Saying so beats scoring a query against the wrong function.
        raise EmbeddingUnavailable(
            f"{self.model_id} is an image-only callable and has no text encoder"
        )

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"custom vision target {self._target_ref} loaded"}
        except Exception as exc:
            return {"status": "unavailable", "detail": str(exc)}


def _load_dotted(ref: str, env_name: str, label: str) -> Callable[..., Any]:
    """Import ``module:callable`` (or ``module.callable``) or explain why not."""
    if not ref:
        raise EmbeddingUnavailable(f"{label} target not configured ({env_name})")
    module_name, _, attr = ref.replace(":", ".").rpartition(".")
    if not module_name:
        raise EmbeddingUnavailable(f"invalid {label} target: {ref}")
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr)  # type: ignore[no-any-return]
    except Exception as exc:
        raise EmbeddingUnavailable(f"{label} target unavailable: {exc}") from exc


def build_vision_provider(
    provider: str,
    *,
    model: str = "",
    dim: int = 0,
    space: str = VISION_SPACE_IMAGE,
    timeout: float = 30.0,
    extra: Optional[Dict[str, Any]] = None,
) -> VisionEmbeddingProvider:
    """Construct a vision provider by name. Never makes a network call."""
    kind = str(provider or "").strip().lower()
    cfg = _RemoteConfig(
        model=model,
        dim=int(dim or 0),
        timeout=float(timeout or 30.0),
        extra={"space": space, **(extra or {})},
    )
    if kind == "mlx":
        return MLXVisionEmbeddingProvider(cfg)
    if kind == "custom":
        return CustomVisionEmbeddingProvider(cfg)
    raise ValueError(
        f"unknown vision embedding provider: {provider!r} (expected one of {VISION_PROVIDER_TYPES})"
    )


@dataclass
class ResolvedVisionEmbedder:
    """A vision provider, or an honest account of why there isn't one."""

    provider: Optional[VisionEmbeddingProvider]
    requested: str
    health: Dict[str, Any]
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.provider is not None

    @property
    def space(self) -> str:
        return self.provider.space if self.provider is not None else VISION_SPACE_IMAGE

    def as_port(self) -> Optional[Callable[[str], List[float]]]:
        """The one-argument seam Brain Core injects (``None`` when absent).

        ``lattice_brain`` must not import ``latticeai``, so the ingestion
        pipeline never sees this class — only the callable it hands over.
        """
        if self.provider is None:
            return None
        return self.provider.embed_image

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "requested_provider": self.requested,
            "available": self.available,
            "health": self.health,
            "detail": self.detail,
        }
        if self.provider is not None:
            payload.update(self.provider.metadata())
        return payload


def resolve_vision_embedder(
    provider: str = "",
    *,
    model: str = "",
    dim: int = 0,
    space: str = VISION_SPACE_IMAGE,
    timeout: float = 30.0,
    extra: Optional[Dict[str, Any]] = None,
    probe: bool = True,
) -> ResolvedVisionEmbedder:
    """Build the requested vision provider, or report it unavailable.

    Unlike :func:`resolve_embedder` there is no fallback: a hashed file path is
    not a picture. An empty ``provider`` means the user never asked for image
    embeddings, which is a configuration state rather than a failure.
    """
    requested = str(provider or "").strip().lower()
    if not requested:
        return ResolvedVisionEmbedder(
            None,
            "",
            {"status": "unavailable", "detail": "no vision provider configured"},
            "image embeddings are off; set a vision provider to enable them",
        )
    try:
        prov = build_vision_provider(
            requested, model=model, dim=dim, space=space, timeout=timeout, extra=extra
        )
    except Exception as exc:
        return ResolvedVisionEmbedder(
            None,
            requested,
            {"status": "unavailable", "detail": str(exc)},
            f"could not construct vision provider {requested}",
        )
    if not probe:
        return ResolvedVisionEmbedder(
            prov, requested, {"status": "unknown", "detail": "not probed"}, ""
        )
    try:
        health = prov.health()
    except Exception as exc:  # a provider probe must never crash startup
        health = {"status": "unavailable", "detail": str(exc)}
    if health.get("status") != "ok":
        return ResolvedVisionEmbedder(
            None,
            requested,
            health,
            f"{requested} vision model unavailable ({health.get('detail', '')})",
        )
    return ResolvedVisionEmbedder(prov, requested, health, "")


# ── Vision captions (a VLM said this, or nobody did) ──────────────────────────
class VisionCaptioner:
    """Describes an image — the null implementation, which describes nothing.

    Every "clever" fallback here is a lie: ``Image IMG_2381.png (JPEG
    3024x4032)`` is metadata wearing a caption's clothes, and once it is in the
    graph nothing downstream can tell it from a model's actual description. So
    the base class returns ``None`` and :meth:`available` says ``False``.
    """

    provider = "none"
    model_id = ""

    def available(self) -> bool:
        return False

    def caption(self, path: str, *, prompt: str = "") -> Optional[str]:
        return None

    def health(self) -> Dict[str, Any]:
        return {"status": "unavailable", "detail": "no vision-language model is loaded"}

    def metadata(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_id,
            "available": self.available(),
        }


#: Short, literal instruction — a caption is a description, not an essay.
DEFAULT_CAPTION_PROMPT = "Describe this image in one factual sentence."


class MLXVisionCaptioner(VisionCaptioner):
    """Caption through a locally loaded ``mlx_vlm`` model (guarded import)."""

    provider = "mlx-vlm"

    def __init__(self, model: str, *, prompt: str = DEFAULT_CAPTION_PROMPT, max_tokens: int = 64):
        self.model_id = str(model or "")
        self._prompt = prompt or DEFAULT_CAPTION_PROMPT
        self._max_tokens = max(1, int(max_tokens))
        self._loaded: Optional[Tuple[Any, Any]] = None

    def _load(self) -> Tuple[Any, Any]:
        if self._loaded is not None:
            return self._loaded
        try:  # optional dependency (`pip install "ltcai[local]"`)
            from mlx_vlm import load as vlm_load  # type: ignore

            model, processor = vlm_load(self.model_id)
            self._loaded = (model, processor)
        except Exception as exc:
            raise EmbeddingUnavailable(f"vision-language model unavailable: {exc}") from exc
        return self._loaded

    def available(self) -> bool:
        try:
            self._load()
            return True
        except EmbeddingUnavailable:
            return False

    def caption(self, path: str, *, prompt: str = "") -> Optional[str]:
        try:
            model, processor = self._load()
            from mlx_vlm import generate as vlm_generate  # type: ignore

            text = vlm_generate(
                model,
                processor,
                str(path),
                prompt or self._prompt,
                max_tokens=self._max_tokens,
            )
        except Exception:
            # A caption the model did not produce is not a caption. Absence is
            # the honest answer, and every caller already handles it.
            return None
        cleaned = str(text or "").strip()
        return cleaned or None

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"VLM {self.model_id} loaded"}
        except EmbeddingUnavailable as exc:
            return {"status": "unavailable", "detail": str(exc)}


class CustomVisionCaptioner(VisionCaptioner):
    """A user-supplied ``module:callable`` that captions an image path."""

    provider = "custom-vlm"

    def __init__(self, target: str = ""):
        self._target_ref = str(target or os.getenv(VISION_CAPTION_TARGET_ENV, ""))
        self.model_id = self._target_ref
        self._fn: Optional[Callable[..., Any]] = None

    def _load(self) -> Callable[..., Any]:
        if self._fn is None:
            self._fn = _load_dotted(self._target_ref, VISION_CAPTION_TARGET_ENV, "vision caption")
        return self._fn

    def available(self) -> bool:
        try:
            self._load()
            return True
        except EmbeddingUnavailable:
            return False

    def caption(self, path: str, *, prompt: str = "") -> Optional[str]:
        try:
            text = self._load()(str(path), prompt or DEFAULT_CAPTION_PROMPT)
        except Exception:
            return None
        cleaned = str(text or "").strip()
        return cleaned or None

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {"status": "ok", "detail": f"custom captioner {self._target_ref} loaded"}
        except EmbeddingUnavailable as exc:
            return {"status": "unavailable", "detail": str(exc)}


def resolve_vision_captioner(
    provider: str = "", *, model: str = "", target: str = ""
) -> VisionCaptioner:
    """Build a captioner, or the null one that honestly captions nothing."""
    kind = str(provider or "").strip().lower()
    if kind in {"mlx", "mlx-vlm", "mlx_vlm"} and model:
        return MLXVisionCaptioner(model)
    if kind in {"custom", "custom-vlm"}:
        return CustomVisionCaptioner(target)
    return VisionCaptioner()


def vision_caption_port(captioner: VisionCaptioner) -> Optional[Callable[[str], Optional[str]]]:
    """The caption seam Brain Core injects — ``None`` when no VLM is loaded."""
    return captioner.caption if captioner.available() else None


__all__ = [
    "DEFAULT_CAPTION_PROMPT",
    "DEFAULT_VISION_DIM",
    "VISION_CAPTION_TARGET_ENV",
    "VISION_PROVIDER_TYPES",
    "VISION_SPACES",
    "VISION_SPACE_IMAGE",
    "VISION_SPACE_SHARED",
    "VISION_TARGET_ENV",
    "CustomVisionCaptioner",
    "CustomVisionEmbeddingProvider",
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "MLXVisionCaptioner",
    "MLXVisionEmbeddingProvider",
    "ResolvedVisionEmbedder",
    "VisionCaptioner",
    "VisionEmbeddingProvider",
    "build_vision_provider",
    "resolve_vision_captioner",
    "resolve_vision_embedder",
    "vision_caption_port",
    "HashEmbeddingProvider",
    "MLXEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "CustomEmbeddingProvider",
    "ResolvedEmbedder",
    "build_embedding_provider",
    "resolve_embedder",
    "resolve_embedding_profile",
    "embedding_provider_profiles",
    "PRODUCTION_PROVIDER_PROFILES",
    "PROVIDER_TYPES",
]
