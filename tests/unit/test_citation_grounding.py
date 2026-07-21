"""Answer-citation binding tests (backlog #11, review §7.2 E #4).

Covers: the assess_answer_grounding heuristic (supported via token overlap,
supported via explicit title citation, unsupported when the answer ignores
every retrieved source, no_context when retrieval returned nothing), the
0-score falsy trap, and the wiring into the streaming trailer via
stream_chat (annotation only — the answer is never blocked or modified).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.chat_helpers import assess_answer_grounding
from latticeai.api.chat_stream import stream_chat


def _trace(nodes=None, source_files=None):
    return {
        "graph_nodes": nodes or [],
        "source_files": source_files or [],
        "confidence": 0.5,
    }


MEETING_NODE = {
    "id": "doc:meeting",
    "title": "주간 회의록",
    "summary": "출시일을 8월 15일로 확정했다. 범위는 식물 기록과 물주기 알림.",
    "metadata": {"filename": "meeting.md"},
}
STACK_NODE = {
    "id": "doc:stack",
    "title": "프로젝트 개요",
    "summary": "프론트엔드는 React, 백엔드는 FastAPI, 데이터는 SQLite 로컬 저장.",
    "metadata": {},
}


def test_supported_when_answer_uses_source_content():
    answer = "회의에서 출시일을 8월 15일로 확정했고, 범위는 식물 기록과 물주기 알림입니다."
    grounding = assess_answer_grounding(
        answer,
        trace=_trace(nodes=[MEETING_NODE, STACK_NODE]),
        context_quality={"mode": "hybrid", "nodes": 2, "limited": False},
    )
    assert grounding["status"] == "supported"
    assert grounding["label"] == "근거 있음"
    assert "doc:meeting" in grounding["source_ids"]
    assert grounding["overlap"] > 0.0
    assert grounding["reason"] is None


def test_supported_via_explicit_title_citation():
    # Barely any token overlap, but the answer names the source document.
    answer = "자세한 내용은 '주간 회의록' 문서를 참고하세요."
    grounding = assess_answer_grounding(answer, trace=_trace(nodes=[MEETING_NODE]))
    assert grounding["status"] == "supported"
    cited = {item["id"]: item for item in grounding["cited"]}
    assert cited["doc:meeting"]["explicit"] is True


def test_unsupported_when_answer_ignores_sources():
    answer = "고대 로마의 수도교는 중력만으로 물을 운반했습니다."
    grounding = assess_answer_grounding(
        answer,
        trace=_trace(nodes=[MEETING_NODE, STACK_NODE]),
        context_quality={"mode": "hybrid", "nodes": 2, "limited": False},
    )
    assert grounding["status"] == "unsupported"
    assert grounding["label"] == "근거 없음"
    assert grounding["source_ids"] == []
    assert grounding["reason"]


def test_no_context_when_nothing_retrieved():
    grounding = assess_answer_grounding(
        "아무 답변",
        trace=_trace(),
        context_quality={"mode": "none", "nodes": 0, "limited": True},
    )
    assert grounding["status"] == "no_context"
    assert grounding["label"] == "근거 없음"
    assert grounding["source_ids"] == []


def test_empty_answer_is_unsupported_not_crash():
    grounding = assess_answer_grounding("", trace=_trace(nodes=[MEETING_NODE]))
    assert grounding["status"] == "unsupported"
    assert grounding["overlap"] == 0.0  # exact 0.0 — the falsy value is valid


def test_source_files_only_trace_still_binds():
    answer = "meeting.md 파일의 회의록 내용을 요약하면 출시일 확정입니다."
    grounding = assess_answer_grounding(
        answer,
        trace=_trace(source_files=[{
            "source": "meeting.md",
            "node_id": "doc:meeting",
            "node_title": "회의록 출시일 확정",
        }]),
    )
    assert grounding["status"] == "supported"
    assert grounding["source_ids"] == ["doc:meeting"]


def test_grounding_never_raises_on_malformed_trace():
    for trace in (None, {}, {"graph_nodes": [None, 42, {}]}, {"graph_nodes": "oops"}):
        try:
            result = assess_answer_grounding("답변", trace=trace)
        except TypeError:
            # "oops" string iterates chars → non-dict entries are skipped;
            # any raise here is a regression.
            raise
        assert result["status"] in {"supported", "unsupported", "no_context"}


# ── streaming wiring ─────────────────────────────────────────────────────────

class _Req:
    message = "회의 결정 사항 알려줘"
    conversation_id = "conv-1"
    user_email = "user@example.com"
    user_nickname = None
    source = None
    max_tokens = 128
    temperature = 0.2


class _Router:
    async def stream_generate_as(self, model_id, message, context, max_tokens, temperature, image):
        yield "출시일을 8월 15일로 확정했고 물주기 알림을 포함합니다."


class _ChatService:
    def __init__(self):
        self.persisted = []

    def build_graph_trace(self, *args, **kwargs):  # pragma: no cover - trace_seed given
        return _trace()

    async def persist_answer(self, **kwargs):
        self.persisted.append(kwargs)
        return {"id": "trace-1", **kwargs.get("trace", {})}


def test_stream_chat_trailer_carries_grounding():
    chat_service = _ChatService()
    trace_seed = _trace(nodes=[MEETING_NODE])

    async def scenario():
        events = []
        async for chunk in stream_chat(
            _Req(),
            "context",
            None,
            router=_Router(),
            chat_service=chat_service,
            knowledge_graph=None,
            enable_graph=False,
            notify=None,
            trace_seed=trace_seed,
            effective_email="user@example.com",
            history_meta={},
            model_id="test-model",
            workspace_id=None,
            context_quality={"mode": "hybrid", "nodes": 1, "limited": True},
        ):
            events.append(chunk)
        return events

    events = asyncio.run(scenario())

    payloads = [
        json.loads(event[len("data: "):])
        for event in events
        if event.startswith("data: ") and event.strip() != "data: [DONE]"
    ]
    trailer = payloads[-1]
    assert trailer["grounding"]["status"] == "supported"
    assert trailer["grounding"]["source_ids"] == ["doc:meeting"]
    assert trailer["context_quality"]["mode"] == "hybrid"
    # The verdict was also recorded on the persisted trace.
    persisted_trace = chat_service.persisted[0]["trace"]
    assert persisted_trace["grounding"]["status"] == "supported"
