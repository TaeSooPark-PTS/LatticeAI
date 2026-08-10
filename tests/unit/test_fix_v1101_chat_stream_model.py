"""v11.0.1 D9: the fast-path answer reports one model, not two.

``single_answer_response`` streams a canned answer for the fast-path chat
intents (network status, history, client URL). It used to advertise the
answering surface in the ``X-Model`` header while the SSE frame body carried
``single_text_stream``'s "system" default, so a client that read the stream
and a client that read the headers disagreed about who answered.

These tests pin the agreement itself — header value == body value — for every
model the real intent handlers pass, and confirm the helper's default is only
reached when no model is named at all.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from latticeai.api import chat_stream
from latticeai.api.chat_helpers import single_text_stream


def _drain(stream) -> List[str]:
    async def gather() -> List[str]:
        return [frame async for frame in stream]

    return asyncio.run(gather())


def _bodies(frames: List[str]) -> List[Dict[str, Any]]:
    decoded = []
    for frame in frames:
        body = frame.split("data: ", 1)[1].strip()
        if body != "[DONE]":
            decoded.append(json.loads(body))
    return decoded


# The three models the fast-path intent handlers actually pass
# (``latticeai/api/chat_intents.py``).
@pytest.mark.parametrize("model", ["network_status", "history", "client_url"])
def test_fast_path_header_and_sse_body_name_the_same_model(model: str):
    response = chat_stream.single_answer_response(
        SimpleNamespace(stream=True), "정리해 드렸습니다.", model=model
    )

    frames = _drain(response.body_iterator)
    bodies = _bodies(frames)

    assert response.headers["x-model"] == model
    assert [body["model"] for body in bodies] == [model]
    assert bodies == [{"chunk": "정리해 드렸습니다.", "model": model}]
    assert frames[-1] == "data: [DONE]\n\n"


def test_the_non_streaming_answer_is_untouched_by_the_model_passthrough():
    response = chat_stream.single_answer_response(
        SimpleNamespace(stream=False), "정리해 드렸습니다.", model="network_status"
    )

    assert json.loads(bytes(response.body)) == {"response": "정리해 드렸습니다."}
    assert "x-model" not in response.headers


def test_the_helper_default_is_only_reached_when_no_model_is_named():
    """The default stays "system" so any other call site keeps its bytes."""
    frames = _drain(single_text_stream("hi"))

    assert _bodies(frames) == [{"chunk": "hi", "model": "system"}]
