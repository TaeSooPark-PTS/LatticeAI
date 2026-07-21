"""Query-class retrieval fusion benchmark fixture (backlog #5).

A small deterministic corpus + judged queries covering the four fusion query
classes (fact / code / person / recency). ``tests/unit/
test_retrieval_fusion_gate.py`` ingests DOCUMENTS into a temp
KnowledgeGraphStore, runs the class-aware hybrid fusion per query, and fails
CI when precision@k / must-include hit rate regress below THRESHOLDS.

Every document ships enough distinctive ko/en lexical signal that ranking is
stable with the offline hash embedder — this gate guards fusion wiring
regressions, not embedding-model quality.
"""

from __future__ import annotations

FIXTURE_NAME = "9.9.x-query-class-fusion-gate"
TOP_K = 5

# Gate thresholds (fail CI below these). Most queries carry exactly one
# relevant doc, so precision@5 is structurally capped near 1/5 per query —
# the current wiring scores ~0.244 (near-perfect); 0.20 marks a regression
# where relevant docs start dropping out of the top-k. Classification is
# deterministic and therefore pinned at 1.0.
THRESHOLDS = {
    "precision@k": 0.20,
    "recall@k": 0.75,
    "must_include_hit_rate": 0.90,
    "query_class_accuracy": 1.0,
}

DOCUMENTS = [
    # ── fact ────────────────────────────────────────────────────────────────
    {
        "id": "bench:release-decision",
        "title": "출시 결정 회의록",
        "text": (
            "Lattice AI 9.10 출시 결정: 데모 코퍼스 온보딩과 retrieval fusion "
            "게이트를 포함해 출시하기로 결정했다. 출시 일정은 품질 게이트 통과가 "
            "전제 조건이다."
        ),
    },
    {
        "id": "bench:architecture-note",
        "title": "하이브리드 검색 아키텍처 노트",
        "text": (
            "하이브리드 검색은 keyword lexical 채널과 vector 채널, graph 채널을 "
            "가중치 융합한다. fusion weights는 쿼리 클래스별로 다르게 적용된다."
        ),
    },
    {
        "id": "bench:privacy-policy",
        "title": "로컬 우선 프라이버시 원칙",
        "text": (
            "Lattice AI는 로컬 우선이다. 지식 그래프와 임베딩은 사용자의 기기에 "
            "저장되고 클라우드 업로드는 명시적 동의 없이는 일어나지 않는다."
        ),
    },
    # ── code ────────────────────────────────────────────────────────────────
    {
        "id": "bench:ingest-folder-bug",
        "title": "ingest_folder 재귀 버그 수정",
        "text": (
            "ingest_folder 함수의 recursive 처리에서 .latticeignore 패턴이 "
            "무시되는 버그를 수정했다. os.walk의 dirnames pruning 코드가 원인이었다."
        ),
    },
    {
        "id": "bench:hybrid-search-func",
        "title": "hybrid_search 함수 설계",
        "text": (
            "hybrid_search 함수는 alpha 가중치로 vector_search 점수와 lexical "
            "점수를 융합한다. score 0은 falsy이므로 or 기본값 코드를 쓰면 안 된다."
        ),
    },
    # ── person ──────────────────────────────────────────────────────────────
    {
        "id": "bench:person-minjun",
        "title": "김민준 — 백엔드 담당자",
        "text": (
            "김민준 님은 백엔드 담당자다. 지식 그래프 파이프라인과 수집 품질을 "
            "맡고 있으며 회의에서 출시 게이트 지표를 보고한다."
        ),
    },
    {
        "id": "bench:person-seoyeon",
        "title": "박서연 — 디자이너",
        "text": (
            "박서연 님은 제품 디자이너다. 온보딩 화면과 출처 하이라이트 UI를 "
            "담당하고 접근성 검수도 함께 진행한다."
        ),
    },
    # ── recency ─────────────────────────────────────────────────────────────
    {
        "id": "bench:yesterday-meeting",
        "title": "어제 회의 요약",
        "text": (
            "어제 회의에서는 폴더 watch 모드를 옵트인으로 만들기로 합의했다. "
            "기본값은 꺼짐이고 명시적 동의 후에만 폴링이 시작된다."
        ),
    },
    {
        "id": "bench:lastweek-deploy",
        "title": "지난주 배포 기록",
        "text": (
            "지난주 배포에서는 번들 크기를 22% 줄였고 verifier fail-closed "
            "동작을 릴리스했다. 배포 후 회귀는 발견되지 않았다."
        ),
    },
    # ── distractors (never relevant) ────────────────────────────────────────
    {
        "id": "bench:distractor-lunch",
        "title": "점심 메뉴 메모",
        "text": "수요일 점심은 비빔밥, 목요일은 파스타. 카페는 2층이 한산하다.",
    },
    {
        "id": "bench:distractor-travel",
        "title": "여행 계획 초안",
        "text": "제주도 숙소 후보 세 곳과 렌터카 예약 링크를 모아 두었다.",
    },
    {
        "id": "bench:distractor-billing",
        "title": "구독 영수증 정리",
        "text": "월간 구독 영수증과 세금 계산서를 폴더에 정리했다.",
    },
    {
        "id": "bench:distractor-theme",
        "title": "테마 색상 아이디어",
        "text": "다크 테마 배경은 잉크 톤, 포인트는 옥빛 계열이 어울린다.",
    },
]

# Judged queries. ``query_class`` is the label the classifier must reproduce;
# ``relevant`` are graded fixture ids; ``must_include`` must appear in top-k.
QUERIES = [
    {
        "query": "출시 결정 내용이 뭐였지",
        "query_class": "fact",
        "relevant": {"bench:release-decision": 3},
        "must_include": ["bench:release-decision"],
    },
    {
        "query": "하이브리드 검색 아키텍처 가중치 융합",
        "query_class": "fact",
        "relevant": {"bench:architecture-note": 3, "bench:hybrid-search-func": 1},
        "must_include": ["bench:architecture-note"],
    },
    {
        "query": "프라이버시 로컬 우선 원칙",
        "query_class": "fact",
        "relevant": {"bench:privacy-policy": 3},
        "must_include": ["bench:privacy-policy"],
    },
    {
        "query": "ingest_folder recursive 버그 원인",
        "query_class": "code",
        "relevant": {"bench:ingest-folder-bug": 3},
        "must_include": ["bench:ingest-folder-bug"],
    },
    {
        "query": "hybrid_search alpha 융합 코드",
        "query_class": "code",
        "relevant": {"bench:hybrid-search-func": 3, "bench:architecture-note": 1},
        "must_include": ["bench:hybrid-search-func"],
    },
    {
        "query": "백엔드 담당자 누구야",
        "query_class": "person",
        "relevant": {"bench:person-minjun": 3},
        "must_include": ["bench:person-minjun"],
    },
    {
        "query": "온보딩 UI 디자인은 어떤 사람이 맡았어",
        "query_class": "person",
        "relevant": {"bench:person-seoyeon": 3},
        "must_include": ["bench:person-seoyeon"],
    },
    {
        "query": "어제 회의에서 합의한 것",
        "query_class": "recency",
        "relevant": {"bench:yesterday-meeting": 3},
        "must_include": ["bench:yesterday-meeting"],
    },
    {
        "query": "지난주 배포에서 바뀐 것",
        "query_class": "recency",
        "relevant": {"bench:lastweek-deploy": 3},
        "must_include": ["bench:lastweek-deploy"],
    },
]

__all__ = ["DOCUMENTS", "FIXTURE_NAME", "QUERIES", "THRESHOLDS", "TOP_K"]
