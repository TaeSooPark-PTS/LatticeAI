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
from typing import Any, AsyncIterator, List, Optional

from PIL import Image

from latticeai.core.quiet import quiet

from ._contract import RouterCore as _Core
from .branding import SYSTEM_PROMPT, _compose_system, normalize_branding


def _system_for(context, cite_sources: bool) -> str:
    """The system prompt for one completion.

    ``cite_sources`` is what tells the two kinds of **caller** apart, and
    v12.0.0 added it because nothing did.

    A *chat* caller sends retrieved passages, and gets this product's own
    system prompt with the Context block and
    :data:`~latticeai.models.router.branding.CITATION_INSTRUCTION` appended.
    :func:`_compose_system` used to do that for any non-empty context, which is
    right there and wrong for the agent seam, whose context is the loop's own
    executor prompt: every agent turn was being told its instructions were
    "retrieved sources" to "cite inline as [1], [2]". A large model ignores
    that. A small one obeys it — the acid-test 0.5B wrote
    ``[1] 인사말을 쓸 예시 코드: …`` into a file, and a 2B answered a tool call
    with the citation instruction itself.

    A *worker* caller sends **its own whole prompt**, and gets exactly that.
    Same fact, one flag: a context that is not a corpus is an instruction, and
    an instruction the caller wrote is the only instruction that turn should
    carry. Until now the chat persona was still prepended to it, so six lines
    of "You are Lattice AI … You are a Vision-Language Model … Be concise" sat
    in front of every guided micro-turn — including the one that asks a model to
    write a file's contents. Small models answer the nearest instruction: a live
    2B asked to summarise a README wrote "I am a local AI assistant that can run
    on Apple Silicon" into the file, and a gemma-4-e2b opened two of its three
    files with "Identity: Lattice AI (Vision-Language Model on Apple Silicon)".
    Nothing leaked verbatim; the *subject* leaked, which is the same defect one
    paraphrase further on. The document path has always worked this way — its
    own system prompt replaces the chat identity entirely — so this is the
    existing rule reaching the second caller that has a prompt of its own.

    Both agent prompts still say who they are (``You are the executor of an
    agent loop``) and every guided block still carries the run's own answer
    language, so nothing the loop depends on is being removed — only a second,
    competing identity. Branding normalisation is untouched: it runs over the
    context here and over every generated string on the way out.

    A worker call with **no** context still gets the product prompt. A bare
    completion carrying no instruction at all is the one case where the identity
    is the only thing there is to say.
    """
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
    """Generate on the worker thread, ending at the first stop string.

    The buffered backends have no stop-string argument, so honouring one means
    driving the *streaming* backend and breaking out of it. That is the whole
    difference between a stop string and trimming the answer afterwards: the
    tokens past the stop are never produced, which is time a 2B model would
    otherwise spend explaining a tool call it has already emitted.

    Runs inside :data:`executor`, like every other MLX call, so the GPU stream
    stays on one thread.
    """
    if use_vlm:
        from mlx_vlm import stream_generate as vlm_stream

        chunks = vlm_stream(model, tokenizer, prompt=prompt, image=image, max_tokens=max_tokens, sampler=sampler, draft_model=draft_model, draft_kind="mtp")
    else:
        from mlx_lm import stream_generate as lm_stream

        chunks = lm_stream(model, tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=sampler, draft_model=draft_model)
    text = ""
    for chunk in chunks:
        piece = chunk.text if hasattr(chunk, "text") else (chunk[0] if isinstance(chunk, tuple) else str(chunk))
        text += piece
        if any(marker in text for marker in stops):
            break
    return apply_stop_strings(text, stops)


class _GenerationMixin(_Core):
    """The chat generation half of :class:`LLMRouter`."""

    def _build_prompt(
        self, message: str, context: Optional[str], tokenizer, cite_sources: bool = True
    ) -> str:
        system = _system_for(context, cite_sources)
        if hasattr(tokenizer, "apply_chat_template"):
            try:
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": message}]
                return tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            except Exception:
                quiet()
        return f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"

    def _build_vlm_prompt(self, model, processor, message: str, context: Optional[str], num_images: int, cite_sources: bool = True) -> str:
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
        except Exception as e:
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
        """Generate with a request-scoped model without changing the default.

        ``stop`` ends the reply at the first of the given strings. Local
        generation switches to the streaming backend to do it, so the tokens
        after the stop are never produced rather than merely trimmed; the cloud
        path forwards the list to the provider, which stops server-side. Absent
        or empty means what it always meant — generate to ``max_tokens``.

        ``prefix`` **starts** the reply (v12.0.0): the characters are put in the
        model's mouth rather than requested of it, so a caller that needs the
        answer to begin ``{"thoughts": "`` gets that by construction instead of
        by asking nicely and repairing the result. Locally this is a real
        prefill — the text is appended to the templated prompt after the
        generation marker, so the model continues from mid-token rather than
        starting a fresh turn. The returned text always begins with it; see
        :func:`~latticeai.models.router.loading.apply_prefix` for how the three
        backends are reconciled.
        """
        _selected, cached = self._model_snapshot(model_id)
        if cached is None:
            return "No model."
        return await self._generate_cached(
            cached, message, context, max_tokens, temperature, image_data, stop, prefix,
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
            None, message, context, max_tokens, temperature, image_data, stop, prefix,
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
                cached, message, context, max_tokens, temperature, stop, prefix, cite_sources
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
        # The prefill. Appended *after* the chat template's generation marker,
        # so the first sampled token continues these characters instead of
        # opening a reply. Nothing else in the pipeline changes.
        if prefix:
            prompt = f"{prompt}{prefix}"
        stops = [marker for marker in (stop or []) if marker]

        loop = asyncio.get_event_loop()

        def _gen():
            import mlx.core as mx  # type: ignore[no-redef]

            mx.set_default_device(mx.gpu)  # type: ignore[arg-type]
            # Decoded here, not above: base64 + PIL is CPU work, and this
            # function is the part that runs off the event loop.
            image = self._prep_image(image_data) if image_data else None
            sampler = _mlx_sampler(temperature)
            if stops:
                return _stream_until_stop(
                    model, tokenizer, prompt, image, max_tokens, sampler, draft_model,
                    use_vlm, stops,
                )
            if use_vlm:
                from mlx_vlm import generate as vlm_gen
                return vlm_gen(model, tokenizer, prompt=prompt, image=image, max_tokens=max_tokens, sampler=sampler, draft_model=draft_model, draft_kind="mtp")
            from mlx_lm import generate as lm_gen
            return lm_gen(model, tokenizer, prompt=prompt, max_tokens=max_tokens, sampler=sampler, draft_model=draft_model)
        result = await loop.run_in_executor(executor, _gen)
        # mlx-vlm might return a GenerationResult object; extract the text
        text = result.text if hasattr(result, "text") else str(result)
        return apply_prefix(prefix, normalize_branding(apply_stop_strings(text, stops)))

    async def _cloud_generate(self, cloud: CloudModel, message: str, context: Optional[str], max_tokens: int, temperature: float, stop: Optional[List[str]] = None, prefix: Optional[str] = None, cite_sources: bool = True) -> str:
        system = _system_for(context, cite_sources)
        stops = [marker for marker in (stop or []) if marker]
        extra = {"stop": stops} if stops else {}
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ]
        # Assistant prefill: the OpenAI-compatible way to say "continue this".
        # Local servers (vLLM, llama.cpp, LM Studio, Ollama) honour it; a
        # provider that does not simply reads it as context, and `apply_prefix`
        # makes both answers the same shape for the caller.
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
        return apply_prefix(prefix, normalize_branding(response.choices[0].message.content or ""))

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
