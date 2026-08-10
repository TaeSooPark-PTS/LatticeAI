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
from lattice_brain.self_model import DEFAULT_SUMMARY_TOKENS, summary_for_prompt

_CLEAN_RE = re.compile(r"\s+")

# Approximate-token ceiling for the document-generation context block. Mirrors
# the chat assembler's default budget so neither path can silently outgrow the
# other on the same Brain.
DEFAULT_DOCUMENT_CONTEXT_BUDGET = 2000

# ── Self-Model injection (v11.1.0) ──────────────────────────────────────────
# What the Brain knows about its owner rides along with the knowledge, so a
# generated document sounds like the person who asked for it. Three rules keep
# it from becoming another thing that can go wrong:
#   1. an empty Self-Model injects *nothing* — no header, no blank section;
#   2. the block never takes more than half the context budget, so the profile
#      can never crowd out the knowledge the request is actually about;
#   3. the returned contract is unchanged — same keys, same
#      ``context_quality`` shape, one extra trace section only when a block
#      was really injected.
SELF_MODEL_SECTION_TITLE = "사용자 프로필"
SELF_MODEL_TRACE_SOURCE = "self_model"
SELF_MODEL_SECTION_HEADER = f"### 🙋 {SELF_MODEL_SECTION_TITLE}\n\n"


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


def _self_model_budget(budget: int, limit_tokens: int) -> int:
    """Tokens the profile block may use — never more than half the budget."""
    if budget <= 0:
        return limit_tokens
    return min(limit_tokens, budget // 2)


def _self_model_block(
    kg_store,
    *,
    enabled: bool,
    budget: int,
    limit_tokens: int,
    allowed_workspaces,
) -> str:
    """Rendered profile section, or ``""`` when there is nothing to inject.

    The section header is charged to the same allowance as the summary, so the
    *rendered block* — not just its text — honours the ceiling.
    """
    allowance = _self_model_budget(budget, limit_tokens) - approx_tokens(
        SELF_MODEL_SECTION_HEADER
    )
    if not enabled or allowance <= 0:
        return ""
    summary = summary_for_prompt(
        kg_store, limit_tokens=allowance, allowed_workspaces=allowed_workspaces
    )
    if not summary:
        return ""
    return f"{SELF_MODEL_SECTION_HEADER}{summary}"


def _with_self_model(block: str, context_md: str) -> str:
    """Prepend the profile block to rendered knowledge (either may be empty)."""
    return "\n\n".join(part for part in (block, context_md) if part)


def _self_model_trace(block: str) -> List[Dict[str, Any]]:
    if not block:
        return []
    return [{
        "name": SELF_MODEL_SECTION_TITLE,
        "source": SELF_MODEL_TRACE_SOURCE,
        "approx_tokens": approx_tokens(block),
        "provenance": [],
    }]


def retrieve_context_for_generation(
    kg_store,
    query: str,
    *,
    max_results: int = 10,
    max_hops: int = 2,
    allowed_workspaces=None,
    include_legacy_global: bool = False,
    budget: int = DEFAULT_DOCUMENT_CONTEXT_BUDGET,
    include_self_model: bool = True,
    self_model_tokens: int = DEFAULT_SUMMARY_TOKENS,
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
    self_model_md = _self_model_block(
        kg_store,
        enabled=include_self_model,
        budget=budget,
        limit_tokens=self_model_tokens,
        allowed_workspaces=allowed_workspaces,
    )
    # The profile's tokens come out of the same budget, so injecting it can
    # never push the assembled context over the ceiling the caller asked for.
    # The blank line that joins the two blocks is counted too — otherwise the
    # assembled total lands one token over a tight budget.
    knowledge_budget = budget
    if self_model_md:
        knowledge_budget -= approx_tokens(f"{self_model_md}\n\n")

    results = kg_store.search_for_document_generation(query, limit=max_results, **scope_kwargs)
    if not results:
        fallback_ctx = kg_store.context_for_query(
            query,
            limit=max_results,
            **scope_kwargs,
        )
        fallback_ctx, trimmed = _fit_to_budget(fallback_ctx or "", knowledge_budget)
        # Lexical fallback: the hybrid document search found nothing, so the
        # signal says lexical_only — exactly what chat reports in the same
        # situation, never a quiet downgrade.
        quality = context_quality_signal(
            "lexical_only" if fallback_ctx else "none",
            1 if fallback_ctx else 0,
        )
        assembled = _with_self_model(self_model_md, fallback_ctx)
        return {
            "query": query,
            "context_markdown": assembled,
            "sources": [],
            "stats": {"method": "fallback", "matches": 0, "budget_trimmed": trimmed},
            "context_quality": quality,
            "trace": {
                "budget_approx_tokens": budget,
                "used_approx_tokens": approx_tokens(assembled),
                "sections": _self_model_trace(self_model_md) + [{
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
    context_md, trimmed = _fit_to_budget(context_md, knowledge_budget)
    assembled = _with_self_model(self_model_md, context_md)

    return {
        "query": query,
        "context_markdown": assembled,
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
            "used_approx_tokens": approx_tokens(assembled),
            "sections": _self_model_trace(self_model_md) + [
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
        "Audio",
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
