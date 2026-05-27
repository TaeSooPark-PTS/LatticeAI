"""
Document Generator — 지식 그래프 기반 고품질 문서 자동 생성 모듈.

사용자가 "Q3 마케팅 전략 보고서 작성해줘" 같은 일반 채팅을 하면,
Knowledge Graph에서 관련 과거 문서/개념/관계를 자동으로 찾아
LLM이 자연스럽고 일관성 있게 새로운 문서를 생성한다.
"""

import re
from typing import Optional

_DOCUMENT_INTENT_PATTERNS = [
    re.compile(r"(보고서|계획서|기획서|제안서|문서|리포트|요약서|분석서|전략서|매뉴얼|가이드)", re.IGNORECASE),
    re.compile(r"(작성|만들어|생성|써|줘|write|create|generate|draft|compose|prepare)", re.IGNORECASE),
    re.compile(r"(report|proposal|plan|document|summary|analysis|strategy|guide|manual|brief)", re.IGNORECASE),
]

_STRONG_INTENT_PATTERNS = [
    re.compile(r"(작성해|만들어\s*줘|써\s*줘|생성해|write\s+(?:a|me|the)|create\s+(?:a|me|the)|draft\s+(?:a|me|the))", re.IGNORECASE),
    re.compile(r"(보고서|계획서|기획서|제안서|전략서|매뉴얼).*(작성|만들|생성|써)", re.IGNORECASE),
    re.compile(r"(작성|만들|생성|써).*(보고서|계획서|기획서|제안서|전략서|매뉴얼)", re.IGNORECASE),
]


def detect_document_intent(message: str) -> bool:
    """Detect whether the user's message is requesting document generation."""
    if not message or len(message) < 5:
        return False
    for pattern in _STRONG_INTENT_PATTERNS:
        if pattern.search(message):
            return True
    hit_count = sum(1 for p in _DOCUMENT_INTENT_PATTERNS if p.search(message))
    return hit_count >= 2


DOCUMENT_GENERATION_SYSTEM_PROMPT = """당신은 사용자의 개인 AI 지식 어시스턴트 Lattice AI입니다.
사용자의 기존 지식 기반을 활용하여 고품질 문서를 생성합니다.

## 지침
1. 아래 제공된 지식 그래프 컨텍스트를 최대한 활용하세요.
2. 이전 문서의 스타일과 톤을 유지하면서 최신적이고 전문적인 문서를 작성하세요.
3. 출처는 자연스럽게 본문이나 각주에 포함하세요.
4. 사용자의 언어(한국어/영어)에 맞춰 작성하세요.
5. 구조화된 포맷(제목, 소제목, 목록 등)을 사용하세요.

## 사용자의 지식 기반

{graph_context}"""

DOCUMENT_GENERATION_FOLLOWUP_PROMPT = """당신은 사용자의 개인 AI 지식 어시스턴트 Lattice AI입니다.
이전에 생성한 문서를 사용자의 요청에 따라 수정/보완합니다.

## 이전 생성 컨텍스트

{graph_context}

## 이전 문서
{previous_document}

위 문서를 사용자의 요청에 따라 수정하세요. 기존 스타일과 톤을 유지하세요."""


def build_document_system_prompt(graph_context: str) -> str:
    if not graph_context:
        return DOCUMENT_GENERATION_SYSTEM_PROMPT.replace("{graph_context}", "(사용 가능한 지식 기반이 없습니다. 일반 지식을 활용하여 작성합니다.)")
    return DOCUMENT_GENERATION_SYSTEM_PROMPT.replace("{graph_context}", graph_context)


def build_followup_system_prompt(graph_context: str, previous_document: str) -> str:
    prompt = DOCUMENT_GENERATION_FOLLOWUP_PROMPT.replace("{graph_context}", graph_context or "(없음)")
    return prompt.replace("{previous_document}", previous_document or "(없음)")


class DocumentGenerationSession:
    """Maintains state across iterative document generation requests."""

    def __init__(self):
        self._last_context: Optional[str] = None
        self._last_document: Optional[str] = None
        self._conversation_id: Optional[str] = None

    @property
    def has_previous(self) -> bool:
        return self._last_document is not None

    def update(self, context: str, document: str, conversation_id: Optional[str] = None) -> None:
        self._last_context = context
        self._last_document = document
        if conversation_id:
            self._conversation_id = conversation_id

    def get_system_prompt(self, graph_context: str) -> str:
        if self.has_previous:
            return build_followup_system_prompt(
                graph_context or self._last_context or "",
                self._last_document or "",
            )
        return build_document_system_prompt(graph_context)

    def clear(self) -> None:
        self._last_context = None
        self._last_document = None
        self._conversation_id = None
