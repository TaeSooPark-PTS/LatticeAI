"""
Context Builder — Knowledge Graph 기반 문서 생성용 컨텍스트 조합 모듈.

retrieve_context_for_generation() 파이프라인:
  Step 1: Query → Hybrid Search (text + graph + recency)
  Step 2: Seed nodes → Multi-hop traversal (Document → Project → Concept)
  Step 3: Top-K 결과를 구조화된 Markdown Context로 변환

**Shared context contract (v9.9.6).** Chat assembles context through
:class:`lattice_brain.context.ContextAssembler` — budgeted, provenance-carrying,
with an honest ``context_quality`` signal. Document generation used to build
its own markdown with no budget and no quality signal, so the same Brain
answered a chat question and wrote a document under different guarantees.

The rendering stays different on purpose (a document prompt wants structured
sections, a chat prompt wants terse lines), but the *contract* is now one:

* the same ``approx_tokens`` accounting and an explicit budget,
* the same ``context_quality_signal`` shape chat reports,
* a ``trace`` in the assembler's shape (budget / used / sections).

Review 2026-07-27 P1 #5: "같은 Brain이면 같은 품질 계약".
"""

import re
from typing import Any, Dict, List

from lattice_brain.context import approx_tokens
from lattice_brain.graph.retrieval import context_quality_signal

_CLEAN_RE = re.compile(r"\s+")

# Approximate-token ceiling for the document-generation context block. Mirrors
# the chat assembler's default budget so neither path can silently outgrow the
# other on the same Brain.
DEFAULT_DOCUMENT_CONTEXT_BUDGET = 2000


def _clean(text: str, max_len: int = 700) -> str:
    return _CLEAN_RE.sub(" ", str(text or "")).strip()[:max_len]


def _empty_result(query: str, reason: str) -> Dict[str, Any]:
    quality = context_quality_signal("none", 0, reason=reason)
    return {
        "query": query,
        "context_markdown": "",
        "sources": [],
        "stats": {"method": "none", "matches": 0},
        "context_quality": quality,
        "trace": {
            "budget_approx_tokens": DEFAULT_DOCUMENT_CONTEXT_BUDGET,
            "used_approx_tokens": 0,
            "sections": [],
        },
    }


def _fit_to_budget(context_md: str, budget: int) -> tuple:
    """Trim rendered markdown to ``budget`` approximate tokens.

    Cuts at a section boundary when possible so the prompt never ends inside
    half an entry, and reports the trim so the trace stays honest.
    """
    if budget <= 0 or approx_tokens(context_md) <= budget:
        return context_md, False
    limit = budget * 4
    head = context_md[:limit]
    boundary = head.rfind("\n### ")
    if boundary > limit // 3:
        head = head[:boundary]
    return head.rstrip(), True


def retrieve_context_for_generation(
    kg_store,
    query: str,
    *,
    max_results: int = 10,
    max_hops: int = 2,
    allowed_workspaces=None,
    include_legacy_global: bool = False,
    budget: int = DEFAULT_DOCUMENT_CONTEXT_BUDGET,
) -> Dict[str, Any]:
    """Knowledge Graph에서 문서 생성에 필요한 컨텍스트를 검색·조합한다.

    Returns:
        {
            "query": str,
            "context_markdown": str,   # LLM 프롬프트에 직접 주입할 Markdown
            "sources": [...],          # 참조된 소스 목록
            "stats": {...},            # 검색 통계
            "context_quality": {...},  # chat과 동일한 정직 신호 (v9.9.6)
            "trace": {...},            # assembler와 동일한 예산/섹션 추적
        }
    """
    query = str(query or "").strip()
    if not query or not kg_store:
        return _empty_result(query, "문서 생성 컨텍스트를 조회할 수 없습니다")

    scope_kwargs = (
        {
            "allowed_workspaces": allowed_workspaces,
            "include_legacy_global": include_legacy_global,
        }
        if allowed_workspaces is not None
        else {}
    )
    results = kg_store.search_for_document_generation(query, limit=max_results, **scope_kwargs)
    if not results:
        fallback_ctx = kg_store.context_for_query(
            query,
            limit=max_results,
            **scope_kwargs,
        )
        fallback_ctx, trimmed = _fit_to_budget(fallback_ctx or "", budget)
        # Lexical fallback: the hybrid document search found nothing, so the
        # signal says lexical_only — exactly what chat reports in the same
        # situation, never a quiet downgrade.
        quality = context_quality_signal(
            "lexical_only" if fallback_ctx else "none",
            1 if fallback_ctx else 0,
        )
        return {
            "query": query,
            "context_markdown": fallback_ctx,
            "sources": [],
            "stats": {"method": "fallback", "matches": 0, "budget_trimmed": trimmed},
            "context_quality": quality,
            "trace": {
                "budget_approx_tokens": budget,
                "used_approx_tokens": approx_tokens(fallback_ctx),
                "sections": [{
                    "name": "Knowledge (fallback)",
                    "source": "knowledge",
                    "approx_tokens": approx_tokens(fallback_ctx),
                    "provenance": [],
                }],
            },
        }

    seed_ids = [r["id"] for r in results]
    hop_data = kg_store.multi_hop_context(
        seed_ids,
        max_hops=max_hops,
        **scope_kwargs,
    )

    extra_nodes_by_id = {}
    for node in hop_data.get("nodes", []):
        if node["id"] not in {r["id"] for r in results}:
            extra_nodes_by_id[node["id"]] = node

    sections = _build_context_sections(results, extra_nodes_by_id, hop_data.get("edges", []))
    context_md = _render_markdown(query, sections)
    sources = _extract_sources(results)
    context_md, trimmed = _fit_to_budget(context_md, budget)

    return {
        "query": query,
        "context_markdown": context_md,
        "sources": sources,
        "stats": {
            "method": "hybrid",
            "primary_matches": len(results),
            "graph_nodes": len(hop_data.get("nodes", [])),
            "graph_edges": len(hop_data.get("edges", [])),
            "budget_trimmed": trimmed,
        },
        # Same honest signal chat reports for the same Brain.
        "context_quality": context_quality_signal("hybrid", len(results)),
        "trace": {
            "budget_approx_tokens": budget,
            "used_approx_tokens": approx_tokens(context_md),
            "sections": [
                {
                    "name": section["title"],
                    "source": "knowledge",
                    "approx_tokens": approx_tokens(
                        " ".join(str(item.get("summary") or "") for item in section["items"])
                    ),
                    "provenance": [
                        {"id": item.get("id"), "type": item.get("type")}
                        for item in section["items"][:8]
                    ],
                }
                for section in sections
            ],
        },
    }


def _build_context_sections(
    primary_results: List[Dict[str, Any]],
    extra_nodes: Dict[str, Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    sections = []

    docs = [r for r in primary_results if r["type"] in (
        "Document", "File", "SlideDeck", "Spreadsheet", "CodeFile", "Image", "ImageText",
    )]
    if docs:
        sections.append({
            "title": "관련 문서/파일",
            "items": docs,
            "icon": "📄",
        })

    decisions = [r for r in primary_results if r["type"] in ("Decision", "Task")]
    if decisions:
        sections.append({
            "title": "관련 결정사항/작업",
            "items": decisions,
            "icon": "✅",
        })

    conversations = [r for r in primary_results if r["type"] == "Chat"]
    if conversations:
        sections.append({
            "title": "관련 대화",
            "items": conversations,
            "icon": "💬",
        })

    concepts = [r for r in primary_results if r["type"] in ("Concept", "Feature")]
    extra_concepts = [n for n in extra_nodes.values() if n["type"] in ("Concept", "Feature")]
    all_concepts = concepts + extra_concepts[:8]
    if all_concepts:
        sections.append({
            "title": "관련 개념/기술",
            "items": all_concepts,
            "icon": "🔗",
        })

    return sections


def _render_markdown(query: str, sections: List[Dict[str, Any]]) -> str:
    lines = []
    for section in sections:
        if not section["items"]:
            continue
        lines.append(f"### {section['icon']} {section['title']}")
        lines.append("")
        for item in section["items"][:8]:
            title = item.get("title", "")
            summary = _clean(item.get("summary", ""))
            item_type = item.get("type", "")
            score_info = ""
            if "hybrid_score" in item:
                score_info = f" (relevance: {item['hybrid_score']:.2f})"

            meta = item.get("metadata") or {}
            source = (
                meta.get("relative_path")
                or meta.get("filename")
                or meta.get("conversation_id")
                or meta.get("source")
                or item.get("id", "")
            )

            lines.append(f"- **[{item_type}] {title}**{score_info}")
            if source and source != item.get("id", ""):
                lines.append(f"  - 출처: {source}")
            if summary:
                lines.append(f"  - {summary}")

            related = item.get("related_concepts", [])
            if related:
                tags = ", ".join(c["title"] for c in related[:5])
                lines.append(f"  - 관련: {tags}")

            lines.append("")

    return "\n".join(lines).strip()


def _extract_sources(results: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    sources = []
    seen = set()
    for r in results:
        meta = r.get("metadata") or {}
        source_key = (
            meta.get("relative_path")
            or meta.get("filename")
            or meta.get("conversation_id")
            or r.get("id", "")
        )
        if source_key and source_key not in seen:
            seen.add(source_key)
            sources.append({
                "id": r.get("id", ""),
                "type": r.get("type", ""),
                "title": r.get("title", ""),
                "source": source_key,
            })
    return sources


def format_sources_footnote(sources: List[Dict[str, str]]) -> str:
    if not sources:
        return ""
    lines = ["\n---\n**참조된 지식 그래프 노드:**"]
    for i, src in enumerate(sources[:10], 1):
        lines.append(f"{i}. [{src['type']}] {src['title']} ({src['source']})")
    return "\n".join(lines)
