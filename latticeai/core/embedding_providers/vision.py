from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .autodetect import pinned_model_target
from .base import (
    EmbeddingProvider,
    EmbeddingUnavailable,
    _l2_normalize,
    _load_dotted,
    _RemoteConfig,
)

DEFAULT_VISION_DIM = 512
VISION_SPACE_IMAGE = "image"
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
    provider = "vision"
    grade = "production"
    space: str = VISION_SPACE_IMAGE

    def __init__(self, cfg: _RemoteConfig):
        self._cfg = cfg
        self.dim = int(cfg.dim or DEFAULT_VISION_DIM)
        self.space = _normalize_space(cfg.extra.get("space"))

    def embed_images(self, paths: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError

    @property
    def shares_text_space(self) -> bool:
        return self.space == VISION_SPACE_SHARED

    def embed_image(self, path: str) -> List[float]:
        vectors = self.embed_images([path])
        if not vectors:
            raise EmbeddingUnavailable(f"{self.model_id} returned no vector for {path}")
        return vectors[0]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        if not self.shares_text_space:
            raise EmbeddingUnavailable(
                f"{self.model_id} embeds images into a separate space; text "
                "queries reach image nodes through late fusion, not this index"
            )
        return self._normalize_rows(self._embed_texts_raw(texts))

    def _embed_texts_raw(self, texts: Sequence[str]) -> List[List[float]]:
        raise NotImplementedError

    def _normalize_rows(self, rows: Iterable[Any]) -> List[List[float]]:
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
        try:
            import mlx_clip

            self._encoder = mlx_clip.load(pinned_model_target(self._cfg.model))
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
            raise EmbeddingUnavailable(
                f"MLX vision text embedding failed: {exc}"
            ) from exc

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {
                "status": "ok",
                "detail": f"MLX vision model {self._cfg.model} loaded",
            }
        except (
            EmbeddingUnavailable,
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
            return {"status": "unavailable", "detail": str(exc)}


class CustomVisionEmbeddingProvider(VisionEmbeddingProvider):
    provider = "custom-vision"

    def __init__(self, cfg: _RemoteConfig):
        super().__init__(cfg)
        self._target_ref = str(
            cfg.extra.get("target") or os.getenv(VISION_TARGET_ENV, "")
        )
        self.model_id = (
            f"custom-vision:{cfg.model or self._target_ref or 'callable'}:{self.dim}"
        )
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
            raise EmbeddingUnavailable(
                f"custom vision embedding failed: {exc}"
            ) from exc
        return self._normalize_rows(rows)

    def _embed_texts_raw(self, texts: Sequence[str]) -> List[List[float]]:
        raise EmbeddingUnavailable(
            f"{self.model_id} is an image-only callable and has no text encoder"
        )

    def health(self) -> Dict[str, Any]:
        try:
            self._load()
            return {
                "status": "ok",
                "detail": f"custom vision target {self._target_ref} loaded",
            }
        except (
            EmbeddingUnavailable,
            ImportError,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            AttributeError,
        ) as exc:
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
    except (
        ValueError,
        TypeError,
        RuntimeError,
        OSError,
        KeyError,
        AttributeError,
    ) as exc:
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
    except (
        EmbeddingUnavailable,
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        AttributeError,
    ) as exc:
        health = {"status": "unavailable", "detail": str(exc)}
    if health.get("status") != "ok":
        return ResolvedVisionEmbedder(
            None,
            requested,
            health,
            f"{requested} vision model unavailable ({health.get('detail', '')})",
        )
    return ResolvedVisionEmbedder(prov, requested, health, "")
