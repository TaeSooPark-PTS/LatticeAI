"""Built-in First Value Loop demo corpus (backlog #3, review §3.3 P0).

Three small Korean-friendly documents shipped in-repo so a brand-new user can
one-click seed the Brain and immediately experience a successful recall with
source highlighting — no folder connection, no model download, fully offline.

Contract:

* Documents are ingested through the normal :class:`IngestionPipeline`
  (``source_type="note"``) with ``source_uri = demo://<id>`` and metadata
  ``{"demo_corpus": true}`` so they are identifiable and removable later.
* Ingestion is idempotent — the graph layer dedupes by content hash, so
  re-POSTing never duplicates documents.
* :data:`SUGGESTED_QUESTIONS` are pre-filled questions whose answers exist in
  the corpus (the UI renders them as "ask this" chips).
"""

from __future__ import annotations

from typing import Any, Dict, List

DEMO_URI_PREFIX = "demo://"
DEMO_METADATA_FLAG = "demo_corpus"

DEMO_DOCUMENTS: List[Dict[str, str]] = [
    {
        "id": "meeting-note",
        "title": "주간 회의록 — 사이드 프로젝트 킥오프",
        "text": (
            "2026-07-20 주간 회의록.\n"
            "참석: 나, 김민준(백엔드), 박서연(디자인).\n"
            "핵심 결정: 사이드 프로젝트 '새싹 가든'의 첫 공개 버전을 8월 15일에 "
            "출시하기로 결정했다. 범위는 식물 기록과 물주기 알림 두 가지로 줄인다.\n"
            "김민준이 알림 백엔드를 맡고, 박서연이 온보딩 화면을 맡는다.\n"
            "다음 회의 전까지 각자 프로토타입을 준비하기로 했다."
        ),
    },
    {
        "id": "project-doc",
        "title": "프로젝트 개요 — 새싹 가든",
        "text": (
            "새싹 가든은 집에서 키우는 식물을 기록하는 작은 앱이다.\n"
            "기술 스택: 프론트엔드는 React, 백엔드는 FastAPI, 데이터는 SQLite에 "
            "로컬로 저장한다. 사진은 기기 밖으로 나가지 않는다.\n"
            "첫 버전 목표: 식물 등록, 물주기 알림, 한 줄 관찰 일기.\n"
            "수익화는 생각하지 않고, 주말에 만드는 것을 원칙으로 한다."
        ),
    },
    {
        "id": "personal-note",
        "title": "개인 노트 — 독서 메모: 아주 작은 습관의 힘",
        "text": (
            "『아주 작은 습관의 힘』을 읽고 남긴 메모.\n"
            "가장 기억에 남는 문장: 습관은 목표가 아니라 시스템으로 만들어진다.\n"
            "적용해 볼 것: 매일 아침 10분 스트레칭을 양치 직후에 붙여서 시작한다.\n"
            "핵심은 2분 규칙 — 새 습관은 2분 안에 끝나는 크기로 시작하는 것이다."
        ),
    },
]

# Pre-filled questions whose answers exist in the corpus above. ``source_uri``
# lets the UI verify the recall cited the right demo document.
SUGGESTED_QUESTIONS: List[Dict[str, str]] = [
    {
        "question": "회의에서 결정한 출시일이 언제야?",
        "expected_source_uri": DEMO_URI_PREFIX + "meeting-note",
        "expected_title": "주간 회의록 — 사이드 프로젝트 킥오프",
    },
    {
        "question": "새싹 가든의 기술 스택이 뭐야?",
        "expected_source_uri": DEMO_URI_PREFIX + "project-doc",
        "expected_title": "프로젝트 개요 — 새싹 가든",
    },
    {
        "question": "새 습관을 시작할 때 쓰는 2분 규칙이 뭐였지?",
        "expected_source_uri": DEMO_URI_PREFIX + "personal-note",
        "expected_title": "개인 노트 — 독서 메모: 아주 작은 습관의 힘",
    },
]


def demo_source_uri(doc_id: str) -> str:
    return DEMO_URI_PREFIX + str(doc_id)


def suggested_questions() -> List[Dict[str, Any]]:
    """Copy of the suggestion chips (callers may annotate freely)."""
    return [dict(item) for item in SUGGESTED_QUESTIONS]


__all__ = [
    "DEMO_DOCUMENTS",
    "DEMO_METADATA_FLAG",
    "DEMO_URI_PREFIX",
    "SUGGESTED_QUESTIONS",
    "demo_source_uri",
    "suggested_questions",
]
