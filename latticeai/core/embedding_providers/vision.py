"""Images on the same contract as text — with two deliberate differences.

**No fallback.** The hash embedder turns text into a real, if crude, cosine
signal; hashing a file path says nothing about the picture, so an unavailable
vision model is reported unavailable and the caller skips the embedding rather
than storing a decoy.

**A separate space by default.** A CLIP-family image vector is not comparable
with a BGE text vector, so ``space == "image"`` means "index these apart and
join them by late fusion". Only a genuinely shared-space model may declare
``space == "shared"``, and only then can a text query be scored against image
vectors.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .base import (
    EmbeddingProvider,
    EmbeddingUnavailable,
    _l2_normalize,
    _load_dotted,
    _RemoteConfig,
)

#: CLIP ViT-B/32 width — the most common local image-embedding output.
DEFAULT_VISION_DIM = 512
#: Image vectors live in their own index and join text results by late fusion.
VISION_SPACE_IMAGE = "image"
#: A genuinely multimodal model (CLIP-style) can share the text space — opt-in.
VISION_SPACE_SHARED = "shared"
VISION_SPACES = (VISION_SPACE_IMAGE, VISION_SPACE_SHARED)
VISION_PROVIDER_TYPES = ("mlx", "custom")
VISION_TARGET_ENV = "LATTICEAI_VISION_EMBEDDING_TARGET"


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
