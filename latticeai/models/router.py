"""
LLM Router — mlx-vlm 기반 Gemma 4 최적화 및 추측 디코딩(Speculative Decoding) 코어
"""

import asyncio
import base64
import gc
import io
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

# Set MLX_VLM_DRAFT_KIND to 'mtp' to enable the Gemma 4 assistant MTP drafter.
os.environ["MLX_VLM_DRAFT_KIND"] = "mtp"

from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Dict, Optional, Tuple, List
from PIL import Image

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

# 추론 전용 싱글 스레드 워커 (GPU 스트림 보호용)
executor = ThreadPoolExecutor(max_workers=1)

try:
    import mlx.core as mx
except Exception as e:
    mx = None
    print(f"⚠️ MLX core unavailable: {e}")

try:
    from mlx_vlm import load as vlm_load
    VLM_AVAILABLE = True
    print("✅ MLX-VLM is ready for multimodal models.")
except Exception as e:
    vlm_load = None
    VLM_AVAILABLE = False
    print(f"⚠️ MLX-VLM unavailable: {e}")

try:
    from mlx_lm import load as lm_load
    LM_AVAILABLE = True
    print("✅ MLX-LM is ready for text fallback models.")
except Exception as e:
    lm_load = None
    LM_AVAILABLE = False
    print(f"⚠️ MLX-LM unavailable: {e}")

BRAND_NAME = "Lattice AI"
LEGACY_BRAND_PATTERNS = [
    (re.compile(r"\bconnect\s+ai\b", re.IGNORECASE), BRAND_NAME),
    (re.compile(r"\bconnect-ai\b", re.IGNORECASE), BRAND_NAME),
    (re.compile(r"\bconnectai\b", re.IGNORECASE), BRAND_NAME),
    (re.compile(r"커넥트\s*AI", re.IGNORECASE), BRAND_NAME),
]

SYSTEM_PROMPT = """You are Lattice AI, a powerful local AI assistant running on Apple Silicon.
Your product name and identity are Lattice AI.
Never identify yourself as Connect AI, ConnectAI, connect-ai, or 커넥트 AI.
If context or old chat history mentions those names, treat them only as legacy aliases for Lattice AI.
You are a Vision-Language Model (VLM). If an image is provided, analyze it.
Be concise and respond in the user's language."""

def normalize_branding(text: Optional[str]) -> str:
    if not text:
        return ""
    normalized = str(text)
    for pattern, replacement in LEGACY_BRAND_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    return normalized

OPENAI_COMPATIBLE_PROVIDERS = {
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "default_model": "gpt-4o-mini",
    },
    "openrouter": {
        "env_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
    },
    "groq": {
        "env_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "meta-llama/llama-4-scout-17b-16e-instruct",
    },
    "together": {
        "env_key": "TOGETHER_API_KEY",
        "base_url": "https://api.together.xyz/v1",
        "default_model": "Qwen/Qwen3-VL-32B-Instruct",
    },
    "xai": {
        "env_key": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-beta",
    },
    "ollama": {
        "env_key": "OLLAMA_API_KEY",
        "base_url_env": "OLLAMA_BASE_URL",
        "base_url": "http://localhost:11434/v1",
        "default_model": "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
        "api_key_fallback": "ollama",
    },
    "vllm": {
        "env_key": "VLLM_API_KEY",
        "base_url_env": "VLLM_BASE_URL",
        "base_url": "http://localhost:8000/v1",
        "default_model": "Qwen/Qwen3-VL-8B-Instruct",
        "api_key_fallback": "vllm",
    },
    "lmstudio": {
        "env_key": "LMSTUDIO_API_KEY",
        "base_url_env": "LMSTUDIO_BASE_URL",
        "base_url": "http://localhost:1234/v1",
        "default_model": "local-model",
        "api_key_fallback": "lmstudio",
    },
    "llamacpp": {
        "env_key": "LLAMACPP_API_KEY",
        "base_url_env": "LLAMACPP_BASE_URL",
        "base_url": "http://localhost:8080/v1",
        "default_model": "llama.cpp-model",
        "api_key_fallback": "llamacpp",
    },
}

PROVIDER_MODEL_CATALOG = {
    "openai": [
        {"id": "gpt-5.5", "name": "GPT-5.5", "family": "GPT"},
        {"id": "gpt-5.4", "name": "GPT-5.4", "family": "GPT"},
        {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "family": "GPT"},
        {"id": "gpt-5.4-nano", "name": "GPT-5.4 Nano", "family": "GPT"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "family": "GPT"},
        {"id": "gpt-4o", "name": "GPT-4o", "family": "GPT"},
        {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini", "family": "GPT"},
        {"id": "gpt-4.1", "name": "GPT-4.1", "family": "GPT"},
    ],
    "openrouter": [
        {"id": "openai/gpt-5.5", "name": "GPT-5.5 via OpenRouter", "family": "GPT"},
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini via OpenRouter", "family": "GPT"},
        {"id": "anthropic/claude-opus-4.7", "name": "Claude Opus 4.7 via OpenRouter", "family": "Claude"},
        {"id": "anthropic/claude-sonnet-4.6", "name": "Claude Sonnet 4.6 via OpenRouter", "family": "Claude"},
        {"id": "anthropic/claude-haiku-4.5", "name": "Claude Haiku 4.5 via OpenRouter", "family": "Claude"},
        {"id": "qwen/qwen3-vl-235b-a22b-instruct", "name": "Qwen3-VL 235B A22B via OpenRouter", "family": "Qwen"},
        {"id": "google/gemma-4-12b-it", "name": "Gemma 4 12B via OpenRouter", "family": "Gemma"},
        {"id": "x-ai/grok-2", "name": "Grok 2 via OpenRouter", "family": "Grok"},
        {"id": "meta-llama/llama-4-scout-17b-16e-instruct", "name": "Llama 4 Scout via OpenRouter", "family": "Llama"},
        {"id": "google/gemini-2.5-flash", "name": "Gemini 2.5 Flash via OpenRouter", "family": "Gemini"},
    ],
    "groq": [
        {"id": "meta-llama/llama-4-scout-17b-16e-instruct", "name": "Llama 4 Scout", "family": "Llama"},
    ],
    "together": [
        {"id": "Qwen/Qwen3-VL-32B-Instruct", "name": "Qwen3-VL 32B", "family": "Qwen"},
        {"id": "google/gemma-4-12b-it", "name": "Gemma 4 12B", "family": "Gemma"},
        {"id": "meta-llama/Llama-4-Scout-17B-16E-Instruct", "name": "Llama 4 Scout", "family": "Llama"},
    ],
    "xai": [
        {"id": "grok-beta", "name": "Grok Beta", "family": "Grok"},
        {"id": "grok-vision-beta", "name": "Grok Vision Beta", "family": "Grok"},
    ],
}

MODEL_SOURCE_BY_FAMILY = {
    "GPT": ("미국", "OpenAI"),
    "Claude": ("미국", "Anthropic"),
    "Qwen": ("중국", "Alibaba"),
    "Llama": ("미국", "Meta"),
    "Gemini": ("미국", "Google"),
    "Grok": ("미국", "xAI"),
}


def source_metadata_for_model(provider: str, model: Dict[str, str], *, local_server: bool) -> Dict[str, str]:
    family = str(model.get("family") or "")
    country, company = MODEL_SOURCE_BY_FAMILY.get(family, ("미상", provider.title()))
    if local_server:
        execution_method = "내 컴퓨터에서만 실행"
        internet_requirement = "모델을 다운로드할 때만 인터넷 필요; 실행 중에는 필요 없음"
    else:
        execution_method = "인터넷 연결 후 사용"
        internet_requirement = "내 파일이 인터넷으로 전송될 수 있음"
    return {
        "source_country": country,
        "source_company": company,
        "execution_method": execution_method,
        "internet_requirement": internet_requirement,
        "model_name": model.get("name") or model.get("id") or "",
        "source_display_order": [
            "source_country",
            "source_company",
            "execution_method",
            "internet_requirement",
            "model_name",
        ],
    }

@dataclass
class CloudModel:
    provider: str
    model: str
    client: object
    cache_key: str

def parse_model_ref(model_id: str) -> tuple[str, str]:
    """Return (provider, model). Unprefixed refs stay local MLX."""
    if model_id.startswith("cloud:"):
        _, provider, model = model_id.split(":", 2)
        return provider, model
    if ":" in model_id:
        provider, model = model_id.split(":", 1)
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            return provider, model
        if provider in {"local_mlx", "mlx"}:
            return "local_mlx", model
    if model_id.startswith("local_mlx:"):
        return "local_mlx", model_id.split(":", 1)[1]
    return "local_mlx", model_id

HF_MODELS_ROOT = Path.home() / ".ltcai" / "hf-models"

def hf_model_dir(repo_id: str) -> Path:
    return HF_MODELS_ROOT / repo_id.replace("/", "__")

def _looks_like_hf_model_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    has_config = (path / "config.json").exists()
    has_weights = any(path.glob("*.safetensors")) or any(path.glob("*.bin"))
    has_tokenizer = (
        (path / "tokenizer.json").exists()
        or (path / "tokenizer.model").exists()
        or (path / "tokenizer_config.json").exists()
    )
    return has_config and has_weights and has_tokenizer

def _resolve_local_hf_model(model_id: str) -> str:
    explicit_path = Path(model_id).expanduser()
    if explicit_path.exists():
        return str(explicit_path)
    local_dir = hf_model_dir(model_id)
    if _looks_like_hf_model_dir(local_dir):
        return str(local_dir)
    return model_id

def _is_gemma4_model_id(model_id: str) -> bool:
    raw = str(model_id or "").lower()
    return bool(re.search(r"gemma[-_/ ]?4|gemma4", raw))


def _local_model_type(path_or_model_id: str) -> Optional[str]:
    raw = str(path_or_model_id or "").strip()
    candidates = []
    explicit = Path(raw).expanduser()
    if raw and explicit.exists():
        candidates.append(explicit / "config.json")
    candidates.append(hf_model_dir(raw) / "config.json")
    for config_path in candidates:
        try:
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                model_type = str(data.get("model_type") or "").strip().lower()
                if model_type:
                    return model_type
        except Exception as e:
            print(f"⚠️ Model config read skipped for {config_path}: {e}")
    return None


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

    Lattice v2.2 keeps local execution on MLX-VLM only. Returning ``None`` lets
    MLX-VLM use its bundled default sampler without pulling another generation
    package into the runtime contract.
    """
    _ = temperature
    return None

class LLMRouter:
    def __init__(self):
        self._cache: Dict[str, Tuple] = {}
        self._current: Optional[str] = None
        self._last_used: Dict[str, float] = {}
        self._max_local_models = max(1, int(os.getenv("LATTICEAI_MAX_LOCAL_MODELS", "1")))

    @property
    def current_model_id(self) -> Optional[str]:
        return self._current

    @property
    def loaded_model_ids(self) -> List[str]:
        return list(self._cache.keys())

    def switch_model(self, model_id: str) -> None:
        if model_id not in self._cache:
            raise KeyError(model_id)
        self._current = model_id
        self._touch(model_id)

    def unload_model(self, model_id: str) -> None:
        self._cache.pop(model_id, None)
        self._last_used.pop(model_id, None)
        if self._current == model_id:
            self._current = next(iter(self._cache), None)
        self._release_memory()

    def unload_all(self) -> None:
        self._cache.clear()
        self._last_used.clear()
        self._current = None
        self._release_memory()

    def unload_idle_models(self, idle_seconds: int) -> List[str]:
        if idle_seconds <= 0:
            return []
        now = time.monotonic()
        unloaded = []
        for model_id, last_used in list(self._last_used.items()):
            if now - last_used >= idle_seconds:
                self.unload_model(model_id)
                unloaded.append(model_id)
        return unloaded

    def model_memory_policy(self) -> Dict[str, object]:
        return {
            "max_local_models": self._max_local_models,
            "loaded_count": len(self._cache),
            "last_used": dict(self._last_used),
        }

    def _touch(self, model_id: Optional[str] = None) -> None:
        model_id = model_id or self._current
        if model_id:
            self._last_used[model_id] = time.monotonic()

    def _is_local_model(self, model_id: str) -> bool:
        cached = self._cache.get(model_id)
        return cached is not None and not isinstance(cached, CloudModel)

    def _enforce_local_model_limit(self, incoming_key: str) -> None:
        local_ids = [model_id for model_id in self._cache if self._is_local_model(model_id)]
        while len(local_ids) >= self._max_local_models:
            victim = min(local_ids, key=lambda model_id: self._last_used.get(model_id, 0))
            if victim == incoming_key:
                break
            print(f"🧹 Unloading local model to stay within memory policy: {victim}")
            self.unload_model(victim)
            local_ids = [model_id for model_id in self._cache if self._is_local_model(model_id)]

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
        adapter_path: str = None,
        draft_model_id: str = None,
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
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        cache_owner = owner or "global"
        cache_key = f"{provider}:{model}::{cache_owner}"
        self._cache[cache_key] = CloudModel(provider=provider, model=model, client=AsyncOpenAI(**client_kwargs), cache_key=cache_key)
        self._current = cache_key
        self._touch(cache_key)
        return f"Cloud provider ready: {cache_key}"

    def detected_cloud_models(self) -> List[Dict[str, str]]:
        local_server_providers = {"ollama", "vllm", "lmstudio", "llamacpp"}
        items = []
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
            provider, model = parse_model_ref(raw)
            if provider != "local_mlx" and provider in OPENAI_COMPATIBLE_PROVIDERS:
                config = OPENAI_COMPATIBLE_PROVIDERS[provider]
                items.append({
                    "id": f"{provider}:{model}",
                    "name": f"{provider.title()} · {model}",
                    "provider": provider,
                    "tag": "cloud",
                    "available": bool(os.getenv(config["env_key"]) or config.get("api_key_fallback")),
                    "requires": None,
                    **source_metadata_for_model(
                        provider,
                        {"id": model, "name": f"{provider.title()} · {model}", "family": provider.title()},
                        local_server=provider in local_server_providers,
                    ),
                })
        return items

    def _is_cloud_current(self) -> bool:
        return bool(self._current and isinstance(self._cache.get(self._current), CloudModel))

    def _local_server_error_hint(self, cloud: CloudModel, error: Exception) -> str:
        raw = str(error)
        if cloud.provider == "lmstudio":
            base_url = os.getenv("LMSTUDIO_BASE_URL") or OPENAI_COMPATIBLE_PROVIDERS["lmstudio"]["base_url"]
            return (
                f"LM Studio 연결 실패: {raw}\n\n"
                f"- LM Studio의 Developer/Local Server를 켜고 모델을 로드했는지 확인하세요.\n"
                f"- Lattice가 보는 주소는 {base_url} 입니다. 포트가 다르면 LMSTUDIO_BASE_URL을 맞춰주세요.\n"
                f"- 모델 선택창에는 LM Studio /v1/models에서 감지된 모델만 표시됩니다."
            )
        return raw

    def _build_prompt(self, message: str, context: Optional[str], tokenizer) -> str:
        system = SYSTEM_PROMPT
        context = normalize_branding(context)
        if context:
            system += f"\n\nContext:\n{context}"
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": message}]
                return tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            except Exception:
                pass
        return f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"

    def _build_vlm_prompt(self, model, processor, message: str, context: Optional[str], num_images: int) -> str:
        system = SYSTEM_PROMPT
        context = normalize_branding(context)
        if context:
            system += f"\n\nContext:\n{context}"
        try:
            from mlx_vlm import apply_chat_template

            return apply_chat_template(
                processor,
                model.config,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ],
                add_generation_prompt=True,
                num_images=num_images,
            )
        except Exception as e:
            print(f"⚠️ VLM chat template fallback: {e}")
            return self._build_prompt(message, context, processor)

    def _unpack_local_cache(self, cached: Tuple) -> Tuple[object, object, object, str]:
        model, tokenizer, draft_model = cached[:3]
        loader_kind = str(cached[3]) if len(cached) > 3 else "mlx_vlm"
        return model, tokenizer, draft_model, loader_kind

    async def generate_as(self, model_id: str | None, message: str, context: Optional[str] = None, max_tokens: int = 4096, temperature: float = 0.2) -> str:
        """Generate using a specific model, temporarily switching if needed. Falls back to current model if model_id is None or not loaded."""
        if not model_id or model_id == self._current:
            return await self.generate(message, context, max_tokens, temperature)
        if model_id not in self._cache:
            raise ValueError(f"Model '{model_id}' is not loaded. Load it first via /models/load.")
        prev = self._current
        self._current = model_id
        try:
            return await self.generate(message, context, max_tokens, temperature)
        finally:
            self._current = prev

    async def generate(self, message: str, context: Optional[str] = None, max_tokens: int = 4096, temperature: float = 0.2, image_data: Optional[str] = None) -> str:
        if not self._current:
            return "No model."
        self._touch()
        cached = self._cache[self._current]
        if isinstance(cached, CloudModel):
            return await self._cloud_generate(cached, message, context, max_tokens, temperature)

        model, tokenizer, draft_model, loader_kind = self._unpack_local_cache(self._cache[self._current])
        use_vlm = loader_kind == "mlx_vlm"
        prompt = (
            self._build_vlm_prompt(model, tokenizer, message, context, 1 if image_data else 0)
            if use_vlm
            else self._build_prompt(message, context, tokenizer)
        )
        
        loop = asyncio.get_event_loop()
        
        def _gen():
            import mlx.core as mx
            mx.set_default_device(mx.gpu)
            if use_vlm:
                from mlx_vlm import generate as vlm_gen
                return vlm_gen(model, tokenizer, prompt=prompt, image=self._prep_image(image_data) if image_data else None, max_tokens=max_tokens, sampler=_mlx_sampler(temperature), draft_model=draft_model, draft_kind="mtp")
            from mlx_lm import generate as lm_gen
            return lm_gen(model, tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=_mlx_sampler(temperature), draft_model=draft_model)
        result = await loop.run_in_executor(executor, _gen)
        # mlx-vlm might return a GenerationResult object; extract the text
        if hasattr(result, "text"):
            return normalize_branding(result.text)
        return normalize_branding(str(result))

    async def _cloud_generate(self, cloud: CloudModel, message: str, context: Optional[str], max_tokens: int, temperature: float) -> str:
        system = SYSTEM_PROMPT
        context = normalize_branding(context)
        if context:
            system += f"\n\nContext:\n{context}"
        try:
            response = await cloud.client.chat.completions.create(
                model=cloud.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            raise RuntimeError(self._local_server_error_hint(cloud, e)) from e
        return normalize_branding(response.choices[0].message.content or "")

    async def stream_generate(self, message: str, context: Optional[str] = None, max_tokens: int = 4096, temperature: float = 0.2, image_data: Optional[str] = None) -> AsyncIterator[str]:
        if not self._current:
            yield "No model."
            return
        self._touch()
        cached = self._cache[self._current]
        if isinstance(cached, CloudModel):
            async for chunk in self._cloud_stream_generate(cached, message, context, max_tokens, temperature):
                yield chunk
            return

        model, tokenizer, draft_model, loader_kind = self._unpack_local_cache(self._cache[self._current])
        use_vlm = loader_kind == "mlx_vlm"
        prompt = (
            self._build_vlm_prompt(model, tokenizer, message, context, 1 if image_data else 0)
            if use_vlm
            else self._build_prompt(message, context, tokenizer)
        )
        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()

        def _stream():
            import mlx.core as mx
            mx.set_default_device(mx.gpu)
            try:
                if use_vlm:
                    from mlx_vlm import stream_generate as vlm_stream
                    gen = vlm_stream(model, tokenizer, prompt=prompt, image=self._prep_image(image_data) if image_data else None, max_tokens=max_tokens, sampler=_mlx_sampler(temperature), draft_model=draft_model, draft_kind="mtp")
                else:
                    from mlx_lm import stream_generate as lm_stream
                    gen = lm_stream(model, tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=_mlx_sampler(temperature), draft_model=draft_model)
                
                for chunk in gen:
                    text = chunk.text if hasattr(chunk, "text") else (chunk[0] if isinstance(chunk, tuple) else str(chunk))
                    loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, f"⚠️ Error: {e}")
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(executor, _stream)
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield normalize_branding(chunk)

    async def _cloud_stream_generate(self, cloud: CloudModel, message: str, context: Optional[str], max_tokens: int, temperature: float) -> AsyncIterator[str]:
        system = SYSTEM_PROMPT
        context = normalize_branding(context)
        if context:
            system += f"\n\nContext:\n{context}"
        try:
            stream = await cloud.client.chat.completions.create(
                model=cloud.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
        except Exception as e:
            yield f"⚠️ {self._local_server_error_hint(cloud, e)}"
            return
        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta.content
            if delta:
                yield normalize_branding(delta)

    def _prep_image(self, image_data: Optional[str]) -> Optional[Image.Image]:
        if not image_data:
            return None
        try:
            image = Image.open(io.BytesIO(base64.b64decode(image_data))).convert("RGB")
            print(f"🖼️ VLM image decoded: {image.width}x{image.height}")
            return image
        except Exception as e:
            print(f"⚠️ VLM image decode failed: {e}")
            return None

    # ── Document Generation Pipeline ──────────────────────────────────────

    async def generate_document(
        self,
        message: str,
        system_prompt: str,
        *,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> str:
        """Generate a document using a specialized system prompt with graph context."""
        if not self._current:
            return "No model loaded."
        self._touch()
        cached = self._cache[self._current]

        if isinstance(cached, CloudModel):
            return await self._cloud_generate_document(cached, message, system_prompt, max_tokens, temperature)

        model, tokenizer, draft_model, loader_kind = self._unpack_local_cache(cached)
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ]
                prompt = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            except Exception:
                prompt = f"<|im_start|>system\n{system_prompt}<|im_end|>\n<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"
        else:
            prompt = f"<|im_start|>system\n{system_prompt}<|im_end|>\n<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"

        loop = asyncio.get_event_loop()
        def _gen():
            import mlx.core as mx
            mx.set_default_device(mx.gpu)
            if loader_kind == "mlx_vlm":
                from mlx_vlm import generate as vlm_gen
                return vlm_gen(model, tokenizer, prompt=prompt, image=None, max_tokens=max_tokens, sampler=_mlx_sampler(temperature), draft_model=draft_model, draft_kind="mtp")
            from mlx_lm import generate as lm_gen
            return lm_gen(model, tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=_mlx_sampler(temperature), draft_model=draft_model)
        result = await loop.run_in_executor(executor, _gen)
        if hasattr(result, "text"):
            return normalize_branding(result.text)
        return normalize_branding(str(result))

    async def _cloud_generate_document(self, cloud: CloudModel, message: str, system_prompt: str, max_tokens: int, temperature: float) -> str:
        try:
            response = await cloud.client.chat.completions.create(
                model=cloud.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            raise RuntimeError(self._local_server_error_hint(cloud, e)) from e
        return normalize_branding(response.choices[0].message.content or "")

    async def stream_generate_document(
        self,
        message: str,
        system_prompt: str,
        *,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """Stream document generation with specialized system prompt."""
        if not self._current:
            yield "No model loaded."
            return
        self._touch()
        cached = self._cache[self._current]

        if isinstance(cached, CloudModel):
            async for chunk in self._cloud_stream_document(cached, message, system_prompt, max_tokens, temperature):
                yield chunk
            return

        model, tokenizer, draft_model, loader_kind = self._unpack_local_cache(cached)
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ]
                prompt = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            except Exception:
                prompt = f"<|im_start|>system\n{system_prompt}<|im_end|>\n<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"
        else:
            prompt = f"<|im_start|>system\n{system_prompt}<|im_end|>\n<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"

        loop = asyncio.get_event_loop()
        queue = asyncio.Queue()

        def _stream():
            import mlx.core as mx
            mx.set_default_device(mx.gpu)
            try:
                if loader_kind == "mlx_vlm":
                    from mlx_vlm import stream_generate as vlm_stream
                    gen = vlm_stream(model, tokenizer, prompt=prompt, image=None, max_tokens=max_tokens, sampler=_mlx_sampler(temperature), draft_model=draft_model, draft_kind="mtp")
                else:
                    from mlx_lm import stream_generate as lm_stream
                    gen = lm_stream(model, tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=_mlx_sampler(temperature), draft_model=draft_model)
                for chunk in gen:
                    text = chunk.text if hasattr(chunk, "text") else (chunk[0] if isinstance(chunk, tuple) else str(chunk))
                    loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, f"⚠️ Error: {e}")
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(executor, _stream)
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield normalize_branding(chunk)

    async def _cloud_stream_document(self, cloud: CloudModel, message: str, system_prompt: str, max_tokens: int, temperature: float) -> AsyncIterator[str]:
        try:
            stream = await cloud.client.chat.completions.create(
                model=cloud.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
        except Exception as e:
            yield f"⚠️ {self._local_server_error_hint(cloud, e)}"
            return
        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta.content
            if delta:
                yield normalize_branding(delta)
