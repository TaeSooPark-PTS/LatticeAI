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
    pass
