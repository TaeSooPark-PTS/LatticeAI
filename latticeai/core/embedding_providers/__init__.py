from __future__ import annotations

from lattice_brain.embeddings import DEFAULT_EMBEDDING_DIM as DEFAULT_EMBEDDING_DIM
from lattice_brain.embeddings import LocalEmbeddingModel as LocalEmbeddingModel

from .autodetect import AUTO_PROVIDER as AUTO_PROVIDER
from .autodetect import AUTODETECT_ENV as AUTODETECT_ENV
from .autodetect import LOCAL_HF_MODELS as LOCAL_HF_MODELS
from .autodetect import LOCAL_MLX_MODELS as LOCAL_MLX_MODELS
from .autodetect import Detection as Detection
from .autodetect import detect_embedder as detect_embedder
from .autodetect import detect_local_hf as detect_local_hf
from .autodetect import detect_local_mlx as detect_local_mlx
from .autodetect import detect_ollama as detect_ollama
from .autodetect import resolve_auto_provider as resolve_auto_provider
from .base import _KNOWN_DIMS as _KNOWN_DIMS
from .base import EmbeddingIndexContract as EmbeddingIndexContract
from .base import EmbeddingProvider as EmbeddingProvider
from .base import EmbeddingUnavailable as EmbeddingUnavailable
from .base import _guess_dim as _guess_dim
from .base import _l2_normalize as _l2_normalize
from .base import _load_dotted as _load_dotted
from .base import _NetworkEmbeddingProvider as _NetworkEmbeddingProvider
from .base import _RemoteConfig as _RemoteConfig
from .captions import DEFAULT_CAPTION_PROMPT as DEFAULT_CAPTION_PROMPT
from .captions import VISION_CAPTION_TARGET_ENV as VISION_CAPTION_TARGET_ENV
from .captions import CustomVisionCaptioner as CustomVisionCaptioner
from .captions import MLXVisionCaptioner as MLXVisionCaptioner
from .captions import VisionCaptioner as VisionCaptioner
from .captions import resolve_vision_captioner as resolve_vision_captioner
from .captions import vision_caption_port as vision_caption_port
from .profiles import PRODUCTION_PROVIDER_PROFILES as PRODUCTION_PROVIDER_PROFILES
from .profiles import RECOMMENDED_EMBEDDING_MODEL as RECOMMENDED_EMBEDDING_MODEL
from .profiles import RECOMMENDED_EMBEDDING_PROFILE as RECOMMENDED_EMBEDDING_PROFILE
from .profiles import embedding_provider_profiles as embedding_provider_profiles
from .profiles import resolve_embedding_profile as resolve_embedding_profile
from .text import DEFAULT_FALLBACK_DIM as DEFAULT_FALLBACK_DIM
from .text import DEFAULT_HF_EMBEDDING_MODEL as DEFAULT_HF_EMBEDDING_MODEL
from .text import EMBEDDINGGEMMA_PREFIXES as EMBEDDINGGEMMA_PREFIXES
from .text import PROVIDER_TYPES as PROVIDER_TYPES
from .text import CustomEmbeddingProvider as CustomEmbeddingProvider
from .text import FallbackTextEmbedder as FallbackTextEmbedder
from .text import HashEmbeddingProvider as HashEmbeddingProvider
from .text import HuggingFaceEmbeddingProvider as HuggingFaceEmbeddingProvider
from .text import MLXEmbeddingProvider as MLXEmbeddingProvider
from .text import OllamaEmbeddingProvider as OllamaEmbeddingProvider
from .text import OpenAICompatibleEmbeddingProvider as OpenAICompatibleEmbeddingProvider
from .text import ResolvedEmbedder as ResolvedEmbedder
from .text import _as_float_list as _as_float_list
from .text import build_embedding_provider as build_embedding_provider
from .text import resolve_embedder as resolve_embedder
from .vision import _KNOWN_VISION_DIMS as _KNOWN_VISION_DIMS
from .vision import DEFAULT_VISION_DIM as DEFAULT_VISION_DIM
from .vision import VISION_PROVIDER_TYPES as VISION_PROVIDER_TYPES
from .vision import VISION_SPACE_IMAGE as VISION_SPACE_IMAGE
from .vision import VISION_SPACE_SHARED as VISION_SPACE_SHARED
from .vision import VISION_SPACES as VISION_SPACES
from .vision import VISION_TARGET_ENV as VISION_TARGET_ENV
from .vision import CustomVisionEmbeddingProvider as CustomVisionEmbeddingProvider
from .vision import MLXVisionEmbeddingProvider as MLXVisionEmbeddingProvider
from .vision import ResolvedVisionEmbedder as ResolvedVisionEmbedder
from .vision import VisionEmbeddingProvider as VisionEmbeddingProvider
from .vision import _guess_vision_dim as _guess_vision_dim
from .vision import _normalize_space as _normalize_space
from .vision import build_vision_provider as build_vision_provider
from .vision import resolve_vision_embedder as resolve_vision_embedder

__all__ = [
    "AUTODETECT_ENV",
    "AUTO_PROVIDER",
    "DEFAULT_HF_EMBEDDING_MODEL",
    "EMBEDDINGGEMMA_PREFIXES",
    "LOCAL_HF_MODELS",
    "LOCAL_MLX_MODELS",
    "RECOMMENDED_EMBEDDING_MODEL",
    "RECOMMENDED_EMBEDDING_PROFILE",
    "Detection",
    "detect_embedder",
    "detect_local_hf",
    "detect_local_mlx",
    "detect_ollama",
    "resolve_auto_provider",
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
    "EmbeddingIndexContract",
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
    "DEFAULT_FALLBACK_DIM",
    "FallbackTextEmbedder",
    "HashEmbeddingProvider",
    "HuggingFaceEmbeddingProvider",
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
