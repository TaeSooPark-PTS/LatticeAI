"""The optional backends, and everything that loads a model through them.

Two things live here together, deliberately. First, the module-level runtime
bindings — ``mx`` / ``vlm_load`` / ``lm_load`` / ``VLM_AVAILABLE`` /
``LM_AVAILABLE`` / ``AsyncOpenAI`` / ``executor`` — which are guarded imports
that ``ensure_mlx_runtime`` **rebinds** after an installer has run. Second,
every method that reads them: ``load_model``, ``_load_cloud_model`` and
``_release_memory``.

They are one module because a rebindable global is only ever correct where it
is defined: a sibling module that did ``from .loading import mx`` would hold
the import-time value forever, and ``ensure_mlx_runtime`` would appear to do
nothing. For the same reason a test standing in for a backend patches
``latticeai.models.router.loading``, not the package.
"""

import os

# Default Gemma 4 assistant drafting to MTP without overriding an operator's
# explicit MLX runtime choice.
os.environ.setdefault("MLX_VLM_DRAFT_KIND", "mtp")

import asyncio
import gc
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from latticeai.core.quiet import quiet
from latticeai.models.model_providers import (
    OPENAI_COMPATIBLE_PROVIDERS,
    PROVIDER_MODEL_CATALOG,
)

from ._contract import RouterCore as _Core
from .catalog import CloudModel, parse_model_ref, source_metadata_for_model
from .local_models import (
    _is_gemma4_model_id,
    _local_model_type,
    _resolve_local_hf_model,
)

# Optional dependencies. Each is aliased on import and then re-exported as
# `Any`, so "installed" and "absent" are the same declared type and every
# call site keeps its historical name.
try:
    from openai import AsyncOpenAI as _AsyncOpenAI
except Exception:
    _AsyncOpenAI = None  # type: ignore[assignment,misc]
AsyncOpenAI: Any = _AsyncOpenAI


# 추론 전용 싱글 스레드 워커 (GPU 스트림 보호용)
executor = ThreadPoolExecutor(max_workers=1)


try:
    import mlx.core as _mx
except Exception as e:
    _mx = None  # type: ignore[assignment]
    print(f"⚠️ MLX core unavailable: {e}")
mx: Any = _mx


try:
    from mlx_vlm import load as _vlm_load
    VLM_AVAILABLE = True
    print("✅ MLX-VLM is ready for multimodal models.")
except Exception as e:
    _vlm_load = None  # type: ignore[assignment]
    VLM_AVAILABLE = False
    print(f"⚠️ MLX-VLM unavailable: {e}")
vlm_load: Any = _vlm_load


try:
    from mlx_lm import load as _lm_load
    LM_AVAILABLE = True
    print("✅ MLX-LM is ready for text fallback models.")
except Exception as e:
    _lm_load = None  # type: ignore[assignment]
    LM_AVAILABLE = False
    print(f"⚠️ MLX-LM unavailable: {e}")
lm_load: Any = _lm_load


def ensure_mlx_runtime() -> None:
    global mx, vlm_load, lm_load, VLM_AVAILABLE, LM_AVAILABLE
    if mx is not None and (vlm_load is not None or lm_load is not None):
        return
    errors = []
    try:
        import mlx.core as mlx_core
        mx = mlx_core
        mx.set_default_device(mx.gpu)
    except Exception as e:
        errors.append(f"mlx: {e}")
        mx = None

    try:
        from mlx_vlm import load as mlx_vlm_load
        vlm_load = mlx_vlm_load
        VLM_AVAILABLE = True
    except Exception as e:
        vlm_load = None
        VLM_AVAILABLE = False
        errors.append(f"mlx-vlm: {e}")

    try:
        from mlx_lm import load as mlx_lm_load
        lm_load = mlx_lm_load
        LM_AVAILABLE = True
    except Exception as e:
        lm_load = None
        LM_AVAILABLE = False
        errors.append(f"mlx-lm: {e}")

    if mx is None or (vlm_load is None and lm_load is None):
        raise RuntimeError(f"MLX runtime is not available after install: {'; '.join(errors)}")


def _mlx_sampler(temperature: float):
    """Build an MLX sampler callable for the given temperature.

    Until v11.9.0 this discarded its argument and returned ``None``, which let
    MLX pick its own default sampler. The cost was invisible and large: the
    agent loop asks for 0.1 when it wants a tool call and 0.0 on the strict
    verification re-ask, and **locally neither did anything**. A cloud model got
    the deterministic re-ask it was designed around; the 2B model on this
    machine got the same creative sampler it had on the first attempt, and the
    "one strict retry" was one more roll of the same dice.

    ``mlx_lm.sample_utils.make_sampler`` is the sampler both backends accept —
    ``mlx_vlm`` takes the callable straight through — and it is already an
    installed dependency of the text path. If it cannot be imported (an
    MLX-VLM-only install, a version that moved it) the answer is the old one:
    ``None``, the backend's default. A missing sampler must never be a failed
    generation.
    """
    try:
        from mlx_lm.sample_utils import make_sampler
    except Exception:  # noqa: BLE001 — an absent sampler is not a failure
        quiet("MLX sampler unavailable; using the backend default")
        return None
    try:
        return make_sampler(temp=float(temperature))
    except Exception:  # noqa: BLE001 — a signature change must not stop a run
        quiet("MLX sampler construction failed; using the backend default")
        return None


def apply_stop_strings(text: str, stop: Optional[List[str]]) -> str:
    """Cut ``text`` at the earliest stop string, if any is present.

    Shared by the buffered and streaming local paths so "where does this reply
    end" has one answer. Empty stop strings are ignored: a caller that sends
    ``""`` means "no stop", not "stop before the first character".
    """
    if not stop:
        return text
    cut = min(
        (text.index(marker) for marker in stop if marker and marker in text),
        default=-1,
    )
    return text if cut < 0 else text[:cut]


class _LoadingMixin(_Core):
    """Loading, unloading memory, and enumerating what could be loaded."""

    def _release_memory(self) -> None:
        gc.collect()
        if mx is not None and hasattr(mx, "clear_cache"):
            try:
                mx.clear_cache()
            except Exception as e:
                print(f"⚠️ MLX cache clear skipped: {e}")

    async def load_model(
        self,
        model_id: str,
        adapter_path: Optional[str] = None,
        draft_model_id: Optional[str] = None,
        api_key_override: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> str:
        provider, provider_model = parse_model_ref(model_id)
        if provider != "local_mlx":
            return self._load_cloud_model(provider, provider_model, api_key_override=api_key_override, owner=owner)

        ensure_mlx_runtime()
        if mx is None or (vlm_load is None and lm_load is None):
            raise RuntimeError("MLX is not available in this process. Run on Apple Silicon with Metal access.")

        cache_key = f"{model_id}_{draft_model_id}" if draft_model_id else model_id
        with self._lock:
            if cache_key in self._cache:
                self._current = cache_key
                self._touch(cache_key)
                return f"Cached: {cache_key}"

        self._enforce_local_model_limit(cache_key)
        print(f"⏳ Loading local model stack: {cache_key}...")
        loop = asyncio.get_event_loop()
        target_model_id = _resolve_local_hf_model(model_id)
        target_draft_model_id = _resolve_local_hf_model(draft_model_id) if draft_model_id else None
        
        def _load():
            mx.set_default_device(mx.gpu)
            is_gemma4 = _is_gemma4_model_id(model_id)
            model_type = _local_model_type(target_model_id) or _local_model_type(model_id)
            loader_kind = "mlx_vlm"

            try:
                if vlm_load is None:
                    raise RuntimeError("MLX-VLM is not installed.")
                print(f"🔄 Loading Target (VLM Mode): {target_model_id}...")
                model, tokenizer = vlm_load(target_model_id)
            except Exception as vlm_error:
                if not (is_gemma4 and model_type != "gemma4_unified" and lm_load is not None):
                    raise
                print(f"⚠️ Gemma 4 MLX-VLM load failed; retrying MLX-LM text path: {vlm_error}")
                print(f"🔄 Loading Target (LM Mode): {target_model_id}...")
                model, tokenizer = lm_load(target_model_id)
                loader_kind = "mlx_lm"

            draft_model = None
            if target_draft_model_id:
                if loader_kind == "mlx_vlm":
                    print(f"🔄 Loading Assistant (VLM Mode): {target_draft_model_id}...")
                    draft_model, _ = vlm_load(target_draft_model_id)
                elif lm_load is not None:
                    print(f"🔄 Loading Assistant (LM Mode): {target_draft_model_id}...")
                    draft_model, _ = lm_load(target_draft_model_id)
                print("✅ Assistant Ready.")

            return model, tokenizer, draft_model, loader_kind

        try:
            # Use the dedicated single-thread executor to ensure MLX GPU streams match during inference
            model, tokenizer, draft_model, loader_kind = await loop.run_in_executor(executor, _load)
            with self._lock:
                self._cache[cache_key] = (model, tokenizer, draft_model, loader_kind)
                self._current = cache_key
                self._touch(cache_key)
            print(f"✅ Fully Loaded: {cache_key} ({loader_kind})")
            return f"Success: {cache_key} ({loader_kind})"
        except Exception as e:
            print(f"❌ Load Error: {e}")
            raise e

    def _load_cloud_model(self, provider: str, model: str, api_key_override: Optional[str] = None, owner: Optional[str] = None) -> str:
        if AsyncOpenAI is None:
            raise RuntimeError("openai package is not installed. Add it to requirements.txt and install dependencies.")
        config = OPENAI_COMPATIBLE_PROVIDERS.get(provider)
        if not config:
            raise RuntimeError(f"Unsupported cloud provider: {provider}")

        api_key = api_key_override or os.getenv(config["env_key"]) or config.get("api_key_fallback")
        if not api_key:
            raise RuntimeError(f"Missing API key env var: {config['env_key']}")

        base_url = os.getenv(config.get("base_url_env", "")) if config.get("base_url_env") else None
        base_url = base_url or config.get("base_url")
        # base_url is passed only when configured: an explicit None is not
        # the same as omitting the argument.
        client = (
            AsyncOpenAI(api_key=api_key, base_url=base_url)
            if base_url
            else AsyncOpenAI(api_key=api_key)
        )

        cache_owner = owner or "global"
        cache_key = f"{provider}:{model}::{cache_owner}"
        with self._lock:
            self._cache[cache_key] = CloudModel(
                provider=provider, model=model, client=client, cache_key=cache_key
            )
            self._current = cache_key
            self._touch(cache_key)
        return f"Cloud provider ready: {cache_key}"

    def detected_cloud_models(self) -> List[Dict[str, str]]:
        local_server_providers = {"ollama", "vllm", "lmstudio", "llamacpp"}
        items: List[Dict[str, Any]] = []
        for provider, config in OPENAI_COMPATIBLE_PROVIDERS.items():
            has_key = bool(os.getenv(config["env_key"]) or config.get("api_key_fallback"))
            provider_models = PROVIDER_MODEL_CATALOG.get(provider) or [{
                "id": config["default_model"],
                "name": f"{provider.title()} · {config['default_model']}",
                "family": provider.title(),
            }]
            for model in provider_models:
                model_id = model["id"]
                local_server = provider in local_server_providers
                items.append({
                    "id": f"{provider}:{model_id}",
                    "name": model.get("name") or f"{provider.title()} · {model_id}",
                    "provider": provider,
                    "family": model.get("family"),
                    "tag": "local-server" if local_server else "cloud",
                    "available": has_key,
                    "requires": config["env_key"] if not has_key else None,
                    **source_metadata_for_model(provider, model, local_server=local_server),
                })
        custom = os.getenv("LATTICEAI_CLOUD_MODELS") or ""
        for raw in [item.strip() for item in custom.split(",") if item.strip()]:
            provider, custom_model = parse_model_ref(raw)
            if provider != "local_mlx" and provider in OPENAI_COMPATIBLE_PROVIDERS:
                config = OPENAI_COMPATIBLE_PROVIDERS[provider]
                items.append({
                    "id": f"{provider}:{custom_model}",
                    "name": f"{provider.title()} · {custom_model}",
                    "provider": provider,
                    "tag": "cloud",
                    "available": bool(os.getenv(config["env_key"]) or config.get("api_key_fallback")),
                    "requires": None,
                    **source_metadata_for_model(
                        provider,
                        {
                            "id": custom_model,
                            "name": f"{provider.title()} · {custom_model}",
                            "family": provider.title(),
                        },
                        local_server=provider in local_server_providers,
                    ),
                })
        return items
