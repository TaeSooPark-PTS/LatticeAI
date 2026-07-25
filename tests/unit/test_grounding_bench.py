"""Synthetic grounding benchmark gate (review 2026-07-25 Wave 2.3).

A deterministic, fixture-only benchmark for
:func:`latticeai.api.chat_helpers.assess_answer_grounding`: eleven ko/en cases
covering clearly-supported answers (explicit title citation / strong token
overlap), clearly-unsupported answers (confident prose with zero source
overlap), empty-retrieval ``no_context``, and one paraphrase case that
documents the current boundary of the heuristic. The aggregate accuracy gate
(``BENCH_MIN_ACCURACY``) fails the suite when the heuristic regresses on any
case. No model, no network, no randomness — the grounding verdict stays an
annotation (it never blocks or modifies answers); this bench only pins the
annotation's behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.chat_helpers import (
    GROUNDING_MIN_OVERLAP_RATIO,
    GROUNDING_MIN_OVERLAP_TOKENS,
    assess_answer_grounding,
)

# ── bench gate thresholds (review 2026-07-25 Wave 2.3) ───────────────────────
# Every fixture verdict must match: the bench is a regression gate, so the
# required aggregate accuracy over BENCH_CASES is exactly 1.0.
BENCH_MIN_ACCURACY = 1.0
# The heuristic thresholds under test, re-exported for documentation: an
# answer binds to a source via explicit title citation OR >= 2 shared content
# tokens at >= 0.08 overlap ratio (see chat_helpers.assess_answer_grounding).
BENCH_HEURISTIC_MIN_TOKENS = GROUNDING_MIN_OVERLAP_TOKENS
BENCH_HEURISTIC_MIN_RATIO = GROUNDING_MIN_OVERLAP_RATIO


def _trace(nodes):
    return {"graph_nodes": list(nodes), "source_files": []}


MEETING = {
    "id": "doc:meeting",
    "title": "주간 회의록",
    "summary": "출시일을 8월 15일로 확정했다. 범위는 식물 기록과 물주기 알림.",
}
STACK = {
    "id": "doc:stack",
    "title": "프로젝트 개요",
    "summary": "프론트엔드는 React, 백엔드는 FastAPI, 데이터는 SQLite 로컬 저장.",
}
RUNBOOK = {
    "id": "doc:runbook",
    "title": "Deployment Runbook",
    "summary": "Roll out with blue-green deployment, verify health checks, then shift traffic gradually.",
}
MONITORING = {
    "id": "doc:monitoring",
    "title": "Server Monitoring Guide",
    "summary": "Prometheus scrapes node exporter metrics every 15 seconds and Grafana renders the dashboards.",
}
# Long source (37 content tokens) for the paraphrase-boundary case below.
ONBOARDING = {
    "id": "doc:onboarding",
    "title": "온보딩 절차 정리",
    "summary": (
        "신규 입사자는 첫날 보안 교육을 수강하고 둘째 날 개발 장비를 수령한다. "
        "셋째 날에는 코드베이스 투어와 멘토 배정이 진행되며, 첫 주 안에 "
        "스테이징 배포 권한 신청서를 제출해야 한다. 둘째 주부터는 온콜 "
        "로테이션 참관을 시작하고, 한 달 안에 작은 버그 수정 과제를 완료한다."
    ),
}

# Each case: (case_id, answer, retrieved nodes, expected status, expected
# bound source id or None). Expectations were pinned by running the heuristic
# — the bench asserts observed behavior, including its documented limits.
BENCH_CASES = [
    (
        "ko_title_citation",
        "자세한 내용은 주간 회의록 문서를 참고하세요 [1].",
        [MEETING],
        "supported",
        "doc:meeting",
    ),
    (
        "ko_strong_overlap",
        "회의에서 출시일을 8월 15일로 확정했고, 범위는 식물 기록과 물주기 알림입니다 [1].",
        [MEETING, STACK],
        "supported",
        "doc:meeting",
    ),
    (
        "en_title_citation",
        "See the Deployment Runbook for the exact rollout order [1].",
        [RUNBOOK],
        "supported",
        "doc:runbook",
    ),
    (
        "en_strong_overlap",
        "Prometheus scrapes the node exporter metrics and Grafana renders the dashboards [1].",
        [MONITORING, RUNBOOK],
        "supported",
        "doc:monitoring",
    ),
    (
        "ko_unsupported_confident_prose",
        "고대 로마의 수도교는 중력만으로 도시까지 물을 운반했습니다.",
        [MEETING, STACK],
        "unsupported",
        None,
    ),
    (
        "en_unsupported_confident_prose",
        "Photosynthesis converts sunlight into chemical energy stored as glucose molecules.",
        [RUNBOOK, MONITORING],
        "unsupported",
        None,
    ),
    (
        "ko_no_context",
        "질문하신 내용은 저장된 자료에 없습니다.",
        [],
        "no_context",
        None,
    ),
    (
        "en_no_context",
        "I could not find that in your knowledge base.",
        [],
        "no_context",
        None,
    ),
    (
        "empty_answer_with_sources",
        "",
        [MEETING],
        "unsupported",
        None,
    ),
    (
        "ko_multi_source_binds_correct_doc",
        "출시일을 8월 15일로 확정했고 물주기 알림이 포함됩니다 [1].",
        [STACK, MEETING],
        "supported",
        "doc:meeting",
    ),
    # KNOWN LIMITATION (documented boundary, observed — not desired behavior):
    # a faithful summary that *paraphrases* a long source shares only 2
    # content tokens (온보딩, 절차) with its 37-token body. That clears the
    # 2-token floor but lands at ratio 2/37 ≈ 0.054 < 0.08, so the token
    # heuristic labels this genuinely grounded summary "unsupported". A future
    # semantic-overlap upgrade should flip this case to "supported".
    (
        "ko_paraphrase_summary_boundary",
        "새로 합류한 직원이 초기 교육부터 실무 과제까지 마치는 온보딩 절차 요약입니다.",
        [ONBOARDING],
        "unsupported",
        None,
    ),
]


def _run_case(case):
    case_id, answer, nodes, expected_status, expected_source = case
    verdict = assess_answer_grounding(
        answer,
        trace=_trace(nodes),
        context_quality={
            "mode": "hybrid" if nodes else "none",
            "nodes": len(nodes),
            "limited": False,
        },
    )
    return verdict, expected_status, expected_source


@pytest.mark.parametrize("case", BENCH_CASES, ids=[case[0] for case in BENCH_CASES])
def test_grounding_bench_case(case):
    verdict, expected_status, expected_source = _run_case(case)
    assert verdict["status"] == expected_status
    if expected_source is not None:
        assert expected_source in verdict["source_ids"]
    else:
        assert verdict["source_ids"] == []


def test_grounding_bench_aggregate_accuracy_gate():
    """The bench gate: aggregate verdict accuracy must be BENCH_MIN_ACCURACY."""
    correct = sum(
        1
        for case in BENCH_CASES
        if _run_case(case)[0]["status"] == case[3]
    )
    accuracy = correct / len(BENCH_CASES)
    assert accuracy == BENCH_MIN_ACCURACY, (
        f"grounding bench accuracy {accuracy:.2f} "
        f"({correct}/{len(BENCH_CASES)}) below gate {BENCH_MIN_ACCURACY}"
    )


def test_paraphrase_boundary_case_shares_two_tokens_but_misses_ratio():
    """Pin the *mechanism* of the documented limitation, not just its verdict.

    The paraphrase summary clears the shared-token floor (>= 2 tokens) yet
    stays under the overlap-ratio threshold against its long source, which is
    exactly why the heuristic reports it unsupported. If either threshold or
    the tokenizer changes this test localizes the drift.
    """
    verdict, _, _ = _run_case(BENCH_CASES[-1])
    assert verdict["status"] == "unsupported"
    assert 0.0 < verdict["overlap"] < BENCH_HEURISTIC_MIN_RATIO
