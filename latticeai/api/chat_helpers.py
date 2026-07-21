"""Pure chat helpers: language/intent detection, file-action parsing,
network-status formatting, workspace-scope extraction, and recent-context
assembly. Split out of the chat router module so create_chat_router keeps
only request wiring. chat.py re-imports every name, so external import
sites (tests, app_factory) are unaffected.
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator, Dict, List, Optional

from fastapi import Request


def pair_user_history(history: List[Dict], user_email: str) -> List[Dict]:
    """Restrict history to one user's exchange.

    Keeps the user's own messages plus assistant replies that directly follow
    them. A bare role=="assistant" pass would leak every other user's replies
    into this user's prompt context.
    """
    paired: List[Dict] = []
    include_next_assistant = False
    for item in history:
        if item.get("role") == "assistant":
            if include_next_assistant:
                assistant_user = item.get("user_email")
                if assistant_user and assistant_user != user_email:
                    # Concurrent requests can interleave rows in the global
                    # history order.  A reply explicitly owned by somebody
                    # else is never this user's reply; keep waiting for the
                    # next correctly-owned (or legacy ownerless) assistant.
                    continue
                paired.append(item)
                include_next_assistant = False
        elif item.get("user_email") == user_email:
            paired.append(item)
            include_next_assistant = True
        else:
            include_next_assistant = False
    return paired


def detect_language(text: str) -> str:
    """Detect language: 'ko' (Korean) or 'en' (English)."""
    total = max(len(text), 1)
    ko = sum(1 for c in text if '가' <= c <= '힣')
    if ko / total > 0.05:
        return "ko"
    return "en"

_LANG_HINT = {
    "ko": "Respond in Korean (한국어로 답변하세요).",
    "en": "Respond in English.",
}

def is_network_status_request(text: str) -> bool:
    """사용자가 현재 IP/네트워크 정보를 물었는지 감지합니다."""
    t = (text or "").lower()
    explicit_network = any(
        phrase in t
        for phrase in (
            "ipconfig",
            "ifconfig",
            "network status",
            "네트워크 상태",
            "네트워크 확인",
            "현재 네트워크",
        )
    )
    current_ip = bool(
        re.search(
            r"(내|현재|지금|로컬|local|public|공인|외부|내부)\s*(ip|아이피)\s*(주소)?",
            t,
        )
        or re.search(
            r"(ip|아이피)\s*(주소)?\s*(확인|상태|알려줘|보여줘)",
            t,
        )
    )
    return explicit_network or current_ip

def is_current_url_request(text: str) -> bool:
    t = (text or "").lower()
    explicit_url = any(phrase in t for phrase in ("현재 url", "current url", "page url", "페이지 url"))
    current_page_link = bool(
        re.search(r"(현재|지금|여기|이\s*페이지|브라우저|접속)\s*(페이지\s*)?(url|링크|주소)", t)
        or re.search(r"(url|링크)\s*(알려줘|보여줘|확인)", t)
    )
    return explicit_url or current_page_link

def is_clear_command(text: str) -> bool:
    return (text or "").strip().lower() in {"/clear", "/clear_all"}

# Path segments intentionally exclude spaces: allowing spaces let the target
# match swallow preceding words (e.g. "create a text file report.txt" resolved
# to the whole phrase as the path). Chat file targets are single tokens.
_FILE_TARGET_RE = re.compile(
    r"(?<![\w.-])(?:[~./\\]?[\w.@()+-]+[\\/])*[\w.@()+-]+"
    r"\.(?:py|js|jsx|ts|tsx|md|markdown|txt|json|yaml|yml|toml|html|css|csv|xml|pdf|docx|xlsx|pptx|sh|sql)",
    re.IGNORECASE,
)


def is_file_action_request(text: str) -> bool:
    """Return True for chat requests that should execute file tools, not prose.

    The Brain chat composer posts to ``/chat``. Without this gate, explicit
    side-effect requests such as "create hello.md" are handled as plain model
    generation, which commonly produces a code block instead of a real file.
    Keep the gate narrow so normal Q&A and "how do I create a file?" stay in
    ordinary chat.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    lower = raw.lower()

    if any(phrase in lower for phrase in (
        "how to ", "how do i ", "방법", "어떻게", "예시", "sample", "example",
    )) and not any(phrase in lower for phrase in (
        "actually create", "real file", "실제로", "파일로", "저장해", "만들어",
    )):
        return False

    has_target = bool(_FILE_TARGET_RE.search(raw))
    has_file_word = any(word in lower for word in (
        "file", "파일", "문서", "artifact", "아티팩트", "save as", "저장",
        # Explicit artifact types: users asking for "an html page" expect a
        # real file, not a code block in chat — route them to the file path
        # where the model-agnostic generation pipeline guarantees validity.
        "html", "웹페이지", "웹 페이지", "홈페이지", "webpage", "web page",
    ))
    has_action = any(word in lower for word in (
        "create", "make", "write", "save", "generate", "edit", "update",
        "만들", "생성", "작성", "저장", "수정", "써줘", "만들어줘",
    ))

    if not has_action:
        return False
    if has_target and has_action:
        return True
    return has_file_word and has_action


def file_action_target(text: str) -> Optional[str]:
    """Extract the first explicit workspace file target from a request."""
    match = _FILE_TARGET_RE.search(text or "")
    if not match:
        return None
    return match.group(0).strip().strip("`'\".,:;)]}")


def inline_file_action_content(text: str) -> Optional[str]:
    """Extract short user-provided content for deterministic file writes."""
    raw = (text or "").strip()
    # Each pattern requires an explicit binder so ambiguous words like "text"
    # ("create a text file report.txt") or a bare "with" ("report.md with a
    # summary of X") do not get captured as literal file content.
    patterns = [
        r"(?:내용|본문)\s*(?:은|는|이에요|입니다)\s*(.+)$",
        r"(?:내용|본문|content|body|text)\s*[:=]\s*(.+)$",
        r"(?:content|body)\s+(?:is|as)\s+(.+)$",
        r"(?:with the content|with content|containing)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if match:
            content = match.group(1).strip()
            return content.strip("`'\"")
    return None


def strip_generated_file_content(text: str) -> str:
    """Remove common chat wrappers when a model is asked for file content only."""
    content = (text or "").strip()
    fenced = re.search(r"```(?:[\w.+-]+)?\s*(.*?)\s*```", content, flags=re.DOTALL)
    if fenced:
        content = fenced.group(1).strip()
    return content


def format_network_status(info: Dict) -> str:
    lines = [
        f"내부 IP: {info.get('local_ip') or '확인 안 됨'}",
        f"외부 IP: {info.get('public_ip') or '확인 안 됨'}",
        f"호스트명: {info.get('hostname') or '확인 안 됨'}",
    ]
    local_ips = info.get("local_ips") or {}
    if local_ips:
        lines.extend(["", "인터페이스:"])
        lines.extend(f"- {name}: {ip}" for name, ip in local_ips.items())
    note = info.get("note")
    if note:
        lines.extend(["", note])
    return "\n".join(lines)

def build_context_quality(
    query: str,
    *,
    knowledge_graph=None,
    allowed_workspaces=None,
    limit: int = 6,
) -> Dict[str, object]:
    """Honest RAG context-quality signal for chat responses (v9.8.0, additive).

    Returns ``{"mode": "hybrid"|"lexical_only"|"none", "nodes": int,
    "limited": bool, "reason": str|None}``. ``limited`` is true when the
    graph produced 0–1 matches, when vector retrieval fell back to
    lexical-only, or when retrieval failed entirely — so the frontend can
    tell the user the answer is weakly grounded instead of implying rich
    graph context. Never raises.
    """
    from lattice_brain.graph.retrieval import context_quality_signal

    query = str(query or "").strip()
    if knowledge_graph is None or not query:
        return context_quality_signal(
            "none", 0, reason="지식 그래프 컨텍스트를 사용하지 않았습니다"
        )
    scope_kwargs = (
        {"allowed_workspaces": allowed_workspaces}
        if allowed_workspaces is not None
        else {}
    )
    hybrid_fn = getattr(knowledge_graph, "hybrid_search", None)
    if callable(hybrid_fn):
        try:
            result = hybrid_fn(query, top_k=limit, **scope_kwargs) or {}
        except Exception:  # noqa: BLE001 — signal building must never fail chat
            return context_quality_signal("none", 0, reason="그래프 검색에 실패했습니다")
        matches = result.get("matches") or []
        return context_quality_signal(
            str(result.get("mode") or "hybrid"), len(matches)
        )
    # Lexical-only stores without the hybrid mixin.
    try:
        matches = (
            knowledge_graph.search(query, limit=limit, **scope_kwargs) or {}
        ).get("matches") or []
    except Exception:  # noqa: BLE001 — signal building must never fail chat
        return context_quality_signal("none", 0, reason="그래프 검색에 실패했습니다")
    return context_quality_signal("lexical_only", len(matches))


# ── answer-citation binding (backlog #11, review §7.2 E #4) ──────────────────
_GROUNDING_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")
# High-frequency tokens that overlap by accident, not by grounding.
_GROUNDING_STOP_TOKENS = frozenset({
    "그리고", "그러나", "하지만", "그래서", "있습니다", "입니다", "합니다",
    "있는", "없는", "위해", "통해", "대한", "관련", "경우", "내용", "answer",
    "the", "and", "for", "with", "this", "that", "from", "have", "are",
    "was", "were", "can", "will", "not", "you", "your",
})
GROUNDING_MIN_OVERLAP_TOKENS = 2
GROUNDING_MIN_OVERLAP_RATIO = 0.08


def _grounding_tokens(text: str) -> set:
    return {
        token.lower()
        for token in _GROUNDING_TOKEN_RE.findall(str(text or ""))
        if token.lower() not in _GROUNDING_STOP_TOKENS
    }


def assess_answer_grounding(
    answer: str,
    *,
    trace: Optional[Dict[str, object]] = None,
    context_quality: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Bind an answer to its retrieved sources — honestly (never blocks).

    Heuristic: for each retrieved source (the answer trace's ``graph_nodes``
    / ``source_files``), measure content-token overlap between the answer and
    the source's title+summary, and accept explicit citations (the answer
    names the source title or filename). Returns::

        {"status": "supported",   "label": "근거 있음", "source_ids": [...],
         "cited": [...], "overlap": float}
        {"status": "unsupported", "label": "근거 없음", "source_ids": [], ...}
        {"status": "no_context",  "label": "근거 없음", ...}  # nothing retrieved

    ``no_context`` (still labeled 근거 없음) lets the UI distinguish "the
    Brain had nothing" from "the Brain had sources but the answer ignored
    them". Annotation only — the answer is never modified or blocked.
    """
    answer_text = str(answer or "").strip()
    trace = trace if isinstance(trace, dict) else {}
    sources: List[Dict[str, object]] = []
    seen_ids = set()
    for node in list(trace.get("graph_nodes") or []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or node.get("node_id") or "")
        if not node_id or node_id in seen_ids:
            continue
        seen_ids.add(node_id)
        meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        sources.append({
            "id": node_id,
            "title": str(node.get("title") or ""),
            "body": " ".join(
                str(part or "")
                for part in (node.get("title"), node.get("summary"), meta.get("filename"))
            ),
        })
    for src in list(trace.get("source_files") or []):
        if not isinstance(src, dict):
            continue
        node_id = str(src.get("node_id") or src.get("source") or "")
        if not node_id or node_id in seen_ids:
            continue
        seen_ids.add(node_id)
        sources.append({
            "id": node_id,
            "title": str(src.get("node_title") or ""),
            "body": " ".join(
                str(part or "") for part in (src.get("node_title"), src.get("source"))
            ),
        })

    mode = str((context_quality or {}).get("mode") or "none")
    if not sources:
        return {
            "status": "no_context",
            "label": "근거 없음",
            "source_ids": [],
            "cited": [],
            "overlap": 0.0,
            "reason": (
                "검색된 출처가 없습니다"
                if mode == "none"
                else "출처 후보를 답변에 연결하지 못했습니다"
            ),
        }
    if not answer_text:
        return {
            "status": "unsupported",
            "label": "근거 없음",
            "source_ids": [],
            "cited": [],
            "overlap": 0.0,
            "reason": "답변이 비어 있습니다",
        }

    answer_tokens = _grounding_tokens(answer_text)
    answer_lower = answer_text.lower()
    cited: List[Dict[str, object]] = []
    best_overlap = 0.0
    for source in sources:
        source_tokens = _grounding_tokens(source["body"])
        if not source_tokens:
            continue
        shared = answer_tokens & source_tokens
        denominator = max(1, min(len(source_tokens), 60))
        ratio = len(shared) / denominator
        best_overlap = max(best_overlap, ratio)
        title = str(source.get("title") or "").strip().lower()
        explicit = bool(title) and len(title) >= 4 and title in answer_lower
        # Explicit-citation OR token-overlap binding. Comparisons are explicit
        # (a 0.0 score is falsy but a valid value — never use `or` defaults).
        if explicit or (
            len(shared) >= GROUNDING_MIN_OVERLAP_TOKENS
            and ratio >= GROUNDING_MIN_OVERLAP_RATIO
        ):
            cited.append({
                "id": source["id"],
                "title": source.get("title"),
                "overlap": round(ratio, 4),
                "explicit": explicit,
            })
    if cited:
        return {
            "status": "supported",
            "label": "근거 있음",
            "source_ids": [item["id"] for item in cited],
            "cited": cited,
            "overlap": round(best_overlap, 4),
            "reason": None,
        }
    return {
        "status": "unsupported",
        "label": "근거 없음",
        "source_ids": [],
        "cited": [],
        "overlap": round(best_overlap, 4),
        "reason": "답변이 검색된 출처의 내용을 사용하지 않았습니다",
    }


def workspace_scope_from_request(request: Request) -> Optional[str]:
    header = request.headers.get("X-Workspace-Id")
    if header and header.strip():
        return header.strip()
    query = request.query_params.get("workspace_id")
    return query.strip() if query and query.strip() else None

async def single_text_stream(text: str, model: str = "system") -> AsyncIterator[str]:
    yield f"data: {json.dumps({'chunk': text, 'model': model}, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def build_recent_chat_context(
    *,
    get_history,
    limit: int = 10,
    include_image_missing_replies: bool = True,
    user_email: Optional[str] = None,
    conversation_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> str:
    if user_email:
        # Apply identity/workspace isolation in the durable query rather than
        # relying only on positional post-filtering.  In particular, never
        # admit legacy-global rows into an authenticated model prompt.
        history = get_history(
            user_email=user_email,
            allowed_workspaces={workspace_id} if workspace_id is not None else None,
            include_legacy_global=False,
        )
    else:
        history = get_history()
    if workspace_id is not None:
        history = [
            item for item in history
            if str(item.get("workspace_id") or "personal") == str(workspace_id)
        ]
    if conversation_id:
        history = [item for item in history if item.get("conversation_id") == conversation_id]
    if user_email:
        history = pair_user_history(history, user_email)
    history = history[-limit:]
    lines = []
    for item in history:
        role = item.get("role", "user")
        content = item.get("content", "")
        if not include_image_missing_replies and role == "assistant":
            if "이미지" in content and any(word in content for word in ["업로드", "제공", "올려"]):
                continue
        source = item.get("source")
        label = role
        if source:
            label = f"{role} ({source})"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)
