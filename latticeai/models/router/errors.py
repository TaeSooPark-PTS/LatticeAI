"""A backend that failed mid-stream produced an error, never model output.

Streaming backends used to hand their failure to the caller as a chunk of text,
which every consumer then treated as the model's answer. The failure now
travels as a typed exception, and ``_stream_failure`` is how a worker thread —
which cannot raise into the consuming coroutine — puts one on the chunk queue.
"""


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
