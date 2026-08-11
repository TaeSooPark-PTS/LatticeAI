"""
LLM Router — mlx-vlm 기반 Gemma 4 최적화 및 추측 디코딩(Speculative Decoding) 코어

v11.3.0 turned this module into a package. :class:`LLMRouter` is composed from
four cohesive mixins, each of which moved here verbatim:

* :mod:`.loading` — the guarded optional backends and every method that reads
  them (``load_model``, ``_load_cloud_model``, ``_release_memory``);
* :mod:`.registry` — the locked model registry, eviction, and the immutable
  request-scoped snapshot generation runs against;
* :mod:`.generation` — chat generation and streaming, local and cloud;
* :mod:`.documents` — the same backends driven by a caller-supplied system
  prompt.

Around them: :mod:`.branding` (system prompt + legacy-alias rewrite),
:mod:`.errors` (the typed mid-stream failure), :mod:`.catalog` (model refs and
provenance), :mod:`.local_models` (finding a downloaded model on disk).

Every name this module exported still resolves from
``latticeai.models.router`` — with one deliberate exception, spelled out
because it is the whole reason the loading half is one module:

    ``mx`` / ``vlm_load`` / ``lm_load`` / ``VLM_AVAILABLE`` / ``LM_AVAILABLE``
    are **rebound at runtime** by :func:`ensure_mlx_runtime` after an installer
    has run. Re-exporting them here would publish the import-time value
    forever, so ``ensure_mlx_runtime`` would appear to do nothing. Read them —
    and stand in for them — on ``latticeai.models.router.loading``, where they
    live.

Stubbing note, same shape: a name rebound *here* changes only this module's
binding. The submodule that calls it holds its own, so a test standing in for
a collaborator patches the submodule that reads it.
"""

# The catalog data lives in .model_providers; re-exported here so
# ``from latticeai.models.router import OPENAI_COMPATIBLE_PROVIDERS`` (and the
# model_runtime re-export chain) resolve unchanged after the split.
from latticeai.core.quiet import quiet as quiet
from latticeai.models.model_providers import (
    MODEL_SOURCE_BY_FAMILY as MODEL_SOURCE_BY_FAMILY,
)
from latticeai.models.model_providers import (
    OPENAI_COMPATIBLE_PROVIDERS as OPENAI_COMPATIBLE_PROVIDERS,
)
from latticeai.models.model_providers import (
    PROVIDER_MODEL_CATALOG as PROVIDER_MODEL_CATALOG,
)

from .branding import BRAND_NAME as BRAND_NAME
from .branding import CITATION_INSTRUCTION as CITATION_INSTRUCTION
from .branding import LEGACY_BRAND_PATTERNS as LEGACY_BRAND_PATTERNS
from .branding import SYSTEM_PROMPT as SYSTEM_PROMPT
from .branding import _compose_system as _compose_system
from .branding import normalize_branding as normalize_branding
from .catalog import CloudModel as CloudModel
from .catalog import parse_model_ref as parse_model_ref
from .catalog import source_metadata_for_model as source_metadata_for_model
from .documents import _DocumentMixin
from .errors import ModelStreamError as ModelStreamError
from .errors import _stream_failure as _stream_failure
from .generation import _GenerationMixin

# ``AsyncOpenAI`` and ``executor`` are bound once at import and never rebound,
# so a re-export is the same object the loading half uses — unlike the five MLX
# names named in the module docstring.
from .loading import AsyncOpenAI as AsyncOpenAI
from .loading import _LoadingMixin
from .loading import _mlx_sampler as _mlx_sampler
from .loading import ensure_mlx_runtime as ensure_mlx_runtime
from .loading import executor as executor
from .local_models import HF_MODELS_ROOT as HF_MODELS_ROOT
from .local_models import _is_gemma4_model_id as _is_gemma4_model_id
from .local_models import _local_model_type as _local_model_type
from .local_models import _looks_like_hf_model_dir as _looks_like_hf_model_dir
from .local_models import _resolve_local_hf_model as _resolve_local_hf_model
from .local_models import hf_cache_model_dir as hf_cache_model_dir
from .local_models import hf_model_dir as hf_model_dir
from .registry import _RegistryMixin


class LLMRouter(_LoadingMixin, _RegistryMixin, _GenerationMixin, _DocumentMixin):
    """The multi-engine router, composed from its four cohesive halves.

    The mixins define disjoint method sets, so resolution order changes nothing
    at runtime: this class exposes exactly the methods it exposed when they all
    lived in one 1,007-line module. ``__init__`` comes from the registry half,
    which owns the state the other three read.
    """
