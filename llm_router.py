"""
LLM Router — mlx-vlm 기반 Gemma 4 최적화 및 추측 디코딩(Speculative Decoding) 코어
"""

import asyncio
import base64
import io
import os
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Dict, Optional, Tuple, List
from PIL import Image

# 추론 전용 싱글 스레드 워커 (GPU 스트림 보호용)
executor = ThreadPoolExecutor(max_workers=1)

try:
    import mlx.core as mx
    from mlx_lm import load as lm_load
    from mlx_vlm import load as vlm_load
    VLM_AVAILABLE = True
    print("✅ MLX-VLM and MLX-LM are ready for Gemma 4.")
except ImportError:
    VLM_AVAILABLE = False
    print("⚠️ MLX libraries missing.")

SYSTEM_PROMPT = """You are Connect AI, a powerful local AI assistant running on Apple Silicon.
You are a Vision-Language Model (VLM). If an image is provided, analyze it.
Be concise and respond in the user's language."""

class LLMRouter:
    def __init__(self):
        self._cache: Dict[str, Tuple] = {}
        self._current: Optional[str] = None

    @property
    def current_model_id(self) -> Optional[str]:
        return self._current

    async def load_model(self, model_id: str, adapter_path: str = None, draft_model_id: str = None) -> str:
        cache_key = f"{model_id}_{draft_model_id}" if draft_model_id else model_id
        if cache_key in self._cache:
            self._current = cache_key
            return f"Cached: {cache_key}"

        print(f"⏳ Loading Gemma 4 Stack: {cache_key}...")
        loop = asyncio.get_event_loop()
        
        def _load():
            mx.set_default_device(mx.gpu)
            is_gemma4 = "gemma-4" in model_id.lower() or "gemma4" in model_id.lower()
            
            # 1. Target 로드 (Gemma 4는 항상 vlm_load 사용)
            if is_gemma4 and VLM_AVAILABLE:
                print(f"🔄 Loading Target (VLM Mode): {model_id}...")
                model, tokenizer = vlm_load(model_id)
            else:
                print(f"🔄 Loading Target (LM Mode): {model_id}...")
                model, tokenizer = lm_load(model_id)

            # 2. Draft 로드 (Gemma 4는 항상 vlm_load 사용)
            draft_model = None
            if draft_model_id:
                print(f"🔄 Loading Assistant (VLM Mode): {draft_model_id}...")
                if is_gemma4 and VLM_AVAILABLE:
                    draft_model, _ = vlm_load(draft_model_id)
                else:
                    draft_model, _ = lm_load(draft_model_id)
                print(f"✅ Assistant Ready.")

            return model, tokenizer, draft_model

        try:
            # 기본 executor(None)를 사용하여 로딩 중 메인 루프 차단 방지
            model, tokenizer, draft_model = await loop.run_in_executor(None, _load)
            self._cache[cache_key] = (model, tokenizer, draft_model)
            self._current = cache_key
            print(f"✅ Fully Loaded: {cache_key}")
            return f"Success: {cache_key}"
        except Exception as e:
            print(f"❌ Load Error: {e}")
            raise e

    def _build_prompt(self, message: str, context: Optional[str], tokenizer) -> str:
        system = SYSTEM_PROMPT
        if context: system += f"\n\nContext:\n{context}"
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": message}]
                return tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            except: pass
        return f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"

    async def generate(self, message: str, context: Optional[str] = None, max_tokens: int = 4096, temperature: float = 0.2, image_data: Optional[str] = None) -> str:
        if not self._current: return "No model."
        model, tokenizer, draft_model = self._cache[self._current]
        prompt = self._build_prompt(message, context, tokenizer)
        
        loop = asyncio.get_event_loop()
        
        def _gen():
            is_gemma4 = "gemma-4" in self._current.lower()
            print(f"DEBUG: Generating... (VLM: {is_gemma4 and VLM_AVAILABLE})")
            if is_gemma4 and VLM_AVAILABLE:
                from mlx_vlm import generate as vlm_gen
                print(f"DEBUG: draft_model present? {'Yes' if draft_model else 'No'}")
                return vlm_gen(model, tokenizer, prompt=prompt, image=self._prep_image(image_data), max_tokens=max_tokens, temp=temperature, draft_model=draft_model)
            else:
                from mlx_lm import generate as lm_gen
                return lm_gen(model, tokenizer, prompt=prompt, max_tokens=max_tokens, temp=temperature, draft_model=draft_model)
        
        return await loop.run_in_executor(executor, _gen)

    async def stream_generate(self, message: str, context: Optional[str] = None, max_tokens: int = 4096, temperature: float = 0.2, image_data: Optional[str] = None) -> AsyncIterator[str]:
        if not self._current:
            yield "No model."
            return
        model, tokenizer, draft_model = self._cache[self._current]
        prompt = self._build_prompt(message, context, tokenizer)
        loop = asyncio.get_event_loop()

        def _run():
            is_gemma4 = "gemma-4" in self._current.lower()
            if is_gemma4 and VLM_AVAILABLE:
                # VLM mode: use generate to get full output (avoids Metal thread issue)
                from mlx_vlm import generate as vlm_gen
                return vlm_gen(model, tokenizer, prompt=prompt, image=self._prep_image(image_data), max_tokens=max_tokens, temp=temperature, draft_model=draft_model)
            else:
                # LM mode: keep streaming behavior
                from mlx_lm import stream_generate as lm_stream
                return lm_stream(model, tokenizer, prompt=prompt, max_tokens=max_tokens, temp=temperature, draft_model=draft_model)

        result = await loop.run_in_executor(executor, _run)
        # If VLM mode, result is a string; otherwise it's an iterator of chunks
        if isinstance(result, str):
            yield result
        else:
            for chunk in result:
                text = chunk.text if hasattr(chunk, "text") else (chunk[0] if isinstance(chunk, tuple) else str(chunk))
                yield text

    def _prep_image(self, image_data: Optional[str]) -> Image.Image:
        if not image_data: return Image.new('RGB', (224, 224), color='white')
        try:
            return Image.open(io.BytesIO(base64.b64decode(image_data)))
        except:
            return Image.new('RGB', (224, 224), color='white')
