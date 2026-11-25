from __future__ import annotations

import http.client
from typing import Tuple

import httpx


def _collect_optional_errors() -> Tuple[
    Tuple[type[Exception], ...], Tuple[type[Exception], ...]
]:
    jinja_errors: list[type[Exception]] = []
    try:
        from jinja2 import TemplateError

        jinja_errors.append(TemplateError)
    except ImportError:
        pass

    hf_errors: list[type[Exception]] = []
    try:
        import huggingface_hub.errors as hf_mod

        for attr in dir(hf_mod):
            obj = getattr(hf_mod, attr)
            if isinstance(obj, type) and issubclass(obj, Exception):
                hf_errors.append(obj)
    except ImportError:
        pass

    return tuple(jinja_errors), tuple(hf_errors)


_JINJA_ERRORS, _HF_HUB_ERRORS = _collect_optional_errors()

CHAT_TEMPLATE_ERRORS: Tuple[type[Exception], ...] = (
    TypeError,
    ValueError,
    RuntimeError,
    AttributeError,
    KeyError,
    *_JINJA_ERRORS,
)

VLM_CHAT_TEMPLATE_ERRORS: Tuple[type[Exception], ...] = (
    ImportError,
    *CHAT_TEMPLATE_ERRORS,
)

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
    pass


def _stream_failure(stage: str, exc: BaseException) -> ModelStreamError:
    error = ModelStreamError(f"{stage}: {exc}")
    error.__cause__ = exc
    return error
