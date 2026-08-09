"""Pure chat helpers — the branches the router-level tests never reach.

Language detection, empty-input gates, fenced-content stripping, the Korean
network-status block, the lexical-only retrieval failure signal, grounding
source de-duplication, the one-shot SSE stream, and the recent-context
assembler's anonymous / image-reply-filtering paths.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from latticeai.api.chat_helpers import (
    assess_answer_grounding,
    build_context_quality,
    build_recent_chat_context,
    detect_language,
    format_network_status,
    is_file_action_request,
    single_text_stream,
    strip_generated_file_content,
)


def _drain(agen) -> List[str]:
    async def run() -> List[str]:
        return [chunk async for chunk in agen]

    return asyncio.run(run())


# ── language detection ──────────────────────────────────────────────────

def test_korean_text_detects_ko_and_ascii_detects_en():
    assert detect_language("안녕하세요 파일을 만들어 주세요") == "ko"
    # a single Hangul syllable in a long ASCII sentence stays under the 5% bar
    assert detect_language("please write the report file for me now ok 가") == "en"
    assert detect_language("write the report") == "en"


# ── file-action gate: empty input ───────────────────────────────────────

def test_blank_message_is_never_a_file_action():
    assert is_file_action_request("") is False
    assert is_file_action_request("   \n\t ") is False
    assert is_file_action_request(None) is False


# ── generated-content unwrapping ────────────────────────────────────────

def test_strip_generated_file_content_unwraps_a_fenced_block():
    raw = "Sure!\n```python\nprint('hi')\n```\nAnything else?"
    assert strip_generated_file_content(raw) == "print('hi')"


def test_strip_generated_file_content_keeps_unfenced_text():
    assert strip_generated_file_content("  plain body  ") == "plain body"
    assert strip_generated_file_content(None) == ""


# ── network status formatting ───────────────────────────────────────────

def test_format_network_status_renders_interfaces_and_note():
    text = format_network_status({
        "local_ip": "192.168.0.10",
        "public_ip": "203.0.113.7",
        "hostname": "lattice-box",
        "local_ips": {"en0": "192.168.0.10", "lo0": "127.0.0.1"},
        "note": "VPN 연결됨",
    })
    lines = text.split("\n")
    assert lines[0] == "내부 IP: 192.168.0.10"
    assert lines[1] == "외부 IP: 203.0.113.7"
    assert lines[2] == "호스트명: lattice-box"
    assert "인터페이스:" in lines
    assert "- en0: 192.168.0.10" in lines
    assert "- lo0: 127.0.0.1" in lines
    assert lines[-1] == "VPN 연결됨"


def test_format_network_status_marks_unknown_fields_and_skips_optionals():
    text = format_network_status({})
    assert text == "내부 IP: 확인 안 됨\n외부 IP: 확인 안 됨\n호스트명: 확인 안 됨"


# ── context quality: lexical-only store that fails ──────────────────────

class _LexicalOnlyGraph:
    """A store without the hybrid mixin — chat must fall back to ``search``."""

    def __init__(self, *, boom: bool = False) -> None:
        self.boom = boom
        self.calls: List[Dict[str, Any]] = []

    def search(self, query, limit=6, **kwargs):
        self.calls.append({"query": query, "limit": limit, **kwargs})
        if self.boom:
            raise RuntimeError("index locked")
        return {"matches": [{"id": "n1"}, {"id": "n2"}]}


def test_lexical_search_failure_reports_an_honest_none_signal():
    graph = _LexicalOnlyGraph(boom=True)
    signal = build_context_quality("배포 절차", knowledge_graph=graph)
    assert signal["mode"] == "none"
    assert signal["nodes"] == 0
    assert signal["limited"] is True
    assert signal["reason"] == "그래프 검색에 실패했습니다"
    assert graph.calls == [{"query": "배포 절차", "limit": 6}]


def test_lexical_search_success_is_reported_as_lexical_only():
    graph = _LexicalOnlyGraph()
    signal = build_context_quality(
        "배포 절차", knowledge_graph=graph, allowed_workspaces={"ws-1"}
    )
    assert signal["mode"] == "lexical_only"
    assert signal["nodes"] == 2
    assert graph.calls[0]["allowed_workspaces"] == {"ws-1"}


# ── grounding: malformed / duplicate / empty sources ────────────────────

def test_grounding_skips_malformed_and_duplicate_source_files():
    verdict = assess_answer_grounding(
        "배포 절차는 스테이징 검증 후 프로덕션 승격입니다",
        trace={
            "graph_nodes": [
                {"id": "node-1", "title": "배포 절차",
                 "summary": "스테이징 검증 후 프로덕션 승격"},
            ],
            "source_files": [
                "not-a-dict",                      # skipped: wrong type
                {"node_id": "", "source": ""},     # skipped: no identity
                {"node_id": "node-1", "source": "deploy.md"},  # skipped: duplicate
                {"node_id": "node-2", "node_title": "무관 문서", "source": "other.md"},
            ],
        },
    )
    assert verdict["status"] == "supported"
    # node-1 arrived once; the duplicate source_files row did not add a source
    assert verdict["source_ids"] == ["node-1"]


def test_grounding_ignores_sources_whose_body_has_no_tokens():
    verdict = assess_answer_grounding(
        "완전히 다른 주제의 답변입니다",
        trace={"graph_nodes": [{"id": "empty-node"}]},
    )
    # the only candidate carried no comparable text, so nothing binds
    assert verdict["status"] == "unsupported"
    assert verdict["source_ids"] == []
    assert verdict["overlap"] == 0.0


# ── one-shot SSE stream ─────────────────────────────────────────────────

def test_single_text_stream_emits_one_chunk_then_done():
    frames = _drain(single_text_stream("네트워크 상태입니다", model="network_status"))
    assert len(frames) == 2
    assert frames[1] == "data: [DONE]\n\n"
    payload = json.loads(frames[0][len("data: "):])
    assert payload == {"chunk": "네트워크 상태입니다", "model": "network_status"}


def test_single_text_stream_defaults_to_the_system_model():
    frames = _drain(single_text_stream("hi"))
    assert json.loads(frames[0][len("data: "):])["model"] == "system"


# ── recent chat context assembly ────────────────────────────────────────

def test_recent_context_without_identity_reads_the_unscoped_history():
    calls: List[Dict[str, Any]] = []

    def get_history(**kwargs):
        calls.append(kwargs)
        return [
            {"role": "user", "content": "안녕", "source": "web"},
            {"role": "assistant", "content": "반갑습니다"},
        ]

    text = build_recent_chat_context(get_history=get_history)
    # anonymous callers must not pass identity/workspace filters at all
    assert calls == [{}]
    assert text == "user (web): 안녕\nassistant: 반갑습니다"


def test_recent_context_can_drop_image_missing_replies():
    history = [
        {"role": "user", "content": "이 사진 분석해줘"},
        {"role": "assistant", "content": "이미지를 업로드해 주세요"},
        {"role": "user", "content": "그럼 텍스트로"},
        {"role": "assistant", "content": "네, 알겠습니다"},
    ]

    kept = build_recent_chat_context(
        get_history=lambda **kw: list(history),
        include_image_missing_replies=False,
    )
    assert "이미지를 업로드해 주세요" not in kept
    assert "네, 알겠습니다" in kept

    # the default keeps the same reply — the filter is opt-in
    assert "이미지를 업로드해 주세요" in build_recent_chat_context(
        get_history=lambda **kw: list(history)
    )
