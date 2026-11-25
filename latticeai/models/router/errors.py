"""A backend that failed mid-stream produced an error, never model output.

Streaming backends used to hand their failure to the caller as a chunk of text,
which every consumer then treated as the model's answer. The failure now
travels as a typed exception, and ``_stream_failure`` is how a worker thread —
which cannot raise into the consuming coroutine — puts one on the chunk queue.
"""

from __future__ import annotations

import http.client
from typing import Tuple

import httpx


def _collect_optional_errors() -> Tuple[
    Tuple[type[Exception], ...], Tuple[type[Exception], ...]
]:
    """Resolve optional third-party exception base classes once at import time."""
    jinja_errors: list[type[Exception]] = []
    try:
        from jinja2 import TemplateError

        jinja_errors.append(TemplateError)
    except ImportError:  # pragma: no cover - optional runtime dependency
        pass

    hf_errors: list[type[Exception]] = []
    try:
        import huggingface_hub.errors as hf_mod

        for attr in dir(hf_mod):
            obj = getattr(hf_mod, attr)
            if isinstance(obj, type) and issubclass(obj, Exception):
                hf_errors.append(obj)
    except ImportError:  # pragma: no cover - optional runtime dependency
        pass

    return tuple(jinja_errors), tuple(hf_errors)


_JINJA_ERRORS, _HF_HUB_ERRORS = _collect_optional_errors()

#: Concrete exceptions raised by Jinja2/tokenizer chat-template rendering.
CHAT_TEMPLATE_ERRORS: Tuple[type[Exception], ...] = (
    TypeError,
    ValueError,
    RuntimeError,
    AttributeError,
    KeyError,
    *_JINJA_ERRORS,
)

#: Chat-template errors plus optional VLM helper import failures.
VLM_CHAT_TEMPLATE_ERRORS: Tuple[type[Exception], ...] = (
    ImportError,
    *CHAT_TEMPLATE_ERRORS,
)

#: Concrete exceptions raised across model download, load, HTTP, and smoke paths.
MODEL_RUNTIME_BOUNDARY_ERRORS: Tuple[type[Exception], ...] = (
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    ImportError,
    KeyError,
    AttributeError,
    TimeoutError,
    LookupError,
    httpx.HTTPError,
    http.client.HTTPException,
    *_JINJA_ERRORS,
    *_HF_HUB_ERRORS,
)


class ModelStreamError(RuntimeError):
    """A backend failed mid-stream. This is an error, never model output.

    Streaming backends used to hand their failure to the caller as a chunk of
    text (``"⚠️ Error: ..."``), which every consumer then treated as the
    model's answer: it was echoed to the client as content and persisted as a
    successful turn. The failure now travels as this typed exception instead.

    The MLX generators run on a worker thread that cannot raise into the
    consuming coroutine, so the thread puts an instance on the chunk queue and
    :meth:`LLMRouter._drain_stream_queue` re-raises it. The SSE endpoints
    (``latticeai.api.chat_stream.stream_chat`` and the document stream in
    ``latticeai.api.chat_documents``) already wrap their ``async for`` in
    ``except Exception`` and emit an ``error`` frame plus a ``[stream_error]``
    marker on the persisted answer, so the stream framing is unchanged.
    """


def _stream_failure(stage: str, exc: BaseException) -> ModelStreamError:
    """Envelope a backend exception for transport across the chunk queue.

    ``raise ... from exc`` is unavailable on the worker thread (nothing there
    consumes the traceback), so the cause is attached explicitly and stays
    visible in logs when the consumer re-raises.
    """
    error = ModelStreamError(f"{stage}: {exc}")
    error.__cause__ = exc
    return error
