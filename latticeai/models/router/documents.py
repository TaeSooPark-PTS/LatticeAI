"""Document generation — the same backends, driven by a specialized prompt.

Structurally parallel to :mod:`.generation` and deliberately separate: a
document run carries the caller's own system prompt, never the chat system
prompt, and never an image. Same executor, same stream-failure envelope, same
drain.
"""

import asyncio
from typing import Any, AsyncIterator

from ._contract import RouterCore as _Core
from .branding import normalize_branding
from .catalog import CloudModel
from .errors import ModelStreamError, _stream_failure
from .loading import _mlx_sampler, executor


class _DocumentMixin(_Core):
    """The document generation half of :class:`LLMRouter`."""

    # ── Document Generation Pipeline ──────────────────────────────────────

    async def generate_document_as(
        self,
        model_id: str | None,
        message: str,
        system_prompt: str,
        *,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> str:
        """Generate a document with a request-scoped model."""
        _selected, cached = self._model_snapshot(model_id)
        if cached is None:
            return "No model loaded."

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
            import mlx.core as mx  # type: ignore[no-redef]

            mx.set_default_device(mx.gpu)  # type: ignore[arg-type]
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

    async def stream_generate_document_as(
        self,
        model_id: str | None,
        message: str,
        system_prompt: str,
        *,
        max_tokens: int = 8192,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """Stream a document with a request-scoped model."""
        _selected, cached = self._model_snapshot(model_id)
        if cached is None:
            yield "No model loaded."
            return

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
        queue: "asyncio.Queue[Any]" = asyncio.Queue()

        def _stream():
            import mlx.core as mx  # type: ignore[no-redef]

            mx.set_default_device(mx.gpu)  # type: ignore[arg-type]
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
            except Exception as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait, _stream_failure("MLX document stream failed", exc)
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(executor, _stream)
        async for chunk in self._drain_stream_queue(queue):
            yield chunk

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
        except Exception as exc:
            raise ModelStreamError(self._local_server_error_hint(cloud, exc)) from exc
        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta.content
            if delta:
                yield normalize_branding(delta)
