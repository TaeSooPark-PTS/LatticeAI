"""Chat generation — one answer, or a stream of tokens, local or cloud.

The MLX generators run on the dedicated single-thread executor so GPU streams
match across a request; they cannot raise into the consuming coroutine, so a
failure is enveloped onto the chunk queue and :meth:`_drain_stream_queue`
re-raises it. That is the whole reason a backend failure can no longer be
mistaken for the model's answer.
"""

import asyncio
import base64
import io
from typing import Any, AsyncIterator, Optional

from PIL import Image

from latticeai.core.quiet import quiet

from ._contract import RouterCore as _Core
from .branding import SYSTEM_PROMPT, _compose_system, normalize_branding
from .catalog import CloudModel
from .errors import ModelStreamError, _stream_failure
from .loading import _mlx_sampler, executor


class _GenerationMixin(_Core):
    """The chat generation half of :class:`LLMRouter`."""

    def _build_prompt(self, message: str, context: Optional[str], tokenizer) -> str:
        context = normalize_branding(context)
        system = _compose_system(SYSTEM_PROMPT, context)
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": message}]
                return tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            except Exception:
                quiet()
        return f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"

    def _build_vlm_prompt(self, model, processor, message: str, context: Optional[str], num_images: int) -> str:
        context = normalize_branding(context)
        system = _compose_system(SYSTEM_PROMPT, context)
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

    async def generate_as(
        self,
        model_id: str | None,
        message: str,
        context: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        image_data: Optional[str] = None,
    ) -> str:
        """Generate with a request-scoped model without changing the default."""
        _selected, cached = self._model_snapshot(model_id)
        if cached is None:
            return "No model."
        return await self._generate_cached(cached, message, context, max_tokens, temperature, image_data)

    async def generate(
        self,
        message: str,
        context: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        image_data: Optional[str] = None,
    ) -> str:
        return await self.generate_as(None, message, context, max_tokens, temperature, image_data)

    async def _generate_cached(
        self,
        cached: object,
        message: str,
        context: Optional[str],
        max_tokens: int,
        temperature: float,
        image_data: Optional[str],
    ) -> str:
        if isinstance(cached, CloudModel):
            return await self._cloud_generate(cached, message, context, max_tokens, temperature)

        model, tokenizer, draft_model, loader_kind = self._unpack_local_cache(cached)
        use_vlm = loader_kind == "mlx_vlm"
        prompt = (
            self._build_vlm_prompt(model, tokenizer, message, context, 1 if image_data else 0)
            if use_vlm
            else self._build_prompt(message, context, tokenizer)
        )
        
        loop = asyncio.get_event_loop()
        
        def _gen():
            import mlx.core as mx  # type: ignore[no-redef]

            mx.set_default_device(mx.gpu)  # type: ignore[arg-type]
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
        context = normalize_branding(context)
        system = _compose_system(SYSTEM_PROMPT, context)
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

    async def stream_generate_as(
        self,
        model_id: str | None,
        message: str,
        context: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        image_data: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Stream with a request-scoped model without changing the default."""
        _selected, cached = self._model_snapshot(model_id)
        if cached is None:
            yield "No model."
            return
        if isinstance(cached, CloudModel):
            async for chunk in self._cloud_stream_generate(cached, message, context, max_tokens, temperature):
                yield chunk
            return

        model, tokenizer, draft_model, loader_kind = self._unpack_local_cache(cached)
        use_vlm = loader_kind == "mlx_vlm"
        prompt = (
            self._build_vlm_prompt(model, tokenizer, message, context, 1 if image_data else 0)
            if use_vlm
            else self._build_prompt(message, context, tokenizer)
        )
        loop = asyncio.get_event_loop()
        queue: "asyncio.Queue[Any]" = asyncio.Queue()

        def _stream():
            import mlx.core as mx  # type: ignore[no-redef]

            mx.set_default_device(mx.gpu)  # type: ignore[arg-type]
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
            except Exception as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait, _stream_failure("MLX chat stream failed", exc)
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(executor, _stream)
        async for chunk in self._drain_stream_queue(queue):
            yield chunk

    @staticmethod
    async def _drain_stream_queue(queue: "asyncio.Queue[Any]") -> AsyncIterator[str]:
        """Yield worker-thread chunks until the terminator; raise failures.

        ``None`` terminates the stream. A :class:`ModelStreamError` on the
        queue is a backend failure envelope, not model text, so it is raised
        into the consuming coroutine — callers must never be able to mistake
        it for an answer.
        """
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            if isinstance(chunk, ModelStreamError):
                raise chunk
            yield normalize_branding(chunk)

    async def stream_generate(
        self,
        message: str,
        context: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        image_data: Optional[str] = None,
    ) -> AsyncIterator[str]:
        async for chunk in self.stream_generate_as(
            None, message, context, max_tokens, temperature, image_data
        ):
            yield chunk

    async def _cloud_stream_generate(self, cloud: CloudModel, message: str, context: Optional[str], max_tokens: int, temperature: float) -> AsyncIterator[str]:
        context = normalize_branding(context)
        system = _compose_system(SYSTEM_PROMPT, context)
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
        except Exception as exc:
            # Same invariant as the MLX path: a backend that never produced a
            # token failed, and that is an error — not the model's answer.
            raise ModelStreamError(self._local_server_error_hint(cloud, exc)) from exc
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
