"""Lattice AI Auto Graph Curator.

피드백 #4 (lattice_ai_auto_graph_direction.txt) 반영.

핵심 방향:
- 사용자는 노드/엣지를 직접 만들지 않는다.
- 대화/파일/작업 로그 → topic candidate → cluster → promoted node
  → derived thread edge → 자동 레이아웃.
- 너무 많은 노드를 만들지 않고, 알리아스를 자동 병합.
- secret/API key/private key 같은 원문은 그래프에 들어가면 안 된다.

이 모듈은 텍스트 단위 토픽 후보 추출, 클러스터링/병합, 노드 승격 판정,
파생 이야기 엣지 생성, 큐레이션(중요도 점수)을 담당하는 가벼운 헬퍼다.
무거운 의존성 없이 동작하므로 기존 knowledge_graph.py 위에 얹어 쓸 수 있다.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


# ── Secret / sensitive patterns to NEVER include in graph ─────────────────────

SECRET_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?i)\b(?:api[_-]?key|secret|access[_-]?token|password|passwd|pwd|bearer)\s*[:=]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),  # GitHub PAT
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack token
]


def contains_secret(text: str) -> bool:
    if not text:
        return False
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


def mask_secrets(text: str) -> str:
    """문자열 안의 secret을 마스킹한다. 그래프 저장 직전에 한 번 더 거쳐야 한다."""
    if not text:
        return text
    out = text
    for pat in SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


# ── Stopwords (KO + EN) ───────────────────────────────────────────────────────

_STOPWORDS: Set[str] = {
    # 한국어
    "그리고", "그러나", "또한", "하지만", "그런데", "그래서", "이것", "저것",
    "이번", "저번", "지금", "어제", "오늘", "내일", "에서", "에게", "에는",
    "되어", "있다", "없다", "있는", "없는", "같은", "처럼", "위해", "통해",
    "에서의", "에서는", "라고", "이라고", "이다", "이며", "이고", "되는",
    # 영어
    "the", "and", "for", "are", "but", "not", "you", "can", "with", "this",
    "that", "from", "into", "have", "has", "your", "any", "all", "one", "out",
    "use", "using", "used", "about", "via", "per", "let", "let's", "we'll",
    "i'll", "as", "be", "is", "it", "an", "or", "to", "of", "in", "on",
}

# item 5: 한국어 그래프 노이즈를 줄이기 위한 일반어 blacklist 강화.
# 의미를 담지 않는 흔한 단어들. (코드/도메인 고유명사는 제외)
_GENERIC_BLACKLIST: Set[str] = {
    # 한국어 일반어
    "내용", "관련", "사용", "경우", "부분", "정도", "생각", "방법", "진행",
    "확인", "작업", "설정", "추가", "수정", "정보", "결과", "상태", "기준",
    "그것", "그거", "여기", "거기", "이거", "저거", "무엇", "어떤", "관해",
    "그냥", "정말", "조금", "많이", "다시", "먼저", "현재", "다음", "이전",
    # 영어 일반어
    "thing", "things", "stuff", "etc", "really", "just", "like", "make",
    "made", "want", "need", "good", "work", "works", "very", "more", "most",
    "some", "such", "then", "than", "also", "here", "there", "what", "which",
    "when", "where", "will", "would", "should", "could", "does", "done",
}

# item 5: 파일 확장자 토큰. 파일명에서 떨어져 나온 노이즈라 노드 후보로 부적절.
_FILE_EXT_TOKENS: Set[str] = {
    "py", "js", "ts", "tsx", "jsx", "json", "md", "txt", "csv", "tsv",
    "png", "jpg", "jpeg", "gif", "svg", "webp", "pdf", "html", "css",
    "yml", "yaml", "toml", "sh", "bash", "zsh", "log", "ipynb", "xml",
    "lock", "cfg", "ini", "env", "bin", "exe", "zip", "tar", "gz",
}

_FILTER_TOKENS: Set[str] = _STOPWORDS | _GENERIC_BLACKLIST | _FILE_EXT_TOKENS

# item 5: 한국어 조사. 토큰 끝에서 제거해 "그래프를"/"그래프가"/"그래프" 를 하나로 모은다.
_JOSA_SUFFIXES: List[str] = sorted(
    [
        "으로는", "에서는", "에서의", "에게서", "이라는", "이라고", "라는", "라고",
        "으로", "에서", "에게", "한테", "까지", "부터", "보다", "처럼", "마다",
        "조차", "밖에", "라도", "이나", "에는", "에도", "께서", "이란",
        "은", "는", "이", "가", "을", "를", "와", "과", "에", "의", "도",
        "만", "로", "나", "께", "란",
    ],
    key=len,
    reverse=True,
)


def _strip_josa(token: str) -> str:
    """한국어 토큰 끝의 조사를 제거한다. (영문/혼합 토큰은 그대로)"""
    if not re.search(r"[가-힣]", token):
        return token
    for suf in _JOSA_SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 2:
            return token[: -len(suf)]
    return token


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    # 한글/영문/숫자만 남김
    cleaned = re.sub(r"[^0-9A-Za-z가-힣\s]", " ", text)
    tokens = [t for t in cleaned.split() if t]
    out = []
    for t in tokens:
        low = _strip_josa(t.lower())
        if len(low) < 2:
            continue
        if low in _FILTER_TOKENS:
            continue
        out.append(low)
    return out


def _ngrams(tokens: Sequence[str], n: int = 2) -> List[str]:
    if len(tokens) < n:
        return []
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


# ── Topic candidates ──────────────────────────────────────────────────────────


@dataclass
class TopicCandidate:
    label: str
    score: float
    sources: List[str] = field(default_factory=list)
    aliases: Set[str] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "score": self.score,
            "sources": list(self.sources),
            "aliases": sorted(self.aliases),
        }


def extract_topic_candidates(
    documents: Iterable[Dict[str, Any]],
    *,
    min_score: float = 1.5,
    top_k: int = 50,
) -> List[TopicCandidate]:
    """대화/파일/작업 로그 documents에서 topic candidate를 뽑는다.

    documents: [{"id": str, "text": str, "kind": "chat|file|task", "weight": float}]
    """
    counts: Dict[str, float] = {}
    sources: Dict[str, List[str]] = {}

    for doc in documents:
        text = str(doc.get("text") or "")
        # secret이 섞여 있으면 제거하고 진행
        text = mask_secrets(text)
        weight = float(doc.get("weight") or 1.0)
        kind = str(doc.get("kind") or "chat")
        if kind == "file":
            weight *= 1.5  # 파일은 신호가 강함
        elif kind == "task":
            weight *= 1.2

        tokens = _tokenize(text)
        if not tokens:
            continue

        # 단어 + 2gram 두 가지 모두 후보로 둔다
        bag = list(set(tokens + _ngrams(tokens, 2)))
        seen_in_doc: Set[str] = set()
        for term in bag:
            if term in seen_in_doc:
                continue
            seen_in_doc.add(term)
            counts[term] = counts.get(term, 0.0) + weight
            sources.setdefault(term, []).append(str(doc.get("id") or ""))

    # log-normalize and filter
    candidates: List[TopicCandidate] = []
    for term, score in counts.items():
        if score < min_score:
            continue
        term_sources = sources.get(term, [])
        # item 5: 같은 대화/폴더(단일 출처)에서만 반복된 단어는 감점한다.
        # 여러 출처에서 반복된 개념일수록 가산해 "진짜 주제"만 위로 올린다.
        distinct_sources = len({s for s in term_sources if s})
        if distinct_sources <= 1:
            diversity = 0.5  # 단일 출처 노이즈 감점
        else:
            diversity = 1.0 + 0.15 * math.log(distinct_sources)
        normalized = math.log(1.0 + score) * (1.0 + 0.05 * len(term.split())) * diversity
        candidates.append(
            TopicCandidate(
                label=term,
                score=round(normalized, 4),
                sources=term_sources[:20],
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_k]


# ── Alias normalization / merging ─────────────────────────────────────────────

DEFAULT_ALIAS_GROUPS: List[List[str]] = [
    ["lattice ai", "latticeai", "래티스 ai", "래티스ai", "내 앱", "내 ai"],
    ["gpt-oss", "gpt oss", "openai gpt-oss"],
    ["gemma 4", "gemma4", "google gemma 4"],
    ["llama 3", "llama3", "meta llama 3"],
]


def build_alias_index(groups: Optional[List[List[str]]] = None) -> Dict[str, str]:
    groups = groups or DEFAULT_ALIAS_GROUPS
    idx: Dict[str, str] = {}
    for grp in groups:
        if not grp:
            continue
        canon = grp[0].lower().strip()
        for alias in grp:
            idx[alias.lower().strip()] = canon
    return idx


def cluster_candidates(
    candidates: List[TopicCandidate],
    alias_index: Optional[Dict[str, str]] = None,
) -> List[TopicCandidate]:
    """비슷한 라벨을 자동 병합한다."""
    alias_index = alias_index or build_alias_index()
    merged: Dict[str, TopicCandidate] = {}

    def canon_of(label: str) -> str:
        low = label.lower().strip()
        if low in alias_index:
            return alias_index[low]
        # 단순 정규화: 공백/하이픈 통일
        norm = re.sub(r"[-_]+", " ", low)
        norm = re.sub(r"\s+", " ", norm).strip()
        return norm

    for c in candidates:
        key = canon_of(c.label)
        if key in merged:
            existing = merged[key]
            existing.score += c.score * 0.6  # 중복일수록 score는 약간 가산
            existing.aliases.add(c.label)
            existing.sources = list({*existing.sources, *c.sources})[:50]
        else:
            cand = TopicCandidate(
                label=key,
                score=c.score,
                sources=list(c.sources),
                aliases={c.label} if c.label.lower() != key else set(),
            )
            merged[key] = cand

    return sorted(merged.values(), key=lambda x: x.score, reverse=True)


# ── Promotion rules ───────────────────────────────────────────────────────────


@dataclass
class PromotionDecision:
    candidate: TopicCandidate
    promote: bool
    reason: str
    importance: float


def should_promote(
    candidate: TopicCandidate,
    *,
    existing_node_labels: Optional[Set[str]] = None,
    min_sources: int = 2,
    min_importance: float = 1.0,
) -> PromotionDecision:
    existing_node_labels = existing_node_labels or set()
    # 1. secret 라벨이면 절대 승격 금지
    if contains_secret(candidate.label):
        return PromotionDecision(candidate, False, "contains secret", 0.0)
    # 2. 이미 같은 라벨의 노드가 있으면 승격하지 않음 (alias로 들어감)
    if candidate.label in existing_node_labels:
        return PromotionDecision(candidate, False, "duplicate of existing node", candidate.score)
    # 3. 출처가 너무 적으면 노이즈로 간주
    if len(set(candidate.sources)) < min_sources:
        return PromotionDecision(candidate, False, "too few sources", candidate.score)
    # 4. 너무 짧은 라벨(단어 1자) 거부
    if len(candidate.label) < 2:
        return PromotionDecision(candidate, False, "label too short", candidate.score)

    importance = candidate.score
    if importance < min_importance:
        return PromotionDecision(candidate, False, "importance below threshold", importance)

    return PromotionDecision(candidate, True, "promoted", importance)


# ── Thread edges (파생 이야기) ────────────────────────────────────────────────


@dataclass
class ThreadEdge:
    source: str
    target: str
    story: str
    evidence: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "story": self.story,
            "evidence": list(self.evidence),
            "created_at": self.created_at,
        }


def derive_thread_story(
    source_label: str,
    target_label: str,
    *,
    snippets: Iterable[str],
    max_len: int = 220,
) -> str:
    """간단한 1~2문장 파생 이야기를 만든다. 빠르고 결정적."""
    cleaned: List[str] = []
    for s in snippets:
        if not s:
            continue
        sm = mask_secrets(str(s))
        # 가장 의미있어 보이는 첫 문장만 따온다
        sentences = re.split(r"[.!?\n]+", sm)
        for sent in sentences:
            t = sent.strip()
            if 8 <= len(t) <= max_len:
                cleaned.append(t)
                break
        if len(cleaned) >= 2:
            break
    if not cleaned:
        return f"{source_label}에서 {target_label}로 이어지는 흐름이 발견되었습니다."
    joined = ". ".join(cleaned[:2])
    return joined[:max_len]


# ── Curation (중요도 기반 hide/show) ──────────────────────────────────────────


def curate_nodes(
    nodes: List[Dict[str, Any]],
    *,
    max_visible: int = 20,
    behavior_signals: Optional[Dict[str, Dict[str, float]]] = None,
    decay_seconds: float = 60 * 60 * 24 * 14,  # 2주
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """노드 리스트에 visible/score 정보를 부여한다.

    nodes: [{"id": str, "label": str, "importance": float, "updated_at": float}]
    behavior_signals: {node_id: {"clicks": int, "searches": int}} 형태.
    """
    now = now or time.time()
    behavior_signals = behavior_signals or {}
    enriched: List[Dict[str, Any]] = []

    for n in nodes:
        importance = float(n.get("importance") or 0.0)
        updated_at = float(n.get("updated_at") or now)
        age = max(0.0, now - updated_at)
        decay = math.exp(-age / decay_seconds) if decay_seconds > 0 else 1.0
        sig = behavior_signals.get(str(n.get("id") or ""), {})
        boost = (
            0.4 * math.log(1.0 + float(sig.get("clicks") or 0))
            + 0.6 * math.log(1.0 + float(sig.get("searches") or 0))
        )
        final_score = round(importance * decay + boost, 4)
        enriched.append({**n, "curated_score": final_score})

    enriched.sort(key=lambda x: x.get("curated_score", 0.0), reverse=True)
    for i, n in enumerate(enriched):
        n["visible"] = i < max_visible
    return enriched


# ── End-to-end helper ─────────────────────────────────────────────────────────


def auto_build_graph_overlay(
    documents: List[Dict[str, Any]],
    *,
    existing_node_labels: Optional[Set[str]] = None,
    alias_index: Optional[Dict[str, str]] = None,
    max_new_nodes: int = 8,
) -> Dict[str, Any]:
    """한 번에 토픽 추출 → 클러스터 → 승격 결정까지 수행한 결과를 돌려준다.

    실제 그래프 DB에 쓰는 작업은 호출자가 담당한다. 이 함수는 부작용 없음.
    """
    candidates = extract_topic_candidates(documents)
    clustered = cluster_candidates(candidates, alias_index=alias_index)

    promotions: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    promoted_count = 0
    for cand in clustered:
        if promoted_count >= max_new_nodes:
            skipped.append({"label": cand.label, "reason": "max_new_nodes reached"})
            continue
        decision = should_promote(cand, existing_node_labels=existing_node_labels)
        if decision.promote:
            promotions.append({
                "label": cand.label,
                "importance": decision.importance,
                "aliases": sorted(cand.aliases),
                "sources": cand.sources,
            })
            promoted_count += 1
        else:
            skipped.append({"label": cand.label, "reason": decision.reason})

    return {
        "promotions": promotions,
        "skipped": skipped,
        "candidates_total": len(candidates),
        "clustered_total": len(clustered),
    }


__all__ = [
    "TopicCandidate",
    "PromotionDecision",
    "ThreadEdge",
    "contains_secret",
    "mask_secrets",
    "extract_topic_candidates",
    "cluster_candidates",
    "should_promote",
    "derive_thread_story",
    "curate_nodes",
    "auto_build_graph_overlay",
    "build_alias_index",
    "DEFAULT_ALIAS_GROUPS",
]
