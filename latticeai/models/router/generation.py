import asyncio
import base64
import io
import logging
from typing import Any, AsyncIterator, List, Optional

from PIL import Image

from latticeai.core.quiet import quiet

from ._contract import RouterCore as _Core
from .branding import SYSTEM_PROMPT, _compose_system, normalize_branding
from .errors import CHAT_TEMPLATE_ERRORS, VLM_CHAT_TEMPLATE_ERRORS


def _system_for(context, cite_sources: bool) -> str:
    context = normalize_branding(context)
    if cite_sources:
        return _compose_system(SYSTEM_PROMPT, context)
    return context or SYSTEM_PROMPT


from .catalog import CloudModel
from .errors import ModelStreamError, _stream_failure
from .loading import _mlx_sampler, apply_prefix, apply_stop_strings, executor


def _stream_until_stop(
    model,
    tokenizer,
    prompt: str,
    image,
    max_tokens: int,
    sampler,
    draft_model,
    use_vlm: bool,
    stops: List[str],
) -> str:
    if use_vlm:
        from mlx_vlm import stream_generate as vlm_stream

        chunks = vlm_stream(
            model,
            tokenizer,
            prompt=prompt,
            image=image,
            max_tokens=max_tokens,
            sampler=sampler,
            draft_model=draft_model,
            draft_kind="mtp",
        )
    else:
        from mlx_lm import stream_generate as lm_stream

        chunks = lm_stream(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=sampler,
            draft_model=draft_model,
        )
    text = ""
    for chunk in chunks:
        piece = (
            chunk.text
            if hasattr(chunk, "text")
            else (chunk[0] if isinstance(chunk, tuple) else str(chunk))
        )
        text += piece
        if any(marker in text for marker in stops):
            break
    return apply_stop_strings(text, stops)


class _GenerationMixin(_Core):
    def _build_prompt(
        self, message: str, context: Optional[str], tokenizer, cite_sources: bool = True
    ) -> str:
        system = _system_for(context, cite_sources)
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                msgs = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ]
                return tokenizer.apply_chat_template(
                    msgs, tokenize=False, add_generation_prompt=True
                )
            except CHAT_TEMPLATE_ERRORS:
                quiet()
        return f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"

    def _build_vlm_prompt(
        self,
        model,
        processor,
        message: str,
        context: Optional[str],
        num_images: int,
        cite_sources: bool = True,
    ) -> str:
        system = _system_for(context, cite_sources)
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
        except VLM_CHAT_TEMPLATE_ERRORS as e:
            print(f"⚠️ VLM chat template fallback: {e}")
            return self._build_prompt(message, context, processor, cite_sources)

    async def generate_as(
        self,
        model_id: str | None,
        message: str,
        context: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        image_data: Optional[str] = None,
        stop: Optional[List[str]] = None,
        prefix: Optional[str] = None,
        cite_sources: bool = True,
    ) -> str:
        _selected, cached = self._model_snapshot(model_id)
        if cached is None:
            return "No model."
        return await self._generate_cached(
            cached,
            message,
            context,
            max_tokens,
            temperature,
            image_data,
            stop,
            prefix,
            cite_sources,
        )

    async def generate(
        self,
        message: str,
        context: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        image_data: Optional[str] = None,
        stop: Optional[List[str]] = None,
        prefix: Optional[str] = None,
        cite_sources: bool = True,
    ) -> str:
        return await self.generate_as(
            None,
            message,
            context,
            max_tokens,
            temperature,
            image_data,
            stop,
            prefix,
            cite_sources,
        )

    async def _generate_cached(
        self,
        cached: object,
        message: str,
        context: Optional[str],
        max_tokens: int,
        temperature: float,
        image_data: Optional[str],
        stop: Optional[List[str]] = None,
        prefix: Optional[str] = None,
        cite_sources: bool = True,
    ) -> str:
        if isinstance(cached, CloudModel):
            return await self._cloud_generate(
                cached,
                message,
                context,
                max_tokens,
                temperature,
                stop,
                prefix,
                cite_sources,
            )

        model, tokenizer, draft_model, loader_kind = self._unpack_local_cache(cached)
        use_vlm = loader_kind == "mlx_vlm"
        prompt = (
            self._build_vlm_prompt(
                model, tokenizer, message, context, 1 if image_data else 0, cite_sources
            )
            if use_vlm
            else self._build_prompt(message, context, tokenizer, cite_sources)
        )
        if prefix:
            prompt = f"{prompt}{prefix}"
        stops = [marker for marker in (stop or []) if marker]

        loop = asyncio.get_event_loop()

        def _gen():
            import mlx.core as mx

            mx.set_default_device(mx.gpu)
            image = self._prep_image(image_data) if image_data else None
            sampler = _mlx_sampler(temperature)
            if stops:
                return _stream_until_stop(
                    model,
                    tokenizer,
                    prompt,
                    image,
                    max_tokens,
                    sampler,
                    draft_model,
                    use_vlm,
                    stops,
                )
            if use_vlm:
                from mlx_vlm import generate as vlm_gen

                return vlm_gen(
                    model,
                    tokenizer,
                    prompt=prompt,
                    image=image,
                    max_tokens=max_tokens,
                    sampler=sampler,
                    draft_model=draft_model,
                    draft_kind="mtp",
                )
            from mlx_lm import generate as lm_gen

            return lm_gen(
                model,
                tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                sampler=sampler,
                draft_model=draft_model,
            )

        result = await loop.run_in_executor(executor, _gen)
        text = result.text if hasattr(result, "text") else str(result)
        return apply_prefix(prefix, normalize_branding(apply_stop_strings(text, stops)))

    async def _cloud_generate(
        self,
        cloud: CloudModel,
        message: str,
        context: Optional[str],
        max_tokens: int,
        temperature: float,
        stop: Optional[List[str]] = None,
        prefix: Optional[str] = None,
        cite_sources: bool = True,
    ) -> str:
        system = _system_for(context, cite_sources)
        stops = [marker for marker in (stop or []) if marker]
        extra = {"stop": stops} if stops else {}
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ]
        if prefix:
            messages.append({"role": "assistant", "content": prefix})
        try:
            response = await cloud.client.chat.completions.create(
                model=cloud.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **extra,
            )
        except Exception as e:
            raise RuntimeError(self._local_server_error_hint(cloud, e)) from e
        return apply_prefix(
            prefix, normalize_branding(response.choices[0].message.content or "")
        )

    async def stream_generate_as(
        self,
        model_id: str | None,
        message: str,
        context: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        image_data: Optional[str] = None,
    ) -> AsyncIterator[str]:
        _selected, cached = self._model_snapshot(model_id)
        if cached is None:
            yield "No model."
            return
        if isinstance(cached, CloudModel):
            async for chunk in self._cloud_stream_generate(
                cached, message, context, max_tokens, temperature
            ):
                yield chunk
            return

        model, tokenizer, draft_model, loader_kind = self._unpack_local_cache(cached)
        use_vlm = loader_kind == "mlx_vlm"
        prompt = (
            self._build_vlm_prompt(
                model, tokenizer, message, context, 1 if image_data else 0
            )
            if use_vlm
            else self._build_prompt(message, context, tokenizer)
        )
        loop = asyncio.get_event_loop()
        queue: "asyncio.Queue[Any]" = asyncio.Queue()

        def _stream():
            import mlx.core as mx

            mx.set_default_device(mx.gpu)
            try:
                if use_vlm:
                    from mlx_vlm import stream_generate as vlm_stream

                    gen = vlm_stream(
                        model,
                        tokenizer,
                        prompt=prompt,
                        image=self._prep_image(image_data) if image_data else None,
                        max_tokens=max_tokens,
                        sampler=_mlx_sampler(temperature),
                        draft_model=draft_model,
                        draft_kind="mtp",
                    )
                else:
                    from mlx_lm import stream_generate as lm_stream

                    gen = lm_stream(
                        model,
                        tokenizer,
                        prompt=prompt,
                        max_tokens=max_tokens,
                        sampler=_mlx_sampler(temperature),
                        draft_model=draft_model,
                    )

                for chunk in gen:
                    text = (
                        chunk.text
                        if hasattr(chunk, "text")
                        else (chunk[0] if isinstance(chunk, tuple) else str(chunk))
                    )
                    loop.call_soon_threadsafe(queue.put_nowait, text)
            except Exception as exc:
                logging.exception("MLX chat stream failed")
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

    async def _cloud_stream_generate(
        self,
        cloud: CloudModel,
        message: str,
        context: Optional[str],
        max_tokens: int,
        temperature: float,
    ) -> AsyncIterator[str]:
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
        except (
            OSError,
            ValueError,
            TypeError,
            RuntimeError,
            AttributeError,
            Image.DecompressionBombError,
        ) as e:
            print(f"⚠️ VLM image decode failed: {e}")
            return None
