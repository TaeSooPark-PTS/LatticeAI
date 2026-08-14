"""Model runtime and provider helpers for Lattice AI.

This module owns local/cloud model preparation, engine detection, model download,
provider-specific server startup, smoke tests, and runtime feature payloads. It is
configured by ``server_app`` with app-level state but has no FastAPI app import.

Split into cohesive submodules in v11.3.0 (no behaviour change): ``state`` (the
immutable ``ModelRuntimeState`` and the consent gates), ``engines`` (engine
wrappers + the LM Studio client), ``download`` (Hugging Face readiness and
fetch), ``status`` (``engine_status`` / ``runtime_features`` / ``install_engine``),
``loading`` (identity resolution and the load entrypoints) and ``service`` (the
bound ``ModelRuntimeService``). This module
re-exports every name the single file exposed, so
``latticeai.services.model_runtime.X`` keeps working — including the private
names ``model_loading._get_model_runtime_deps`` imports.

Two deliberate omissions: ``engines._LMSTUDIO_MODELS_CACHE`` and
``_LMSTUDIO_MODELS_CACHE_TS`` are *rebound* by ``get_lmstudio_models``, so a
re-export here would be a snapshot frozen at import time. They stay reachable
at ``latticeai.services.model_runtime.engines``, which is where the live values
are.

Stubbing note: rebinding a name *here* changes only this module's name — the
lazy ``from latticeai.services.model_runtime import …`` calls in
``model_engines`` and ``model_loading`` read it, but a submodule that uses the
name holds its own reference. A test standing in for a helper patches the
submodule that uses it.
"""

from __future__ import annotations

# The single file had no ``__all__``, so its public surface was "every module
# global" — including the names it imported for its own use. Every re-export
# below therefore uses the redundant-alias form: it reproduces exactly that
# surface, and it marks each name as deliberate rather than a leftover import.
from latticeai.core.quiet import quiet as quiet
from latticeai.models.router import HF_MODELS_ROOT as HF_MODELS_ROOT
from latticeai.models.router import (
    OPENAI_COMPATIBLE_PROVIDERS as OPENAI_COMPATIBLE_PROVIDERS,
)
from latticeai.models.router import AsyncOpenAI as AsyncOpenAI
from latticeai.models.router import ensure_mlx_runtime as ensure_mlx_runtime
from latticeai.models.router import hf_cache_model_dir as hf_cache_model_dir
from latticeai.models.router import hf_model_dir as hf_model_dir
from latticeai.models.router import parse_model_ref as parse_model_ref

# Catalog data + version-dedup helpers live in ``model_catalog``; re-exported
# here so existing ``from ...model_runtime import ENGINE_MODEL_CATALOG`` imports
# keep working.
from latticeai.services.model_catalog import (
    _VERSIONED_MODEL_PATTERNS as _VERSIONED_MODEL_PATTERNS,
)
from latticeai.services.model_catalog import (
    ENGINE_INSTALLERS as ENGINE_INSTALLERS,
)
from latticeai.services.model_catalog import (
    ENGINE_MODEL_CATALOG as ENGINE_MODEL_CATALOG,
)
from latticeai.services.model_catalog import (
    MODEL_ENGINE_ALIASES as MODEL_ENGINE_ALIASES,
)
from latticeai.services.model_catalog import (
    _model_family_version as _model_family_version,
)
from latticeai.services.model_catalog import (
    _version_tuple as _version_tuple,
)
from latticeai.services.model_catalog import (
    filter_lower_family_versions as filter_lower_family_versions,
)
from latticeai.services.model_errors import ModelRuntimeError as ModelRuntimeError
from latticeai.services.model_runtime.download import (
    download_hf_model as download_hf_model,
)
from latticeai.services.model_runtime.download import (
    estimate_eta_seconds as estimate_eta_seconds,
)
from latticeai.services.model_runtime.download import (
    hf_model_ready as hf_model_ready,
)
from latticeai.services.model_runtime.download import (
    hf_repo_files_with_sizes as hf_repo_files_with_sizes,
)
from latticeai.services.model_runtime.download import (
    model_download_progress_payload as model_download_progress_payload,
)
from latticeai.services.model_runtime.engines import (
    _LMSTUDIO_MODELS_CACHE_TTL as _LMSTUDIO_MODELS_CACHE_TTL,
)

# The private aliases the historical module bound for its own use — including
# the ones ``model_loading._get_model_runtime_deps`` imports by name. They are
# re-exported from the submodule that already binds them under exactly these
# names, which is ruff's redundant-alias re-export form.
from latticeai.services.model_runtime.engines import (
    _LOCAL_SERVER_PROCESSES as _LOCAL_SERVER_PROCESSES,
)
from latticeai.services.model_runtime.engines import (
    LOCAL_SERVER_PROCESSES as LOCAL_SERVER_PROCESSES,
)
from latticeai.services.model_runtime.engines import (
    _engine_install_plan as _engine_install_plan,
)
from latticeai.services.model_runtime.engines import (
    _find_lmstudio_model_key as _find_lmstudio_model_key,
)
from latticeai.services.model_runtime.engines import (
    _json_request as _json_request,
)
from latticeai.services.model_runtime.engines import (
    _lmstudio_candidate_keys as _lmstudio_candidate_keys,
)
from latticeai.services.model_runtime.engines import (
    _safe_engine_install_plan as _safe_engine_install_plan,
)
from latticeai.services.model_runtime.engines import (
    engine_installed as engine_installed,
)
from latticeai.services.model_runtime.engines import (
    engine_support_status as engine_support_status,
)
from latticeai.services.model_runtime.engines import (
    ensure_llamacpp_server as ensure_llamacpp_server,
)
from latticeai.services.model_runtime.engines import (
    ensure_lmstudio_model as ensure_lmstudio_model,
)
from latticeai.services.model_runtime.engines import (
    ensure_lmstudio_server as ensure_lmstudio_server,
)
from latticeai.services.model_runtime.engines import (
    ensure_ollama_server as ensure_ollama_server,
)
from latticeai.services.model_runtime.engines import (
    ensure_vllm_server as ensure_vllm_server,
)
from latticeai.services.model_runtime.engines import (
    find_lmstudio_cli as find_lmstudio_cli,
)
from latticeai.services.model_runtime.engines import (
    get_lmstudio_models as get_lmstudio_models,
)
from latticeai.services.model_runtime.engines import (
    get_ollama_pulled_models as get_ollama_pulled_models,
)
from latticeai.services.model_runtime.engines import (
    get_openai_compatible_server_models as get_openai_compatible_server_models,
)
from latticeai.services.model_runtime.engines import (
    lmstudio_api_base as lmstudio_api_base,
)
from latticeai.services.model_runtime.engines import (
    lmstudio_native_api_base as lmstudio_native_api_base,
)
from latticeai.services.model_runtime.engines import (
    local_binary as local_binary,
)
from latticeai.services.model_runtime.engines import (
    pull_ollama_model_with_progress as pull_ollama_model_with_progress,
)
from latticeai.services.model_runtime.engines import (
    vllm_executable as vllm_executable,
)
from latticeai.services.model_runtime.engines import (
    vllm_metal_python as vllm_metal_python,
)
from latticeai.services.model_runtime.engines import (
    wait_for_openai_compatible_server as wait_for_openai_compatible_server,
)
from latticeai.services.model_runtime.engines import (
    windows_binary_candidates as windows_binary_candidates,
)
from latticeai.services.model_runtime.loading import (
    _LOCAL_SMOKE_ENGINES as _LOCAL_SMOKE_ENGINES,
)
from latticeai.services.model_runtime.loading import (
    _ModelResolution as _ModelResolution,
)
from latticeai.services.model_runtime.loading import (
    _resolve_model_alias as _resolve_model_alias,
)
from latticeai.services.model_runtime.loading import (
    _smoke_test_loaded_model as _smoke_test_loaded_model,
)
from latticeai.services.model_runtime.loading import (
    build_model_resolution as build_model_resolution,
)
from latticeai.services.model_runtime.loading import (
    ensure_engine_ready as ensure_engine_ready,
)
from latticeai.services.model_runtime.loading import (
    normalize_local_model_request as normalize_local_model_request,
)
from latticeai.services.model_runtime.loading import (
    prepare_and_load_model as prepare_and_load_model,
)
from latticeai.services.model_runtime.loading import (
    prepare_and_load_model_stream as prepare_and_load_model_stream,
)
from latticeai.services.model_runtime.loading import (
    sse_event as sse_event,
)
from latticeai.services.model_runtime.service import (
    ModelRuntimeService as ModelRuntimeService,
)
from latticeai.services.model_runtime.service import (
    build_model_runtime as build_model_runtime,
)
from latticeai.services.model_runtime.service import (
    configure_model_runtime as configure_model_runtime,
)
from latticeai.services.model_runtime.state import (
    _MODEL_LOADING_COMPAT_EXPORTS as _MODEL_LOADING_COMPAT_EXPORTS,
)
from latticeai.services.model_runtime.state import (
    _SMOKE_PROMPT as _SMOKE_PROMPT,
)
from latticeai.services.model_runtime.state import (
    ModelRuntimeState as ModelRuntimeState,
)
from latticeai.services.model_runtime.state import (
    _download_allowed as _download_allowed,
)
from latticeai.services.model_runtime.state import (
    _download_block as _download_block,
)
from latticeai.services.model_runtime.state import (
    _engine_install_block as _engine_install_block,
)
from latticeai.services.model_runtime.state import (
    _friendly_model_runtime_error as _friendly_model_runtime_error,
)
from latticeai.services.model_runtime.state import (
    _missing_current_user as _missing_current_user,
)
from latticeai.services.model_runtime.state import (
    _missing_user_api_key as _missing_user_api_key,
)
from latticeai.services.model_runtime.state import (
    _model_runtime_compatibility as _model_runtime_compatibility,
)
from latticeai.services.model_runtime.state import (
    create_model_runtime_state as create_model_runtime_state,
)
from latticeai.services.model_runtime.status import (
    _install_engine as _install_engine,
)
from latticeai.services.model_runtime.status import (
    engine_status as engine_status,
)
from latticeai.services.model_runtime.status import (
    install_engine as install_engine,
)
from latticeai.services.model_runtime.status import (
    runtime_features as runtime_features,
)
