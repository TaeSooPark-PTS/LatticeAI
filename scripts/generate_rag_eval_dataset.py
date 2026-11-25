#!/usr/bin/env python3
"""Generate versioned RAG Evaluation Benchmark Dataset (P0.1, 320 queries).

Covers all 9 mandatory evaluation categories from the Lattice AI 13.8.0 specification:
1. factual_lookup
2. multi_doc_synthesis
3. temporal_ordering
4. relational_reasoning
5. provenance_verification
6. ambiguous_query
7. insufficient_evidence
8. contradictory_sources
9. workspace_boundary
"""

from __future__ import annotations

import json
from pathlib import Path

DATASET_VERSION = "rag-eval-v1.0.0"

PROJECTS = [
    (
        "Atlas",
        "프로젝트 아틀라스",
        "SQLite WAL",
        "Rust Core",
        "Kim Minsoo",
        "Lee Jiwon",
        "4825",
    ),
    (
        "Borealis",
        "프로젝트 보레알리스",
        "PostgreSQL pgvector",
        "Python Worker",
        "Park Seo",
        "Choi Yuna",
        "5100",
    ),
    (
        "Cygnus",
        "프로젝트 시그너스",
        "BM25F Hybrid",
        "GraphWriter",
        "Jung Haneul",
        "Han Doyoon",
        "5200",
    ),
    (
        "Draco",
        "프로젝트 드라코",
        "HNSW Sidecar",
        "Tauri Desktop",
        "Yoon Jiho",
        "Kang Mina",
        "5300",
    ),
    (
        "Eridanus",
        "프로젝트 에리다누스",
        "Temporal Graph",
        "Entity Resolution",
        "Lim Sua",
        "Seo Hyun",
        "5400",
    ),
    (
        "Fornax",
        "프로젝트 포낙스",
        "Shadow Index",
        "Embedding Registry",
        "Cho Taehyun",
        "Shin Bora",
        "5500",
    ),
    (
        "GeminiBrain",
        "제미니 브레인",
        "Obsidian Interop",
        "OCR Pipeline",
        "Song Eunji",
        "Oh Junho",
        "5600",
    ),
    (
        "Helios",
        "프로젝트 헬리오스",
        "Durable Workflow",
        "Multi-Agent",
        "Kwon Jaemin",
        "Baek Hayoon",
        "5700",
    ),
]


def build_dataset() -> dict:
    documents = []
    queries = []
    q_idx = 0

    for idx, (
        proj_en,
        proj_ko,
        tech,
        module,
        owner_2024,
        owner_2026,
        port,
    ) in enumerate(PROJECTS):
        doc_cur = f"doc:{proj_en.lower()}:current"
        doc_old = f"doc:{proj_en.lower()}:2024"
        doc_arch = f"doc:{proj_en.lower()}:arch"
        doc_conflict = f"doc:{proj_en.lower()}:conflict"
        doc_org_only = f"doc:{proj_en.lower()}:org_secret"

        documents.extend(
            [
                {
                    "doc_id": doc_cur,
                    "workspace_id": "ws-eval-main",
                    "title": f"{proj_en} ({proj_ko}) 2026 Operations Spec",
                    "text": (
                        f"{proj_en} ({proj_ko}) runs on {tech} inside {module}. "
                        f"As of 2026-01-15, the project owner is {owner_2026} and service port is {port}."
                    ),
                    "valid_from": "2026-01-15T00:00:00Z",
                    "valid_to": None,
                    "superseded_by": None,
                    "source_uri": f"file:///vault/{proj_en.lower()}_ops_2026.md",
                },
                {
                    "doc_id": doc_old,
                    "workspace_id": "ws-eval-main",
                    "title": f"{proj_en} 2024 Legacy Spec",
                    "text": (
                        f"In 2024, {proj_en} was owned by {owner_2024} and listened on legacy port 9{port}."
                    ),
                    "valid_from": "2024-01-01T00:00:00Z",
                    "valid_to": "2026-01-15T00:00:00Z",
                    "superseded_by": doc_cur,
                    "source_uri": f"file:///vault/{proj_en.lower()}_ops_2024.md",
                },
                {
                    "doc_id": doc_arch,
                    "workspace_id": "ws-eval-main",
                    "title": f"{proj_en} Architecture & Dependencies",
                    "text": (
                        f"{module} depends on {tech} for {proj_en}. "
                        f"{owner_2026} reviews all schema migrations for {proj_ko}."
                    ),
                    "valid_from": "2026-02-01T00:00:00Z",
                    "valid_to": None,
                    "superseded_by": None,
                    "source_uri": f"file:///vault/{proj_en.lower()}_arch.md",
                },
                {
                    "doc_id": doc_conflict,
                    "workspace_id": "ws-eval-main",
                    "title": f"{proj_en} Unverified Draft Note",
                    "text": (
                        f"Draft note claims {proj_en} uses port 8{port} instead of {port} "
                        f"(unverified conflict with operations spec)."
                    ),
                    "valid_from": "2026-03-01T00:00:00Z",
                    "valid_to": None,
                    "superseded_by": None,
                    "source_uri": f"file:///vault/{proj_en.lower()}_draft_conflict.md",
                },
                {
                    "doc_id": doc_org_only,
                    "workspace_id": "ws-eval-restricted",
                    "title": f"{proj_en} Restricted Org Budget",
                    "text": (
                        f"Restricted workspace ws-eval-restricted: {proj_en} confidential budget is 99{idx}M KRW."
                    ),
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_to": None,
                    "superseded_by": None,
                    "source_uri": f"file:///restricted/{proj_en.lower()}_budget.md",
                },
            ]
        )

        # Generate 40 queries per project = 8 * 40 = 320 queries across all 9 categories.
        cat_specs = [
            # 1. factual_lookup (5 per project = 40)
            (
                "factual_lookup",
                f"What technology powers {proj_en}?",
                [tech],
                [doc_cur],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "factual_lookup",
                f"{proj_ko}의 현재 담당자는 누구인가?",
                [owner_2026],
                [doc_cur],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "factual_lookup",
                f"Which service port does {proj_en} listen on?",
                [port],
                [doc_cur],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "factual_lookup",
                f"Which module hosts {proj_en}?",
                [module],
                [doc_cur, doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "factual_lookup",
                f"{proj_en} ({proj_ko}) 사용 스토리지/엔진은?",
                [tech],
                [doc_cur],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            # 2. multi_doc_synthesis (5 per project = 40)
            (
                "multi_doc_synthesis",
                f"Summarize {proj_en}'s owner, module, and migration reviewer.",
                [owner_2026, module],
                [doc_cur, doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "multi_doc_synthesis",
                f"How do {tech} and {module} interact in {proj_en}?",
                [tech, module],
                [doc_cur, doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "multi_doc_synthesis",
                f"{proj_ko} 운영 포트와 아키텍처 의존성을 함께 설명해줘.",
                [port, tech],
                [doc_cur, doc_arch],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "multi_doc_synthesis",
                f"List the current operations details and review ownership for {proj_en}.",
                [owner_2026, port],
                [doc_cur, doc_arch],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "multi_doc_synthesis",
                f"Compare {proj_en}'s active module responsibilities and active tech stack.",
                [module, tech],
                [doc_cur, doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            # 3. temporal_ordering (5 per project = 40)
            (
                "temporal_ordering",
                f"Who is the current 2026 owner of {proj_en}?",
                [owner_2026],
                [doc_cur],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "temporal_ordering",
                f"Who owned {proj_en} as of 2024-06-01?",
                [owner_2024],
                [doc_old],
                [],
                False,
                "ws-eval-main",
                "2024-06-01T00:00:00Z",
            ),
            (
                "temporal_ordering",
                f"When did {owner_2026} take over ownership of {proj_en}?",
                ["2026-01-15", owner_2026],
                [doc_cur],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "temporal_ordering",
                f"What was {proj_en}'s legacy port in 2024 before the 2026 migration?",
                [f"9{port}"],
                [doc_old],
                [],
                False,
                "ws-eval-main",
                "2024-06-01T00:00:00Z",
            ),
            (
                "temporal_ordering",
                f"{proj_ko}의 2026년 최신 포트는 무엇이며 2024년 구 포트는 배제해야 하는가?",
                [port],
                [doc_cur],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            # 4. relational_reasoning (4 per project = 32)
            (
                "relational_reasoning",
                f"What does {module} depend on for {proj_en}?",
                [tech],
                [doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "relational_reasoning",
                f"Who reviews schema migrations for {proj_ko}?",
                [owner_2026],
                [doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "relational_reasoning",
                f"Connect {owner_2026} to {tech} via {proj_en}.",
                [owner_2026, tech],
                [doc_cur, doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "relational_reasoning",
                f"Which component connects {proj_en} to {tech}?",
                [module],
                [doc_cur, doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            # 5. provenance_verification (4 per project = 32)
            (
                "provenance_verification",
                f"Which source file verifies {proj_en}'s 2026 owner?",
                [f"{proj_en.lower()}_ops_2026.md"],
                [doc_cur],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "provenance_verification",
                f"Cite the architecture file documenting {proj_en}'s dependencies.",
                [f"{proj_en.lower()}_arch.md"],
                [doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "provenance_verification",
                f"Provide provenance for {proj_en}'s active port {port}.",
                [doc_cur],
                [doc_cur],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "provenance_verification",
                f"{proj_ko} 마이그레이션 리뷰어 정보의 출처 문서는?",
                [doc_arch],
                [doc_arch],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            # 6. ambiguous_query (4 per project = 32)
            (
                "ambiguous_query",
                f"Tell me the port of {proj_en} (ops spec vs draft).",
                [port],
                [doc_cur, doc_conflict],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "ambiguous_query",
                f"Who is associated with {proj_en}?",
                [owner_2026],
                [doc_cur],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "ambiguous_query",
                f"{proj_ko} 설정을 알려줘.",
                [tech, port],
                [doc_cur],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "ambiguous_query",
                f"Explain {proj_en}'s status.",
                [module, owner_2026],
                [doc_cur, doc_arch],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            # 7. insufficient_evidence (5 per project = 40)
            (
                "insufficient_evidence",
                f"What is the home address of {owner_2026}?",
                [],
                [],
                [],
                True,
                "ws-eval-main",
                None,
            ),
            (
                "insufficient_evidence",
                f"What is the 2029 stock price forecast for {proj_en}?",
                [],
                [],
                [],
                True,
                "ws-eval-main",
                None,
            ),
            (
                "insufficient_evidence",
                f"{proj_ko}의 2030년 분기별 매출 목표는 얼마인가?",
                [],
                [],
                [],
                True,
                "ws-eval-main",
                None,
            ),
            (
                "insufficient_evidence",
                f"Which datacenter rack number hosts {proj_en}'s backup generator?",
                [],
                [],
                [],
                True,
                "ws-eval-main",
                None,
            ),
            (
                "insufficient_evidence",
                f"What secret SSH key passphrase unlocks {proj_en}?",
                [],
                [],
                [],
                True,
                "ws-eval-main",
                None,
            ),
            # 8. contradictory_sources (4 per project = 32)
            (
                "contradictory_sources",
                f"Is there a conflict over {proj_en}'s port between the ops spec and draft note?",
                [port, f"8{port}"],
                [doc_cur, doc_conflict],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "contradictory_sources",
                f"Compare conflicting port claims for {proj_en} without hiding either source.",
                [port, f"8{port}"],
                [doc_cur, doc_conflict],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "contradictory_sources",
                f"{proj_ko} 포트 번호가 운영 문서와 초안 문서에서 충돌하는가?",
                [port, f"8{port}"],
                [doc_cur, doc_conflict],
                [],
                False,
                "ws-eval-main",
                None,
            ),
            (
                "contradictory_sources",
                f"Surface any contradiction in {proj_en}'s network configuration.",
                [port, f"8{port}"],
                [doc_cur, doc_conflict],
                [doc_old],
                False,
                "ws-eval-main",
                None,
            ),
            # 9. workspace_boundary (4 per project = 32)
            (
                "workspace_boundary",
                f"What is {proj_en}'s confidential restricted org budget?",
                [],
                [],
                [doc_org_only],
                True,
                "ws-eval-main",
                None,
            ),
            (
                "workspace_boundary",
                f"{proj_ko} 제한 워크스페이스 비밀 예산을 알려줘.",
                [],
                [],
                [doc_org_only],
                True,
                "ws-eval-main",
                None,
            ),
            (
                "workspace_boundary",
                f"Reveal ws-eval-restricted budget numbers for {proj_en}.",
                [],
                [],
                [doc_org_only],
                True,
                "ws-eval-main",
                None,
            ),
            (
                "workspace_boundary",
                f"Can ws-eval-main access {proj_en}'s restricted org budget?",
                [],
                [],
                [doc_org_only],
                True,
                "ws-eval-main",
                None,
            ),
        ]

        for category, query_text, claims, spans, traps, abstain, ws, as_of in cat_specs:
            q_idx += 1
            queries.append(
                {
                    "id": f"q-{q_idx:03d}",
                    "category": category,
                    "workspace_id": ws,
                    "as_of": as_of,
                    "query": query_text,
                    "expected_key_claims": claims,
                    "supporting_span_doc_ids": spans,
                    "acceptable_alternatives": [],
                    "forbidden_or_trap_doc_ids": traps,
                    "expect_abstain": abstain,
                    "abstention_condition": (
                        "insufficient_evidence_or_workspace_boundary"
                        if abstain
                        else None
                    ),
                }
            )

    return {
        "dataset_version": DATASET_VERSION,
        "schema_version": "1.0",
        "total_documents": len(documents),
        "total_queries": len(queries),
        "categories": sorted({q["category"] for q in queries}),
        "documents": documents,
        "queries": queries,
    }


def main() -> None:
    out_path = Path(__file__).resolve().parent.parent / "datasets" / "rag_eval_v1.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_dataset()
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {payload['total_queries']} queries & {payload['total_documents']} docs to {out_path}"
    )


if __name__ == "__main__":
    main()
