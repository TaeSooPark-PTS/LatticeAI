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

:mod:`.base` defines that :class:`EmbeddingProvider` interface; :mod:`.text`
holds the five concrete text implementations:

1. :class:`HashEmbeddingProvider`  — deterministic, offline, always-available
   fallback (wraps :class:`~lattice_brain.embeddings.LocalEmbeddingModel`).
2. :class:`MLXEmbeddingProvider`   — local Apple-Silicon embedding models.
3. :class:`OllamaEmbeddingProvider` — a local/remote Ollama server.
4. :class:`OpenAICompatibleEmbeddingProvider` — any ``/v1/embeddings`` endpoint
   (OpenAI, LM Studio, vLLM, llama.cpp, Together, …).
5. :class:`CustomEmbeddingProvider` — a user-supplied dotted callable.

:func:`resolve_embedder` builds the configured provider and, when that provider
is unavailable, degrades to the hash fallback while *reporting* the requested
vs. active provider — nothing is silently faked. :mod:`.profiles` is the named
list of supported provider/model/dimension combinations the setup surfaces
offer.

Vision seam (v11.1.0, Track 3)
------------------------------
Images join the same contract through :class:`VisionEmbeddingProvider` in
:mod:`.vision`, with two deliberate differences from the text side:

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

:class:`VisionCaptioner` in :mod:`.captions` is the matching seam for
descriptions. Its default implementation returns ``None``: a caption is what a
vision-language model said about an image, so with no VLM loaded there is no
caption — never a sentence assembled from the filename and passed off as one.

Split into these submodules in v11.3.0 with no behaviour change. Every name the
single module exposed still resolves from
``latticeai.core.embedding_providers``.

Stubbing note: a name rebound *here* changes only this module's binding — the
submodule that calls it holds its own, so a test standing in for a helper
patches the submodule that reads it.
"""

from __future__ import annotations

# The single module had no ``__all__`` restriction on what callers could reach:
# the two names it imported for its own use were part of its surface, and the
# suite imports them from here. Re-exported in the redundant-alias form so they
# read as deliberate rather than as leftover imports.
from lattice_brain.embeddings import DEFAULT_EMBEDDING_DIM as DEFAULT_EMBEDDING_DIM
from lattice_brain.embeddings import LocalEmbeddingModel as LocalEmbeddingModel

from .base import _KNOWN_DIMS as _KNOWN_DIMS
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
from .profiles import embedding_provider_profiles as embedding_provider_profiles
from .profiles import resolve_embedding_profile as resolve_embedding_profile
from .text import PROVIDER_TYPES as PROVIDER_TYPES
from .text import CustomEmbeddingProvider as CustomEmbeddingProvider
from .text import HashEmbeddingProvider as HashEmbeddingProvider
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
