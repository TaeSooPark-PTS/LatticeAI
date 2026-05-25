"""
SQLite knowledge graph for Lattice AI workspace memory.

The graph keeps raw event JSON, normalized node metadata, and edges in one
portable database so it can later migrate to Neo4j/Postgres without changing
the ingestion contract.
"""

import hashlib
import json
import logging
import math
import os
import platform
import re
import shutil
import sqlite3
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from kg_schema import KGStoreV2
except Exception:  # pragma: no cover - v2 schema is optional at import time
    KGStoreV2 = None  # type: ignore[assignment]


GRAPH_SCHEMA_VERSION = 1

LOCAL_TEXT_EXTENSIONS = {".txt", ".md"}
LOCAL_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json",
    ".yaml", ".yml", ".xml", ".sql", ".sh", ".zsh", ".toml", ".ini",
}
LOCAL_DOCUMENT_EXTENSIONS = {".pdf", ".docx"}
LOCAL_SPREADSHEET_EXTENSIONS = {".xlsx", ".csv"}
LOCAL_SLIDE_EXTENSIONS = {".pptx"}
LOCAL_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
LOCAL_SUPPORTED_EXTENSIONS = (
    LOCAL_TEXT_EXTENSIONS
    | LOCAL_CODE_EXTENSIONS
    | LOCAL_DOCUMENT_EXTENSIONS
    | LOCAL_SPREADSHEET_EXTENSIONS
    | LOCAL_SLIDE_EXTENSIONS
    | LOCAL_IMAGE_EXTENSIONS
)

LOCAL_SIZE_LIMITS = {
    "text": 4_000_000,
    "code": 4_000_000,
    "pdf": 50_000_000,
    "document": 50_000_000,
    "spreadsheet": 50_000_000,
    "slide_deck": 50_000_000,
    "image": 100_000_000,
}

COMMON_EXCLUDED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", ".nuxt",
    ".turbo", "dist", "build", "target", "out", "coverage", ".cache",
    ".config", ".ssh", ".gnupg", ".docker", ".kube", ".aws", ".azure",
    ".npm", ".pnpm-store", ".yarn", ".bun", ".cargo", ".rustup", ".pyenv",
    ".conda", ".local", ".claude", ".codex", ".cursor", ".copilot",
    ".antigravity", ".antigravity-ide",
}

COMMON_EXCLUDED_FILE_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_ed25519", "authorized_keys", "known_hosts",
    "credentials.json", "service-account.json", "token.json", "secrets.json",
    "cookies", "login data", "history", "web data", ".ds_store", "thumbs.db",
}
COMMON_EXCLUDED_FILE_SUFFIXES = {
    ".pem", ".key", ".p12", ".pfx", ".kdbx", ".wallet", ".sqlite", ".db",
    ".exe", ".dll", ".sys", ".msi", ".dmg", ".pkg", ".app", ".zip", ".tar",
    ".gz", ".7z", ".rar", ".mp4", ".mov", ".mp3", ".wav", ".tmp", ".bak",
    ".lock",
}
SENSITIVE_PATH_KEYWORDS = {
    "secret", "secrets", "token", "password", "passwd", "credential",
    "credentials", "private", "key", "wallet", "recovery", "seed",
    "mnemonic", "cookie", "session", "auth", "oauth", "certificate",
    "cert", "api_key", "apikey",
}

MACOS_EXCLUDED_PREFIXES = (
    "/System", "/Library", "/Applications", "/private", "/tmp", "/var",
)
WINDOWS_EXCLUDED_NAMES = {
    "windows", "program files", "program files (x86)", "programdata", "appdata",
    "$recycle.bin", "system volume information", "recovery", "perflogs",
    "intel", "amd", "nvidia",
}
LINUX_EXCLUDED_PREFIXES = (
    "/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/root",
    "/run", "/sbin", "/sys", "/tmp", "/usr", "/var", "/snap", "/lost+found",
)


def _now() -> str:
    return datetime.now().isoformat()


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _recency_score(updated_at: Optional[str], *, now: Optional[datetime] = None, half_life_days: float = 14.0) -> float:
    stamp = _parse_iso(updated_at)
    if not stamp:
        return 0.0
    now = now or datetime.now()
    age_days = max(0.0, (now - stamp).total_seconds() / 86400.0)
    decay = math.log(2) / max(0.1, half_life_days)
    return math.exp(-decay * age_days)


def _json(data: Optional[Dict[str, Any]]) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def _safe_loads(raw: Optional[str]) -> Dict[str, Any]:
    """Tolerantly parse a metadata_json column — returns {} on corrupt rows."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError) as e:
        logging.warning("knowledge_graph: corrupt metadata_json (%s) — using empty dict", e)
        return {}


def _slug(text: str, max_len: int = 96) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    value = re.sub(r"[^0-9a-zA-Z가-힣._:@/-]+", "-", value).strip("-")
    return (value or "untitled")[:max_len]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _safe_iso_from_stat_mtime(mtime: float) -> str:
    try:
        return datetime.fromtimestamp(float(mtime)).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _path_fingerprint(path: Path) -> str:
    return _sha256_text(str(path.expanduser().resolve()))[:24]


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _path_parts_lower(path: Path) -> List[str]:
    return [part.lower() for part in path.parts if part and part not in {os.sep, path.anchor}]


def _current_os_type() -> str:
    system = platform.system().lower()
    if system.startswith("darwin"):
        return "macos"
    if system.startswith("windows"):
        return "windows"
    if system.startswith("linux"):
        return "linux"
    return system or "unknown"


def _drive_id_for_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    if resolved.drive:
        return resolved.drive.upper()
    parts = resolved.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return f"/Volumes/{parts[2]}"
    if len(parts) >= 3 and parts[1] == "media":
        return f"/media/{parts[2]}"
    if len(parts) >= 3 and parts[1] == "mnt":
        return f"/mnt/{parts[2]}"
    return resolved.anchor or "/"


def _file_category(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in LOCAL_CODE_EXTENSIONS:
        return "code"
    if ext in LOCAL_TEXT_EXTENSIONS:
        return "text"
    if ext == ".pdf":
        return "pdf"
    if ext in LOCAL_DOCUMENT_EXTENSIONS:
        return "document"
    if ext in LOCAL_SPREADSHEET_EXTENSIONS:
        return "spreadsheet"
    if ext in LOCAL_SLIDE_EXTENSIONS:
        return "slide_deck"
    if ext in LOCAL_IMAGE_EXTENSIONS:
        return "image"
    return "unsupported"


def _node_type_for_category(category: str) -> str:
    return {
        "code": "CodeFile",
        "spreadsheet": "Spreadsheet",
        "slide_deck": "SlideDeck",
        "image": "Image",
        "unsupported": "File",
    }.get(category, "Document")


def _parser_type_for_category(category: str, ext: str) -> str:
    if category in {"text", "code"}:
        return "plain_text"
    if category == "spreadsheet" and ext == ".csv":
        return "csv_text"
    if category == "image":
        return "image_ocr"
    return ext.lstrip(".") or category


def _size_limit_for_category(category: str) -> int:
    return LOCAL_SIZE_LIMITS.get(category, LOCAL_SIZE_LIMITS["document"])


def _is_hidden_path(path: Path, root: Optional[Path] = None) -> bool:
    parts: Iterable[str]
    if root is not None:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            parts = path.parts
    else:
        parts = path.parts
    return any(part.startswith(".") and part not in {".", ".."} for part in parts)


def _excluded_directory_reason(path: Path, *, root: Optional[Path] = None, os_type: Optional[str] = None) -> Optional[str]:
    os_type = os_type or _current_os_type()
    name = path.name.lower()
    if name in COMMON_EXCLUDED_DIRS:
        return "excluded_folder"
    if _is_hidden_path(path, root):
        return "hidden_folder"
    parts = _path_parts_lower(path)
    if os_type == "windows" and any(part in WINDOWS_EXCLUDED_NAMES for part in parts):
        return "system_folder"
    normalized = path.as_posix()
    root_normalized = root.as_posix() if root else ""

    def _prefix_blocks(prefixes: Tuple[str, ...]) -> bool:
        for prefix in prefixes:
            path_under_prefix = normalized == prefix or normalized.startswith(f"{prefix}/")
            root_under_prefix = bool(root_normalized) and (
                root_normalized == prefix or root_normalized.startswith(f"{prefix}/")
            )
            if path_under_prefix and not root_under_prefix:
                return True
        return False

    if os_type == "macos":
        home_library = Path.home() / "Library"
        try:
            root_is_library = bool(root) and _is_relative_to(root.expanduser().resolve(), home_library.expanduser().resolve())
            if _is_relative_to(path.expanduser().resolve(), home_library.expanduser().resolve()) and not root_is_library:
                return "user_library"
        except OSError:
            pass
        if _prefix_blocks(MACOS_EXCLUDED_PREFIXES):
            return "system_folder"
    if os_type == "linux":
        if _prefix_blocks(LINUX_EXCLUDED_PREFIXES):
            return "system_folder"
    return None


def _sensitive_file_reason(path: Path, *, root: Optional[Path] = None) -> Optional[str]:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in COMMON_EXCLUDED_FILE_NAMES or suffix in COMMON_EXCLUDED_FILE_SUFFIXES:
        return "sensitive_or_excluded_file"
    try:
        rel_text = path.relative_to(root).as_posix().lower() if root else path.as_posix().lower()
    except ValueError:
        rel_text = path.as_posix().lower()
    tokens = re.split(r"[^0-9a-zA-Z_가-힣]+", rel_text)
    if any(token in SENSITIVE_PATH_KEYWORDS for token in tokens):
        return "sensitive_name"
    return None


def _root_warning(path: Path, os_type: str) -> Optional[str]:
    resolved = path.expanduser().resolve()
    home = Path.home().expanduser().resolve()
    if os_type == "macos" and resolved == home:
        return "홈 전체에는 설정/숨김 폴더가 포함될 수 있습니다. 문서, 데스크탑, 다운로드, 프로젝트 폴더부터 추가하는 것을 권장합니다."
    if os_type == "linux" and resolved.as_posix() == "/":
        return "루트 디렉터리에는 시스템 파일이 포함되어 있습니다. 일반 사용자 폴더나 마운트된 데이터 폴더를 권장합니다."
    if os_type == "windows" and str(resolved).rstrip("\\/").upper() in {"C:", "C:\\"}:
        return "C드라이브에는 Windows 시스템 파일과 앱 설정 파일이 포함되어 있습니다. 하위 폴더를 선택하는 것을 권장합니다."
    return None


def _sample_file(path: Path, root: Path, status: str, reason: str = "") -> Dict[str, Any]:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    try:
        stat = path.stat()
        size = stat.st_size if path.is_file() else None
        modified_at = _safe_iso_from_stat_mtime(stat.st_mtime)
    except OSError:
        size = None
        modified_at = ""
    return {
        "path": str(path),
        "relative_path": rel,
        "name": path.name,
        "extension": path.suffix.lower(),
        "status": status,
        "reason": reason,
        "size_bytes": size,
        "modified_at": modified_at,
    }


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _chunks(text: str, size: int = 1200, overlap: int = 160) -> List[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + size)
        chunks.append(cleaned[start:end])
        if end >= len(cleaned):
            break
        start = max(0, end - overlap)
    return chunks


_CONCEPT_STOP: set = {
    # English stop words
    "the", "and", "for", "with", "this", "that", "from", "into", "which",
    "are", "was", "were", "has", "have", "had", "can", "will", "would",
    "could", "should", "may", "might", "must", "shall", "being", "been",
    "also", "just", "then", "than", "when", "where", "what", "how", "why",
    "its", "their", "your", "our", "you", "they", "them", "these", "those",
    "use", "used", "using", "based", "like", "such", "via", "per", "let",
    "yes", "not", "but", "are", "all", "any", "out", "new", "get", "set",
    # Korean stop words
    "사용자", "내용", "파일", "채팅", "답변", "입니다", "그리고", "처럼",
    "있어", "없어", "이야", "이다", "한다", "하다", "되다", "됩니다",
    "경우", "방법", "부분", "상태", "정도", "결과", "이후", "이전",
    "그것", "이것", "저것", "여기", "거기", "저기", "우리", "저희",
    "기능", "서버", "모델", "설정", "설명", "버전", "지원", "사용", "실행",
    "todo", "fixme", "note", "참고", "주의", "warning",
}


def _extract_concepts(text: str, limit: int = 12) -> List[str]:
    """Extract meaningful named concepts from text.

    Priority order:
    1. Backtick / quoted terms (explicitly technical)
    2. Multi-word proper nouns (Lattice AI, GPT-4o, Claude Sonnet)
    3. Single capitalized proper nouns not at sentence start (Claude, Python, FastAPI)
    4. Korean compound technical terms (멀티모달, 에이전트, 그래프RAG)
    5. Hyphenated / versioned identifiers (gpt-4o, mlx-lm, llama-3.3)
    """
    text = str(text or "")
    seen: dict = {}  # concept_lower → original form

    def _add(term: str) -> None:
        key = term.strip().lower()
        if (
            key
            and key not in _CONCEPT_STOP
            and not key.isdigit()
            and len(key) >= 2
        ):
            seen.setdefault(key, term.strip())

    # 1. Backtick-quoted code/term (highest confidence)
    for m in re.findall(r'`([^`]{2,40})`', text):
        if not re.search(r'[\(\)\[\]{}]', m):  # skip code expressions
            _add(m)

    # 2. Double/single quoted terms
    for m in re.findall(r'"([^"]{2,40})"', text):
        _add(m)

    # 3. Multi-word English proper nouns (Title Case or ALL-CAPS first word, 2–4 words).
    #    Pattern A: Mixed-case first word — "Lattice AI", "Tool Use", "Graph RAG"
    for m in re.findall(
        r'([A-Z][a-z]{1,20}(?:\s+(?:[A-Z]{2,10}|[A-Z][a-z0-9]{1,20}|\d[\w.]{0,6})){1,3})',
        text,
    ):
        _add(m)
    #    Pattern B: ALL-CAPS first word — "VS Code", "MCP Server", "GPT-4o Mini"
    for m in re.findall(
        r'([A-Z]{2,6}(?:\s+(?:[A-Z]{2,10}|[A-Z][a-z0-9]{1,20})){1,2})',
        text,
    ):
        _add(m)

    # 4. Single capitalized proper noun.
    #    Use ASCII-boundary lookaround instead of \b so Korean particles
    #    (와, 의, 는 …) after an English word don't block the match.
    all_caps_words = re.findall(r'(?<![A-Za-z0-9])([A-Z][A-Za-z0-9]{2,24})(?![A-Za-z0-9])', text)
    freq: Dict[str, int] = {}
    for w in all_caps_words:
        freq[w] = freq.get(w, 0) + 1
    sentence_starts = set(re.findall(r'(?:^|(?<=[.!?])\s+)([A-Z][a-z]+)', text))
    for m, cnt in freq.items():
        if m.lower() in _CONCEPT_STOP:
            continue
        if cnt >= 2 or m not in sentence_starts:
            _add(m)

    # 5. Korean technical compound nouns (3–12 chars, no common particles)
    for m in re.findall(r'[가-힣]{2,12}(?:AI|LLM|API|UI|RAG|bot|Bot|기능|모델|서버|에이전트|파이프라인|워크플로)', text):
        _add(m)
    # Korean standalone terms that appear after topic markers (은/는/이/가 앞)
    for m in re.findall(r'([가-힣]{2,12})(?:은|는|이|가|을|를|의|에서|으로|와|과)', text):
        if m.lower() not in _CONCEPT_STOP and len(m) >= 2:
            # Only add if it's non-trivial (has 3+ chars or appears multiple times)
            cnt = text.count(m)
            if len(m) >= 3 or cnt >= 2:
                _add(m)

    # 6. Hyphenated / versioned identifiers (gpt-4o, llama-3.3, mlx-lm)
    for m in re.findall(r'\b([a-zA-Z][a-zA-Z0-9]*(?:-[a-zA-Z0-9.]+)+)\b', text):
        if len(m) >= 4:
            _add(m)

    # De-duplicate: remove shorter if ALL its occurrences in the source text
    # are followed immediately by the suffix that forms the longer concept.
    # "Lattice" → dropped when every occurrence is "Lattice AI"
    # "Claude"  → kept  because it appears as just "Claude" too.
    values = list(seen.values())
    values_lower = [v.lower() for v in values]
    keep = set(range(len(values)))
    for i, v in enumerate(values):
        vl = v.lower()
        for j, wl in enumerate(values_lower):
            if i == j or j not in keep:
                continue
            # Check if vl is a word-prefix of wl
            suffix = wl[len(vl):]
            if not (wl.startswith(vl) and re.match(r'^[\s\-]', suffix)):
                continue
            # Count occurrences of v NOT followed by the suffix
            suffix_stripped = suffix.lstrip(" -")
            # Escape for regex
            pattern_with_suffix = re.escape(v) + r'[\s\-]+' + re.escape(suffix_stripped)
            pattern_alone = re.escape(v) + r'(?![\s\-]*' + re.escape(suffix_stripped) + r')'
            alone_count = len(re.findall(pattern_alone, text, re.IGNORECASE))
            if alone_count == 0:
                # Shorter term never appears alone → safe to remove
                keep.discard(i)
                break

    final = [values[i] for i in range(len(values)) if i in keep]
    return final[:limit]


# ──────────────────────────────────────────────────────────────────────────────
# Node type taxonomy  (점 = 명사)
# ──────────────────────────────────────────────────────────────────────────────
# Chat      — 대화 세션
# Document  — 파일 (PDF·PPT·Word·Excel·이미지 등)
# Concept   — 개념·아이디어·기술 용어
# Person    — 사람 (사용자, 언급된 인물)
# Error     — 오류·버그·예외
# Code      — 코드 스니펫·함수·클래스
# Feature   — 소프트웨어 기능
# Task      — 할 일·액션 아이템
# Decision  — 결정 사항

# Edge type vocabulary  (선 = 동사 — 과거형 서술어)
EDGE_VERB = {
    "언급함":   r"언급|mention|refer|cited",
    "포함함":   r"포함|include|consist|구성|탑재|contains",
    "해결함":   r"해결|resolv|fix|수정|고쳤|closed",
    "의존함":   r"의존|depend|require|필요|based on",
    "설명함":   r"설명|explain|describe|정의|란|이란|means",
    "비교함":   r"비교|versus|vs\.?|차이|다르|compare",
    "사용함":   r"사용|use|활용|이용|apply",
    "연결함":   r"연결|connect|통합|integrate|연동|link",
    "확장함":   r"확장|extend|플러그인|plugin|addon",
    "생성함":   r"생성|만들|create|generate|build|produced",
    "대체함":   r"대체|replace|instead|alternative",
    "지원함":   r"지원|support|제공|provide|offer",
    "발생함":   r"발생|occur|throw|raise|triggered",
    "관련됨":   r"관련|related|associated|연관",
}


def _infer_edge(sentence: str) -> str:
    """Return the best-matching verb-form edge label for a sentence."""
    s = sentence.lower()
    for label, pattern in EDGE_VERB.items():
        if re.search(pattern, s):
            return label
    return "관련됨"


# Technical words that cannot be person names
_NOT_PERSON_WORDS: set = {
    "use", "api", "rag", "sdk", "ide", "cli", "llm", "mcp", "ui", "ux",
    "new", "old", "get", "set", "run", "add", "fix", "tool", "code",
    "base", "core", "data", "file", "test", "type", "mode", "view",
}


def _classify_node_type(concept: str, text: str) -> str:
    """Classify a concept into the node taxonomy.

    Term-level signals take priority; then a tight ±60-char window is used
    so distant keywords don't cause mis-classification.
    """
    term = concept.lower()

    # ── Term-level signals (highest confidence) ───────────────────────────
    if re.search(r'(?:error|exception|traceback|오류|에러|버그)$', term, re.I):
        return "Error"
    if re.search(r'error|exception|err\b', term, re.I) and len(concept) < 30:
        return "Error"
    if re.search(r'\(\)|\.py$|\.js$|\.ts$|\.go$|::\w', term):
        return "Code"

    # Person: "First Last" pattern, neither word is a known technical term
    if re.match(r'^[A-Z][a-z]{1,15} [A-Z][a-z]{1,15}$', concept):
        words = term.split()
        if not any(w in _NOT_PERSON_WORDS for w in words):
            return "Person"

    # ── Windowed context (±60 chars) — NOT used for Error to avoid false positives
    idx = text.lower().find(term)
    if idx >= 0:
        win = text[max(0, idx - 60): idx + len(concept) + 60].lower()
        if re.search(r'def |class |function|함수|클래스|메서드|import', win):
            return "Code"
        # Feature: concept appears DIRECTLY adjacent to 기능/feature keyword
        if (
            len(concept) <= 12
            and re.search(
                rf'{re.escape(term)}.{{0,8}}(?:기능|feature)|(?:기능|feature).{{0,8}}{re.escape(term)}',
                win,
            )
        ):
            return "Feature"

    return "Concept"


def _extract_triples(
    text: str,
    concepts: List[str],
    limit: int = 20,
) -> List[Dict[str, str]]:
    """Extract (subject, verb-edge, object, context) triples from text.

    For each sentence containing ≥2 concepts, infer the verb-form edge label
    from surrounding context and create a directed triple.
    """
    if len(concepts) < 2:
        return []

    concept_lower = {c.lower(): c for c in concepts}
    triples: List[Dict[str, str]] = []
    seen_pairs: set = set()

    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?\n])\s+|\n{2,}', text)
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 8:
            continue
        sent_lower = sent.lower()

        present = [concept_lower[k] for k in concept_lower if k in sent_lower]
        if len(present) < 2:
            continue

        edge = _infer_edge(sent)

        for i in range(len(present) - 1):
            subj, obj = present[i], present[i + 1]
            # Deduplicate by (subj, obj) regardless of direction for same edge
            pair_key = tuple(sorted([subj.lower(), obj.lower()])) + (edge,)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            triples.append({
                "subject": subj,
                "relation": edge,          # verb form (동사)
                "object": obj,
                "context": sent[:240],
            })
            if len(triples) >= limit:
                return triples

    return triples


def _semantic_items(text: str) -> List[Dict[str, str]]:
    """Extract explicit decision / task items from text."""
    items: List[Dict[str, str]] = []
    for raw_line in str(text or "").splitlines():
        line = _clean_text(raw_line)
        if len(line) < 6:
            continue
        lowered = line.lower()
        if re.search(r"(결정|확정|하기로|decided|decision)", lowered):
            items.append({"type": "Decision", "title": line[:120], "summary": line[:500]})
        if re.search(r"(todo|해야|하자|진행|구현|수정|확인|next|task|\[ \])", lowered):
            items.append({"type": "Task", "title": line[:120], "summary": line[:500]})
    return items[:8]


def _topic_candidates(text: str, limit: int = 8) -> List[str]:
    """Return compact keyword candidates for fallback graph search."""
    candidates = _extract_concepts(text, limit=limit)
    if candidates:
        return candidates[:limit]
    seen: Dict[str, str] = {}
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_.:-]{2,}|[가-힣]{2,12}", str(text or "")):
        key = token.lower()
        if key in _CONCEPT_STOP or key.isdigit():
            continue
        seen.setdefault(key, token)
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]


class KnowledgeGraphStore:
    def __init__(self, db_path: Path, blob_dir: Path):
        self.db_path = Path(db_path)
        self.blob_dir = Path(blob_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS graph_meta (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nodes (
                  id TEXT PRIMARY KEY,
                  type TEXT NOT NULL,
                  title TEXT NOT NULL,
                  summary TEXT,
                  metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                  raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edges (
                  id TEXT PRIMARY KEY,
                  from_node TEXT NOT NULL,
                  to_node TEXT NOT NULL,
                  type TEXT NOT NULL,
                  weight REAL NOT NULL DEFAULT 1.0,
                  metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                  created_at TEXT NOT NULL,
                  UNIQUE(from_node, to_node, type),
                  FOREIGN KEY(from_node) REFERENCES nodes(id) ON DELETE CASCADE,
                  FOREIGN KEY(to_node) REFERENCES nodes(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS chunks (
                  id TEXT PRIMARY KEY,
                  source_node TEXT NOT NULL,
                  text TEXT NOT NULL,
                  metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(source_node) REFERENCES nodes(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS knowledge_sources (
                  id TEXT PRIMARY KEY,
                  root_path TEXT NOT NULL UNIQUE,
                  os_type TEXT NOT NULL,
                  drive_id TEXT,
                  label TEXT,
                  status TEXT NOT NULL,
                  include_ocr INTEGER NOT NULL DEFAULT 0,
                  watch_enabled INTEGER NOT NULL DEFAULT 0,
                  consent_json TEXT NOT NULL CHECK (json_valid(consent_json)),
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  last_scanned_at TEXT
                );
                CREATE TABLE IF NOT EXISTS local_file_index (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  os_type TEXT NOT NULL,
                  drive_id TEXT,
                  root_path TEXT NOT NULL,
                  file_path TEXT NOT NULL,
                  relative_path TEXT NOT NULL,
                  file_name TEXT NOT NULL,
                  extension TEXT NOT NULL,
                  size_bytes INTEGER,
                  modified_at TEXT,
                  sha256 TEXT,
                  last_scanned_at TEXT,
                  last_indexed_at TEXT,
                  parser_type TEXT,
                  status TEXT NOT NULL,
                  error_message TEXT,
                  graph_node_id TEXT,
                  deleted INTEGER NOT NULL DEFAULT 0,
                  metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
                  UNIQUE(source_id, relative_path),
                  FOREIGN KEY(source_id) REFERENCES knowledge_sources(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
                CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node);
                CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node);
                CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_node);
                CREATE INDEX IF NOT EXISTS idx_knowledge_sources_root ON knowledge_sources(root_path);
                CREATE INDEX IF NOT EXISTS idx_local_file_index_source ON local_file_index(source_id);
                CREATE INDEX IF NOT EXISTS idx_local_file_index_status ON local_file_index(status);
                CREATE INDEX IF NOT EXISTS idx_local_file_index_graph_node ON local_file_index(graph_node_id);
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(GRAPH_SCHEMA_VERSION)),
            )
        self._init_v2_schema()

    def _init_v2_schema(self) -> None:
        """Initialize the PPT-aligned v2 tables alongside the legacy graph tables."""
        if KGStoreV2 is None:
            return
        try:
            KGStoreV2(self.db_path).init_schema()
        except Exception as e:
            logging.warning("knowledge_graph: v2 schema init skipped: %s", e)

    def _upsert_node(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        node_type: str,
        title: str,
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        raw: Optional[Dict[str, Any]] = None,
    ) -> str:
        now = _now()
        conn.execute(
            """
            INSERT INTO nodes(id, type, title, summary, metadata_json, raw_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,
              summary=excluded.summary,
              metadata_json=excluded.metadata_json,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (node_id, node_type, title[:240], summary[:1000], _json(metadata), _json(raw), now, now),
        )
        return node_id

    def _upsert_edge(
        self,
        conn: sqlite3.Connection,
        from_node: str,
        to_node: str,
        edge_type: str,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        edge_id = f"edge:{_sha256_text(f'{from_node}|{edge_type}|{to_node}')[:24]}"
        conn.execute(
            """
            INSERT INTO edges(id, from_node, to_node, type, weight, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(from_node, to_node, type) DO UPDATE SET
              weight=max(edges.weight, excluded.weight),
              metadata_json=excluded.metadata_json
            """,
            (edge_id, from_node, to_node, edge_type, float(weight), _json(metadata), _now()),
        )
        return edge_id

    # ── Local folder sources → Graph RAG ──────────────────────────────────

    def discover_local_roots(self) -> Dict[str, Any]:
        """Return safe, cross-platform starting points for structure browsing."""
        os_type = _current_os_type()
        home = Path.home().expanduser()
        roots: List[Dict[str, Any]] = []
        seen: set = set()

        def add(label: str, path: Path, kind: str, *, recommended: bool = True, warning: Optional[str] = None) -> None:
            try:
                resolved = path.expanduser().resolve()
            except OSError:
                resolved = path.expanduser()
            key = str(resolved)
            if key in seen or not resolved.exists():
                return
            seen.add(key)
            roots.append({
                "id": f"{kind}:{_path_fingerprint(resolved)}",
                "label": label,
                "path": key,
                "kind": kind,
                "recommended": recommended,
                "warning": warning or _root_warning(resolved, os_type),
            })

        add("홈", home, "home", warning=_root_warning(home, os_type))
        for name, label in (
            ("Documents", "문서"),
            ("Desktop", "데스크탑"),
            ("Downloads", "다운로드"),
            ("Pictures", "사진"),
            ("Projects", "프로젝트"),
        ):
            add(label, home / name, name.lower())

        if os_type == "macos":
            volumes = Path("/Volumes")
            if volumes.exists():
                try:
                    for volume in sorted(volumes.iterdir(), key=lambda p: p.name.lower()):
                        add(volume.name, volume, "volume", recommended=False)
                except OSError:
                    pass
        elif os_type == "windows":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = Path(f"{letter}:\\")
                if drive.exists():
                    add(f"{letter}: 드라이브", drive, "drive", recommended=(letter != "C"))
            for env_name, label in (("OneDrive", "OneDrive"), ("OneDriveCommercial", "OneDrive")):
                raw = os.environ.get(env_name)
                if raw:
                    add(label, Path(raw), "cloud", recommended=False)
        elif os_type == "linux":
            for base in (Path("/mnt"), Path("/media")):
                add(str(base), base, "mounts", recommended=False)
                try:
                    if base.exists():
                        for mounted in sorted(base.iterdir(), key=lambda p: p.name.lower()):
                            add(mounted.name, mounted, "volume", recommended=False)
                except OSError:
                    pass

        return {
            "os_type": os_type,
            "computer": platform.node() or "local",
            "roots": roots,
            "privacy_notice": "처음에는 드라이브와 폴더 구조만 확인하며, 파일 내용은 사용자가 동의한 뒤에만 읽습니다.",
        }

    def preview_local_tree(self, path: Path, *, max_items: int = 200) -> Dict[str, Any]:
        """List one folder level using metadata only; file contents are not read."""
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"경로가 존재하지 않습니다: {path}")
        if not root.is_dir():
            raise ValueError(f"폴더가 아닙니다: {path}")

        os_type = _current_os_type()
        max_items = max(1, min(int(max_items or 200), 1000))
        items: List[Dict[str, Any]] = []
        inaccessible = 0
        try:
            children = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError as exc:
            return {
                "path": str(root),
                "items": [],
                "error": f"접근 권한 없음: {exc}",
                "privacy_notice": "현재 단계에서는 파일 내용을 읽지 않고, 폴더와 파일의 이름/크기/수정일만 확인합니다.",
            }

        for child in children[:max_items]:
            try:
                is_dir = child.is_dir()
                stat = child.stat()
                reason = _excluded_directory_reason(child, root=root, os_type=os_type) if is_dir else _sensitive_file_reason(child, root=root)
                items.append({
                    "name": child.name,
                    "path": str(child),
                    "type": "directory" if is_dir else "file",
                    "extension": "" if is_dir else child.suffix.lower(),
                    "size_bytes": None if is_dir else stat.st_size,
                    "modified_at": _safe_iso_from_stat_mtime(stat.st_mtime),
                    "hidden": _is_hidden_path(child, root),
                    "accessible": True,
                    "excluded_reason": reason,
                })
            except PermissionError:
                inaccessible += 1
                items.append({
                    "name": child.name,
                    "path": str(child),
                    "type": "unknown",
                    "accessible": False,
                    "excluded_reason": "permission_denied",
                })
            except OSError as exc:
                inaccessible += 1
                items.append({
                    "name": child.name,
                    "path": str(child),
                    "type": "unknown",
                    "accessible": False,
                    "excluded_reason": str(exc),
                })

        return {
            "path": str(root),
            "os_type": os_type,
            "items": items,
            "truncated": len(children) > max_items,
            "inaccessible": inaccessible,
            "warning": _root_warning(root, os_type),
            "privacy_notice": "현재 단계에서는 파일 내용을 읽지 않고, 폴더와 파일의 이름/크기/수정일만 확인합니다.",
        }

    def _iter_local_scan_entries(self, root: Path, *, max_files: int) -> Iterable[Dict[str, Any]]:
        os_type = _current_os_type()
        stack = [root]
        files_seen = 0
        while stack:
            current = stack.pop()
            try:
                children = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except PermissionError as exc:
                yield {"kind": "inaccessible_dir", "path": current, "reason": f"permission_denied: {exc}"}
                continue
            except OSError as exc:
                yield {"kind": "inaccessible_dir", "path": current, "reason": str(exc)}
                continue

            for child in children:
                if child.is_symlink():
                    yield {"kind": "excluded", "path": child, "reason": "symlink"}
                    continue
                try:
                    if child.is_dir():
                        reason = _excluded_directory_reason(child, root=root, os_type=os_type)
                        if reason:
                            yield {"kind": "excluded_dir", "path": child, "reason": reason}
                        else:
                            stack.append(child)
                        continue
                    if not child.is_file():
                        yield {"kind": "excluded", "path": child, "reason": "not_regular_file"}
                        continue
                    stat = child.stat()
                except PermissionError as exc:
                    yield {"kind": "inaccessible_file", "path": child, "reason": f"permission_denied: {exc}"}
                    continue
                except OSError as exc:
                    yield {"kind": "inaccessible_file", "path": child, "reason": str(exc)}
                    continue

                files_seen += 1
                if files_seen > max_files:
                    yield {"kind": "limit_reached", "path": child, "reason": "max_files"}
                    return
                yield {"kind": "file", "path": child, "stat": stat}

    def _local_file_decision(self, path: Path, root: Path, stat: os.stat_result) -> Dict[str, Any]:
        ext = path.suffix.lower()
        category = _file_category(ext)
        parser_type = _parser_type_for_category(category, ext)
        sensitive_reason = _sensitive_file_reason(path, root=root)
        if sensitive_reason:
            return {
                "status": "sensitive_blocked",
                "reason": sensitive_reason,
                "category": category,
                "parser_type": parser_type,
                "indexable": False,
            }
        if category == "unsupported":
            return {
                "status": "unsupported",
                "reason": "unsupported_extension",
                "category": category,
                "parser_type": parser_type,
                "indexable": False,
            }
        limit = _size_limit_for_category(category)
        if stat.st_size > limit:
            return {
                "status": "too_large",
                "reason": f"size>{limit}",
                "category": category,
                "parser_type": parser_type,
                "indexable": False,
            }
        return {
            "status": "pending",
            "reason": "",
            "category": category,
            "parser_type": parser_type,
            "indexable": True,
        }

    def audit_local_folder(self, path: Path, *, include_ocr: bool = False, max_files: int = 50_000) -> Dict[str, Any]:
        """Safety-check a folder using metadata only; file bodies are not read."""
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"경로가 존재하지 않습니다: {path}")
        if not root.is_dir():
            raise ValueError(f"폴더가 아닙니다: {path}")

        os_type = _current_os_type()
        max_files = max(1, min(int(max_files or 50_000), 200_000))
        status_counts: Counter = Counter()
        category_counts: Counter = Counter()
        extension_counts: Counter = Counter()
        allowed_samples: List[Dict[str, Any]] = []
        excluded_samples: List[Dict[str, Any]] = []
        total_files = 0
        readable_files = 0
        inaccessible = 0
        excluded_dirs = 0
        limit_reached = False

        for entry in self._iter_local_scan_entries(root, max_files=max_files):
            kind = entry["kind"]
            path_obj = entry["path"]
            if kind == "limit_reached":
                limit_reached = True
                break
            if kind == "excluded_dir":
                excluded_dirs += 1
                if len(excluded_samples) < 25:
                    excluded_samples.append(_sample_file(path_obj, root, "excluded", entry.get("reason", "")))
                continue
            if kind in {"inaccessible_dir", "inaccessible_file"}:
                inaccessible += 1
                status_counts["failed"] += 1
                if len(excluded_samples) < 25:
                    excluded_samples.append(_sample_file(path_obj, root, "failed", entry.get("reason", "")))
                continue
            if kind == "excluded":
                status_counts["excluded"] += 1
                if len(excluded_samples) < 25:
                    excluded_samples.append(_sample_file(path_obj, root, "excluded", entry.get("reason", "")))
                continue
            if kind != "file":
                continue

            total_files += 1
            stat = entry["stat"]
            decision = self._local_file_decision(path_obj, root, stat)
            status = decision["status"]
            category = decision["category"]
            ext = path_obj.suffix.lower() or "(none)"
            category_counts[category] += 1
            extension_counts[ext] += 1
            if decision["indexable"]:
                readable_files += 1
                status_counts["readable"] += 1
                if len(allowed_samples) < 25:
                    allowed_samples.append(_sample_file(path_obj, root, "readable"))
            else:
                status_counts[status] += 1
                if len(excluded_samples) < 25:
                    excluded_samples.append(_sample_file(path_obj, root, status, decision["reason"]))

        doc_weight = category_counts["pdf"] * 1.4 + category_counts["document"] * 0.9 + category_counts["slide_deck"] * 1.0
        sheet_weight = category_counts["spreadsheet"] * 0.6
        ocr_weight = category_counts["image"] * (1.8 if include_ocr else 0.1)
        estimated_seconds = round(readable_files * 0.04 + doc_weight + sheet_weight + ocr_weight, 1)

        return {
            "path": str(root),
            "source_id": f"source:{_path_fingerprint(root)}",
            "os_type": os_type,
            "drive_id": _drive_id_for_path(root),
            "warning": _root_warning(root, os_type),
            "privacy_notice": "현재 단계에서는 파일 내용을 읽지 않고, 폴더와 파일의 이름/크기/수정일만 확인합니다.",
            "include_ocr_requested": bool(include_ocr),
            "summary": {
                "total_files": total_files,
                "readable_files": readable_files,
                "excluded_files": int(
                    status_counts["excluded"]
                    + status_counts["sensitive_blocked"]
                    + status_counts["too_large"]
                    + status_counts["unsupported"]
                ),
                "sensitive_files": int(status_counts["sensitive_blocked"]),
                "too_large_files": int(status_counts["too_large"]),
                "unsupported_files": int(status_counts["unsupported"]),
                "image_ocr_candidates": int(category_counts["image"]),
                "inaccessible_items": inaccessible,
                "excluded_dirs": excluded_dirs,
                "estimated_seconds": estimated_seconds,
                "storage_root": str(self.db_path.parent),
                "limit_reached": limit_reached,
            },
            "by_status": dict(status_counts),
            "by_category": dict(category_counts),
            "by_extension": dict(extension_counts.most_common(40)),
            "allowed_samples": allowed_samples,
            "excluded_samples": excluded_samples,
            "consent_required": {
                "knowledge_source": True,
                "image_ocr": bool(category_counts["image"]),
                "watch": True,
                "sensitive_files_default_excluded": True,
            },
        }

    def local_sources(self) -> Dict[str, Any]:
        with self._connect() as conn:
            sources = [
                {
                    "id": row["id"],
                    "root_path": row["root_path"],
                    "os_type": row["os_type"],
                    "drive_id": row["drive_id"],
                    "label": row["label"],
                    "status": row["status"],
                    "include_ocr": bool(row["include_ocr"]),
                    "watch_enabled": bool(row["watch_enabled"]),
                    "consent": _safe_loads(row["consent_json"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "last_scanned_at": row["last_scanned_at"],
                }
                for row in conn.execute(
                    """
                    SELECT id, root_path, os_type, drive_id, label, status, include_ocr,
                           watch_enabled, consent_json, created_at, updated_at, last_scanned_at
                    FROM knowledge_sources
                    ORDER BY updated_at DESC
                    """
                )
            ]
            status_rows = conn.execute(
                "SELECT source_id, status, COUNT(*) AS count FROM local_file_index GROUP BY source_id, status"
            ).fetchall()
        counts: Dict[str, Dict[str, int]] = {}
        for row in status_rows:
            counts.setdefault(row["source_id"], {})[row["status"]] = row["count"]
        for source in sources:
            source["file_status"] = counts.get(source["id"], {})
        return {"sources": sources}

    def set_local_source_watch(self, source_id: str, enabled: bool) -> Dict[str, Any]:
        source_id = str(source_id or "").strip()
        if not source_id:
            raise ValueError("source_id required")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM knowledge_sources WHERE id=?",
                (source_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"knowledge source not found: {source_id}")
            conn.execute(
                "UPDATE knowledge_sources SET watch_enabled=?, updated_at=? WHERE id=?",
                (1 if enabled else 0, _now(), source_id),
            )
        return {"source_id": source_id, "watch_enabled": bool(enabled)}

    def _extract_local_file_text(self, path: Path, category: str, *, include_ocr: bool) -> Tuple[str, Dict[str, Any]]:
        ext = path.suffix.lower()
        meta: Dict[str, Any] = {"parser": _parser_type_for_category(category, ext)}
        text = ""
        if category in {"text", "code"} or ext == ".csv":
            text = path.read_text(encoding="utf-8", errors="replace")
        elif ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                meta["pages"] = len(pdf.pages)
                text = "\n\n".join((page.extract_text() or "") for page in pdf.pages)
        elif ext == ".docx":
            from docx import Document
            doc = Document(str(path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            table_lines = []
            for table in doc.tables:
                for row in table.rows:
                    cells = [_clean_text(cell.text) for cell in row.cells]
                    if any(cells):
                        table_lines.append("\t".join(cells))
            meta["paragraphs"] = len(paragraphs)
            meta["tables"] = len(doc.tables)
            meta["table_rows"] = len(table_lines)
            text = "\n\n".join([*paragraphs, *table_lines])
        elif ext == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(str(path), read_only=True, data_only=True)
            rows_all = []
            non_empty_rows = 0
            non_empty_cells = 0
            char_count = 0
            for ws in wb.worksheets:
                sheet_rows = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(cell).strip() if cell is not None else "" for cell in row]
                    if not any(cells):
                        continue
                    line = "\t".join(cells)
                    non_empty_rows += 1
                    non_empty_cells += sum(1 for cell in cells if cell)
                    sheet_rows.append(line)
                    char_count += len(line) + 1
                    if char_count > 200_000:
                        break
                if sheet_rows:
                    rows_all.append(f"[Sheet: {ws.title}]")
                    rows_all.extend(sheet_rows)
                if char_count > 200_000:
                    break
            meta["sheets"] = len(wb.worksheets)
            meta["rows"] = non_empty_rows
            meta["cells"] = non_empty_cells
            text = "\n".join(rows_all)
        elif ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(str(path))
            slides_text = []
            for index, slide in enumerate(prs.slides, 1):
                parts = []
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False):
                        slide_text = shape.text_frame.text.strip()
                        if slide_text:
                            parts.append(slide_text)
                if parts:
                    slides_text.append(f"[Slide {index}]\n" + "\n".join(parts))
            meta["slides"] = len(prs.slides)
            meta["text_slides"] = len(slides_text)
            text = "\n\n".join(slides_text)
        elif category == "image":
            from PIL import Image
            with Image.open(str(path)) as image:
                meta.update({
                    "width": image.width,
                    "height": image.height,
                    "format": image.format,
                    "mode": image.mode,
                    "ocr_enabled": bool(include_ocr),
                })
                if include_ocr:
                    try:
                        import pytesseract
                        text = pytesseract.image_to_string(image)
                        meta["ocr_chars"] = len(text)
                    except Exception as exc:  # pragma: no cover - depends on local OCR runtime
                        meta["ocr_error"] = str(exc)
                        text = ""
        return text[:200_000], meta

    def _ensure_local_hierarchy(
        self,
        conn: sqlite3.Connection,
        *,
        source_id: str,
        root: Path,
        file_path: Path,
        os_type: str,
        drive_id: str,
    ) -> str:
        computer_label = platform.node() or "내 컴퓨터"
        computer_id = f"computer:{_slug(computer_label)}"
        drive_node_id = f"drive:{_sha256_text(f'{os_type}:{drive_id}')[:24]}"
        root_folder_id = f"folder:{_sha256_text(f'{source_id}:root')[:24]}"
        self._upsert_node(conn, computer_id, "Computer", computer_label, metadata={"os_type": os_type})
        self._upsert_node(conn, drive_node_id, "Drive", drive_id, metadata={"os_type": os_type, "drive_id": drive_id})
        self._upsert_edge(conn, computer_id, drive_node_id, "포함함", metadata={"source": "local_scan"})
        self._upsert_node(
            conn,
            root_folder_id,
            "Folder",
            root.name or str(root),
            summary=str(root),
            metadata={"source_id": source_id, "path": str(root), "root": True},
        )
        self._upsert_edge(conn, drive_node_id, root_folder_id, "포함함", metadata={"source": "local_scan"})

        try:
            relative_parent = file_path.parent.relative_to(root)
        except ValueError:
            relative_parent = Path()
        parent_id = root_folder_id
        current_path = root
        for part in relative_parent.parts:
            current_path = current_path / part
            folder_id = f"folder:{_sha256_text(f'{source_id}:{current_path.as_posix()}')[:24]}"
            self._upsert_node(
                conn,
                folder_id,
                "Folder",
                part,
                summary=str(current_path),
                metadata={"source_id": source_id, "path": str(current_path), "root": False},
            )
            self._upsert_edge(conn, parent_id, folder_id, "포함함", metadata={"source": "local_scan"})
            parent_id = folder_id
        return parent_id

    def _upsert_local_file_index(
        self,
        conn: sqlite3.Connection,
        *,
        source_id: str,
        root: Path,
        file_path: Path,
        stat: Optional[os.stat_result],
        os_type: str,
        drive_id: str,
        status: str,
        parser_type: str,
        sha256: Optional[str] = None,
        graph_node_id: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        try:
            relative_path = file_path.relative_to(root).as_posix()
        except ValueError:
            relative_path = file_path.name
        index_id = f"local-index:{_sha256_text(f'{source_id}:{relative_path}')[:24]}"
        now = _now()
        size = stat.st_size if stat else None
        modified_at = _safe_iso_from_stat_mtime(stat.st_mtime) if stat else ""
        conn.execute(
            """
            INSERT INTO local_file_index(
              id, source_id, os_type, drive_id, root_path, file_path, relative_path,
              file_name, extension, size_bytes, modified_at, sha256, last_scanned_at,
              last_indexed_at, parser_type, status, error_message, graph_node_id,
              deleted, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, relative_path) DO UPDATE SET
              os_type=excluded.os_type,
              drive_id=excluded.drive_id,
              root_path=excluded.root_path,
              file_path=excluded.file_path,
              file_name=excluded.file_name,
              extension=excluded.extension,
              size_bytes=excluded.size_bytes,
              modified_at=excluded.modified_at,
              sha256=excluded.sha256,
              last_scanned_at=excluded.last_scanned_at,
              last_indexed_at=excluded.last_indexed_at,
              parser_type=excluded.parser_type,
              status=excluded.status,
              error_message=excluded.error_message,
              graph_node_id=excluded.graph_node_id,
              deleted=excluded.deleted,
              metadata_json=excluded.metadata_json
            """,
            (
                index_id, source_id, os_type, drive_id, str(root), str(file_path), relative_path,
                file_path.name, file_path.suffix.lower(), size, modified_at, sha256, now,
                now if status == "indexed" else None, parser_type, status, error_message,
                graph_node_id, 0 if status != "deleted" else 1, _json(metadata),
            ),
        )
        return index_id

    def _delete_local_file_graph(self, conn: sqlite3.Connection, file_node_id: Optional[str]) -> None:
        if not file_node_id:
            return

        file_row = conn.execute(
            "SELECT metadata_json FROM nodes WHERE id=?",
            (file_node_id,),
        ).fetchone()
        source_id = None
        if file_row:
            source_id = _safe_loads(file_row["metadata_json"]).get("source_id")

        linked_rows = conn.execute(
            """
            SELECT n.id, n.type, n.metadata_json
            FROM edges e
            JOIN nodes n ON n.id=e.to_node
            WHERE e.from_node=?
            """,
            (file_node_id,),
        ).fetchall()
        owned_ids: set = set()
        auto_candidate_ids: set = set()
        for row in linked_rows:
            metadata = _safe_loads(row["metadata_json"])
            if row["type"] in {"Chunk", "ImageText", "Section"} or metadata.get("source_node") == file_node_id:
                owned_ids.add(row["id"])
            elif metadata.get("auto_extracted") and metadata.get("source") == "local_folder":
                auto_candidate_ids.add(row["id"])

        conn.execute("DELETE FROM chunks WHERE source_node=?", (file_node_id,))
        conn.execute("DELETE FROM edges WHERE from_node=? OR to_node=?", (file_node_id, file_node_id))
        conn.execute("DELETE FROM nodes WHERE id=?", (file_node_id,))

        def delete_nodes(node_ids: set) -> None:
            if not node_ids:
                return
            placeholders = ",".join("?" * len(node_ids))
            params = list(node_ids)
            conn.execute(f"DELETE FROM chunks WHERE source_node IN ({placeholders})", params)
            conn.execute(f"DELETE FROM edges WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})", params * 2)
            conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", params)

        delete_nodes(owned_ids)

        removable_auto_ids: set = set()
        for node_id in auto_candidate_ids:
            remaining_edges = conn.execute(
                "SELECT from_node, to_node FROM edges WHERE from_node=? OR to_node=?",
                (node_id, node_id),
            ).fetchall()
            if all(
                (row["from_node"] in auto_candidate_ids and row["to_node"] in auto_candidate_ids)
                for row in remaining_edges
            ):
                removable_auto_ids.add(node_id)
        delete_nodes(removable_auto_ids)
        if source_id:
            self._cleanup_local_graph_orphans(conn, str(source_id))

    def _cleanup_local_graph_orphans(self, conn: sqlite3.Connection, source_id: str) -> None:
        while True:
            folder_rows = conn.execute(
                "SELECT id, metadata_json FROM nodes WHERE type='Folder'"
            ).fetchall()
            leaf_ids = []
            for row in folder_rows:
                metadata = _safe_loads(row["metadata_json"])
                if metadata.get("source_id") != source_id:
                    continue
                has_children = conn.execute(
                    "SELECT 1 FROM edges WHERE from_node=? LIMIT 1",
                    (row["id"],),
                ).fetchone()
                if not has_children:
                    leaf_ids.append(row["id"])
            if not leaf_ids:
                break
            placeholders = ",".join("?" * len(leaf_ids))
            conn.execute(f"DELETE FROM edges WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})", leaf_ids * 2)
            conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", leaf_ids)

        for node_type in ("Drive", "Computer"):
            rows = conn.execute("SELECT id FROM nodes WHERE type=?", (node_type,)).fetchall()
            removable = []
            for row in rows:
                has_children = conn.execute(
                    "SELECT 1 FROM edges WHERE from_node=? LIMIT 1",
                    (row["id"],),
                ).fetchone()
                if not has_children:
                    removable.append(row["id"])
            if removable:
                placeholders = ",".join("?" * len(removable))
                conn.execute(f"DELETE FROM edges WHERE from_node IN ({placeholders}) OR to_node IN ({placeholders})", removable * 2)
                conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", removable)

    def _local_file_index_has_extracted_text(self, row: sqlite3.Row) -> bool:
        metadata = _safe_loads(row["metadata_json"])
        parser = metadata.get("parser") if isinstance(metadata, dict) else {}
        if not isinstance(parser, dict):
            return False
        try:
            return int(parser.get("extracted_chars") or 0) > 0
        except (TypeError, ValueError):
            return False

    def _upsert_local_file_node(
        self,
        conn: sqlite3.Connection,
        *,
        source_id: str,
        root: Path,
        file_path: Path,
        stat: os.stat_result,
        os_type: str,
        drive_id: str,
        sha256: str,
        category: str,
        parser_type: str,
        text: str,
        parser_meta: Dict[str, Any],
    ) -> str:
        text = _clean_text(text)
        if not text:
            raise ValueError("텍스트 추출 결과가 비어 있습니다.")
        try:
            relative_path = file_path.relative_to(root).as_posix()
        except ValueError:
            relative_path = file_path.name
        file_node_id = f"local-file:{_sha256_text(f'{source_id}:{relative_path}')[:24]}"
        parent_folder_id = self._ensure_local_hierarchy(
            conn,
            source_id=source_id,
            root=root,
            file_path=file_path,
            os_type=os_type,
            drive_id=drive_id,
        )
        child_rows = conn.execute(
            """
            SELECT e.to_node AS id
            FROM edges e
            JOIN nodes n ON n.id=e.to_node
            WHERE e.from_node=? AND n.type IN ('Chunk', 'ImageText', 'Section')
            """,
            (file_node_id,),
        ).fetchall()
        child_ids = [row["id"] for row in child_rows]
        conn.execute("DELETE FROM chunks WHERE source_node=?", (file_node_id,))
        if child_ids:
            placeholders = ",".join("?" * len(child_ids))
            conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", child_ids)
        conn.execute("DELETE FROM edges WHERE from_node=?", (file_node_id,))

        metadata = {
            "source": "local_folder",
            "source_id": source_id,
            "root_path": str(root),
            "file_path": str(file_path),
            "relative_path": relative_path,
            "filename": file_path.name,
            "ext": file_path.suffix.lower(),
            "category": category,
            "parser_type": parser_type,
            "bytes": stat.st_size,
            "modified_at": _safe_iso_from_stat_mtime(stat.st_mtime),
            "sha256": sha256,
            "parser": parser_meta,
        }
        self._upsert_node(
            conn,
            file_node_id,
            _node_type_for_category(category),
            file_path.name,
            summary=text[:700],
            metadata=metadata,
            raw=metadata,
        )
        self._upsert_edge(conn, parent_folder_id, file_node_id, "포함함", weight=1.0, metadata={"source": "local_scan"})

        target_for_concepts = text
        if category == "image" and text:
            image_text_id = f"imagetext:{_sha256_text(f'{file_node_id}:ocr')[:24]}"
            self._upsert_node(
                conn,
                image_text_id,
                "ImageText",
                f"{file_path.name} OCR",
                summary=_clean_text(text)[:700],
                metadata={"source_node": file_node_id, "source_id": source_id, "chars": len(text)},
            )
            self._upsert_edge(conn, file_node_id, image_text_id, "포함함", weight=0.8, metadata={"source": "ocr"})

        for index, chunk in enumerate(_chunks(text)):
            chunk_id = f"chunk:{_sha256_text(f'{file_node_id}:{index}:{chunk}')[:24]}"
            self._upsert_node(
                conn,
                chunk_id,
                "Chunk",
                f"{file_path.name} chunk {index + 1}",
                summary=chunk[:500],
                metadata={"index": index, "source_node": file_node_id, "source_id": source_id},
            )
            conn.execute(
                "INSERT OR REPLACE INTO chunks(id, source_node, text, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    file_node_id,
                    chunk,
                    _json({"index": index, "source_node": file_node_id, "source_id": source_id}),
                    _now(),
                ),
            )
            self._upsert_edge(conn, file_node_id, chunk_id, "포함함", weight=0.7, metadata={"source": "local_scan"})

        concepts = _extract_concepts(target_for_concepts, limit=18)
        concept_ids: Dict[str, str] = {}
        for concept in concepts:
            node_t = _classify_node_type(concept, target_for_concepts)
            concept_id = f"{node_t.lower()}:{_slug(concept)}"
            concept_ids[concept.lower()] = concept_id
            self._upsert_node(
                conn,
                concept_id,
                node_t,
                concept,
                metadata={"auto_extracted": True, "source": "local_folder", "source_id": source_id},
            )
            self._upsert_edge(conn, file_node_id, concept_id, "언급함", weight=0.75, metadata={"source": "local_scan"})

        for triple in _extract_triples(target_for_concepts, concepts, limit=20):
            subj_id = concept_ids.get(triple["subject"].lower())
            obj_id = concept_ids.get(triple["object"].lower())
            if subj_id and obj_id and subj_id != obj_id:
                self._upsert_edge(
                    conn,
                    subj_id,
                    obj_id,
                    triple["relation"],
                    weight=0.9,
                    metadata={"context": triple.get("context", "")[:240], "source_id": source_id},
                )

        for item in _semantic_items(target_for_concepts):
            sem_type = item["type"]
            sem_title = item["title"]
            sem_id = f"{sem_type.lower()}:{_sha256_text(f'{file_node_id}:{sem_type}:{sem_title}')[:24]}"
            self._upsert_node(
                conn,
                sem_id,
                sem_type,
                sem_title,
                summary=item["summary"],
                metadata={"auto_extracted": True, "source_node": file_node_id, "filename": file_path.name},
                raw=item,
            )
            self._upsert_edge(conn, file_node_id, sem_id, "포함함", weight=0.9)

        return file_node_id

    def index_local_folder(
        self,
        path: Path,
        *,
        include_ocr: bool = False,
        watch_enabled: bool = False,
        user_email: Optional[str] = None,
        consent: Optional[Dict[str, Any]] = None,
        max_files: int = 5_000,
    ) -> Dict[str, Any]:
        """Read approved files from a local folder and connect them to Graph RAG."""
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"경로가 존재하지 않습니다: {path}")
        if not root.is_dir():
            raise ValueError(f"폴더가 아닙니다: {path}")

        os_type = _current_os_type()
        drive_id = _drive_id_for_path(root)
        source_id = f"source:{_path_fingerprint(root)}"
        now = _now()
        max_files = max(1, min(int(max_files or 5_000), 50_000))
        consent_payload = {
            "approved_at": now,
            "approved_by": user_email,
            "knowledge_source": True,
            "include_ocr": bool(include_ocr),
            "watch_enabled": bool(watch_enabled),
            "sensitive_files_default_excluded": True,
            **(consent or {}),
        }
        counts: Counter = Counter()
        seen_relative_paths: set = set()
        indexed_nodes: List[str] = []
        errors: List[Dict[str, str]] = []
        limit_reached = False

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_sources(
                  id, root_path, os_type, drive_id, label, status, include_ocr,
                  watch_enabled, consent_json, created_at, updated_at, last_scanned_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  root_path=excluded.root_path,
                  os_type=excluded.os_type,
                  drive_id=excluded.drive_id,
                  label=excluded.label,
                  status=excluded.status,
                  include_ocr=excluded.include_ocr,
                  watch_enabled=excluded.watch_enabled,
                  consent_json=excluded.consent_json,
                  updated_at=excluded.updated_at,
                  last_scanned_at=excluded.last_scanned_at
                """,
                (
                    source_id, str(root), os_type, drive_id, root.name or str(root), "scanning",
                    1 if include_ocr else 0, 1 if watch_enabled else 0, _json(consent_payload),
                    now, now, now,
                ),
            )

            for entry in self._iter_local_scan_entries(root, max_files=max_files):
                kind = entry["kind"]
                file_path = entry["path"]
                if kind == "limit_reached":
                    counts["limit_reached"] += 1
                    limit_reached = True
                    break
                if kind in {"excluded_dir", "excluded"}:
                    counts["excluded"] += 1
                    continue
                if kind in {"inaccessible_dir", "inaccessible_file"}:
                    counts["failed"] += 1
                    errors.append({"path": str(file_path), "error": entry.get("reason", "inaccessible")})
                    continue
                if kind != "file":
                    continue

                stat = entry["stat"]
                try:
                    relative_path = file_path.relative_to(root).as_posix()
                except ValueError:
                    relative_path = file_path.name
                seen_relative_paths.add(relative_path)
                modified_at = _safe_iso_from_stat_mtime(stat.st_mtime)
                existing = conn.execute(
                    """
                    SELECT size_bytes, modified_at, sha256, graph_node_id, status, metadata_json
                    FROM local_file_index
                    WHERE source_id=? AND relative_path=?
                    """,
                    (source_id, relative_path),
                ).fetchone()
                decision = self._local_file_decision(file_path, root, stat)
                parser_type = decision["parser_type"]
                if not decision["indexable"]:
                    counts[decision["status"]] += 1
                    if existing and existing["graph_node_id"]:
                        self._delete_local_file_graph(conn, existing["graph_node_id"])
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status=decision["status"],
                        parser_type=parser_type,
                        metadata={"reason": decision["reason"], "category": decision["category"]},
                    )
                    continue

                if (
                    existing
                    and existing["status"] == "indexed"
                    and existing["graph_node_id"]
                    and self._local_file_index_has_extracted_text(existing)
                    and existing["size_bytes"] == stat.st_size
                    and existing["modified_at"] == modified_at
                ):
                    counts["skipped_unchanged"] += 1
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="indexed",
                        parser_type=parser_type,
                        sha256=existing["sha256"],
                        graph_node_id=existing["graph_node_id"],
                        metadata={**_safe_loads(existing["metadata_json"]), "category": decision["category"], "unchanged": True},
                    )
                    continue

                try:
                    data = file_path.read_bytes()
                    digest = _sha256_bytes(data)
                except Exception as exc:
                    counts["failed"] += 1
                    errors.append({"path": str(file_path), "error": str(exc)})
                    if existing and existing["graph_node_id"]:
                        self._delete_local_file_graph(conn, existing["graph_node_id"])
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="failed",
                        parser_type=parser_type,
                        error_message=str(exc),
                        metadata={"category": decision["category"]},
                    )
                    continue

                if (
                    existing
                    and existing["sha256"] == digest
                    and existing["graph_node_id"]
                    and self._local_file_index_has_extracted_text(existing)
                ):
                    counts["skipped_unchanged"] += 1
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="indexed",
                        parser_type=parser_type,
                        sha256=digest,
                        graph_node_id=existing["graph_node_id"],
                        metadata={**_safe_loads(existing["metadata_json"]), "category": decision["category"], "sha256_unchanged": True},
                    )
                    continue

                try:
                    text, parser_meta = self._extract_local_file_text(
                        file_path,
                        decision["category"],
                        include_ocr=include_ocr,
                    )
                    text = _clean_text(text)
                    parser_meta = {**parser_meta, "extracted_chars": len(text)}
                    if not text:
                        counts["skipped_empty_text"] += 1
                        if existing and existing["graph_node_id"]:
                            self._delete_local_file_graph(conn, existing["graph_node_id"])
                        self._upsert_local_file_index(
                            conn,
                            source_id=source_id,
                            root=root,
                            file_path=file_path,
                            stat=stat,
                            os_type=os_type,
                            drive_id=drive_id,
                            status="skipped_empty_text",
                            parser_type=parser_type,
                            sha256=digest,
                            error_message="텍스트 추출 결과가 비어 있습니다.",
                            metadata={"category": decision["category"], "parser": parser_meta},
                        )
                        continue
                    graph_node_id = self._upsert_local_file_node(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        sha256=digest,
                        category=decision["category"],
                        parser_type=parser_type,
                        text=text,
                        parser_meta=parser_meta,
                    )
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="indexed",
                        parser_type=parser_type,
                        sha256=digest,
                        graph_node_id=graph_node_id,
                        metadata={"category": decision["category"], "parser": parser_meta},
                    )
                    counts["indexed"] += 1
                    indexed_nodes.append(graph_node_id)
                except Exception as exc:
                    counts["failed"] += 1
                    errors.append({"path": str(file_path), "error": str(exc)})
                    if existing and existing["graph_node_id"]:
                        self._delete_local_file_graph(conn, existing["graph_node_id"])
                    self._upsert_local_file_index(
                        conn,
                        source_id=source_id,
                        root=root,
                        file_path=file_path,
                        stat=stat,
                        os_type=os_type,
                        drive_id=drive_id,
                        status="failed",
                        parser_type=parser_type,
                        sha256=digest,
                        error_message=str(exc),
                        metadata={"category": decision["category"]},
                    )

            if not limit_reached:
                existing_rows = {
                    row["relative_path"]: row["graph_node_id"]
                    for row in conn.execute(
                        "SELECT relative_path, graph_node_id FROM local_file_index WHERE source_id=?",
                        (source_id,),
                    )
                }
                deleted_paths = set(existing_rows) - seen_relative_paths
                for relative_path in deleted_paths:
                    self._delete_local_file_graph(conn, existing_rows.get(relative_path))
                    conn.execute(
                        """
                        UPDATE local_file_index
                        SET status='deleted', deleted=1, last_scanned_at=?, error_message=NULL, graph_node_id=NULL
                        WHERE source_id=? AND relative_path=?
                        """,
                        (_now(), source_id, relative_path),
                    )
                counts["deleted"] = len(deleted_paths)
            conn.execute(
                """
                UPDATE knowledge_sources
                SET status='active', updated_at=?, last_scanned_at=?
                WHERE id=?
                """,
                (_now(), _now(), source_id),
            )

        return {
            "status": "ok",
            "source": {
                "id": source_id,
                "root_path": str(root),
                "os_type": os_type,
                "drive_id": drive_id,
                "include_ocr": bool(include_ocr),
                "watch_enabled": bool(watch_enabled),
            },
            "counts": dict(counts),
            "indexed_nodes": indexed_nodes[:100],
            "errors": errors[:50],
            "notice": "Lattice AI는 사용자가 선택한 폴더만 AI 지식으로 변환합니다.",
        }

    def ingest_message(
        self,
        role: str,
        content: str,
        *,
        user_email: Optional[str] = None,
        user_nickname: Optional[str] = None,
        source: Optional[str] = None,
        conversation_id: Optional[str] = None,
        raw: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        content = str(content or "")
        digest = _sha256_text("|".join([role or "", content, conversation_id or "", user_email or ""]))[:24]
        node_type = "AIResponse" if role == "assistant" else "Message"
        node_id = f"{node_type.lower()}:{digest}"
        conv_id = f"conversation:{_slug(conversation_id or 'default')}"
        metadata = {
            "role": role,
            "source": source,
            "conversation_id": conversation_id,
            "user_email": user_email,
            "user_nickname": user_nickname,
            "chars": len(content),
        }
        concepts = _extract_concepts(content)
        triples  = _extract_triples(content, concepts)
        semantic = _semantic_items(content)

        with self._connect() as conn:
            # ── 1. Chat node  (점: 명사 — 대화 세션 단위) ─────────────────────
            #    One Chat node per conversation_id; title = first 80 chars of
            #    the first user message in this session (updated on each call).
            chat_title = _clean_text(content)[:80] or (conversation_id or "대화")
            self._upsert_node(
                conn, conv_id, "Chat",
                chat_title,
                summary=_clean_text(content)[:400],
                metadata={"source": source, "conversation_id": conversation_id},
            )

            # ── 2. Person node  (점: 명사 — 사람) ─────────────────────────────
            person_id = None
            if user_email or user_nickname:
                person_key = user_email or user_nickname or "unknown"
                person_id = f"person:{_slug(person_key)}"
                self._upsert_node(
                    conn, person_id, "Person",
                    user_nickname or user_email or "Unknown",
                    metadata={"email": user_email, "nickname": user_nickname},
                )
                # 선: 동사 — Person이 Chat을 "작성함"
                self._upsert_edge(conn, person_id, conv_id, "작성함",
                                  weight=1.0, metadata={"role": role})

            # ── 3. Raw message node  (RAG 검색용, 그래프에서 숨김) ─────────────
            self._upsert_node(
                conn, node_id, node_type,
                _clean_text(content)[:80] or role,
                summary=_clean_text(content)[:500],
                metadata=metadata,
                raw=raw or metadata,
            )
            # 선: Chat이 메시지를 "포함함"
            self._upsert_edge(conn, conv_id, node_id, "포함함",
                              weight=0.3, metadata={"role": role})

            # ── 4. RAG chunks  (검색용, 그래프에서 숨김) ──────────────────────
            for index, chunk in enumerate(_chunks(content)):
                chunk_id = f"chunk:{_sha256_text(f'{node_id}:{index}:{chunk}')[:24]}"
                self._upsert_node(
                    conn, chunk_id, "Chunk",
                    f"chunk {index + 1}",
                    summary=chunk[:500],
                    metadata={"index": index, "source_node": node_id},
                )
                conn.execute(
                    "INSERT OR REPLACE INTO chunks(id, source_node, text, metadata_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, node_id, chunk,
                     _json({"index": index, "source_node": node_id}), _now()),
                )
                self._upsert_edge(conn, node_id, chunk_id, "포함함")

            # ── 5. Concept / Feature / Error / Code 노드  (점: 명사) ───────────
            concept_ids: Dict[str, str] = {}
            for concept in concepts:
                node_t = _classify_node_type(concept, content)
                cid = f"{node_t.lower()}:{_slug(concept)}"
                concept_ids[concept.lower()] = cid
                self._upsert_node(
                    conn, cid, node_t, concept,
                    metadata={"auto_extracted": True, "source": source},
                )
                # 선: Chat이 개념을 "언급함"
                self._upsert_edge(conn, conv_id, cid, "언급함",
                                  weight=0.7, metadata={"source": source})

            # ── 6. Concept–Concept 엣지  (선: 동사형) ─────────────────────────
            for triple in triples:
                subj_id = concept_ids.get(triple["subject"].lower())
                obj_id  = concept_ids.get(triple["object"].lower())
                if subj_id and obj_id and subj_id != obj_id:
                    self._upsert_edge(
                        conn, subj_id, obj_id,
                        triple["relation"],          # 동사형 레이블
                        weight=1.0,
                        metadata={"context": triple.get("context", "")[:240]},
                    )

            # ── 7. Task / Decision 노드  (점: 명사) ────────────────────────────
            for item in semantic:
                sem_type  = item["type"]
                sem_title = item["title"]
                sem_id = f"{sem_type.lower()}:{_sha256_text(f'{conv_id}:{sem_type}:{sem_title}')[:24]}"
                self._upsert_node(
                    conn, sem_id, sem_type, sem_title,
                    summary=item["summary"],
                    metadata={"auto_extracted": True, "source_node": node_id},
                    raw=item,
                )
                # 선: Chat이 Task/Decision을 "생성함"
                self._upsert_edge(conn, conv_id, sem_id, "생성함", weight=0.9)
                # Task/Decision이 관련 개념을 "언급함"
                for cid in list(concept_ids.values())[:3]:
                    self._upsert_edge(conn, sem_id, cid, "언급함", weight=0.6)

        return {"node_id": node_id, "type": node_type}

    def ingest_document(
        self,
        path: Path,
        *,
        original_filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        uploader: Optional[str] = None,
        conversation_id: Optional[str] = None,
        extracted: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path = Path(path)
        data = path.read_bytes()
        digest = _sha256_bytes(data)
        ext = path.suffix.lower()
        filename = original_filename or path.name
        blob_path = self.blob_dir / digest[:2] / f"{digest}{ext}"
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        if not blob_path.exists():
            shutil.copyfile(path, blob_path)

        doc_meta = self._document_structure(path, ext)
        text = str((extracted or {}).get("content") or (extracted or {}).get("preview") or "")
        file_id = f"file:{digest[:24]}"
        metadata = {
            "filename": filename,
            "ext": ext,
            "mime_type": mime_type,
            "bytes": len(data),
            "sha256": digest,
            "blob_path": str(blob_path),
            "uploader": uploader,
            "conversation_id": conversation_id,
            "extracted": {k: v for k, v in (extracted or {}).items() if k != "content"},
            "structure": doc_meta,
        }
        full_text = f"{filename}\n{text}"
        concepts = _extract_concepts(full_text, limit=15)
        triples  = _extract_triples(full_text, concepts)

        with self._connect() as conn:
            # ── Document 노드  (점: 명사 — 파일) ────────────────────────────────
            self._upsert_node(
                conn, file_id, "Document", filename,
                summary=(text or filename)[:500],
                metadata=metadata, raw=metadata,
            )
            self._ingest_structure_nodes(conn, file_id, filename, doc_meta)

            # ── Person 노드 + 동사형 엣지 ─────────────────────────────────────
            if uploader:
                person_id = f"person:{_slug(uploader)}"
                self._upsert_node(
                    conn, person_id, "Person", uploader,
                    metadata={"email": uploader},
                )
                # 선: 동사 — Person이 Document를 "업로드함"
                self._upsert_edge(conn, person_id, file_id, "업로드함", weight=1.0)

            # ── Chat 노드와 연결 ──────────────────────────────────────────────
            if conversation_id:
                conv_id = f"conversation:{_slug(conversation_id)}"
                self._upsert_node(conn, conv_id, "Chat", conversation_id)
                # 선: 동사 — Chat이 Document를 "언급함"
                self._upsert_edge(conn, conv_id, file_id, "언급함", weight=0.8)

            # ── RAG chunks (검색용, 그래프 비표시) ────────────────────────────
            for index, chunk in enumerate(_chunks(text)):
                chunk_id = f"chunk:{_sha256_text(f'{file_id}:{index}:{chunk}')[:24]}"
                self._upsert_node(
                    conn, chunk_id, "Chunk",
                    f"{filename} chunk {index + 1}",
                    summary=chunk[:500],
                    metadata={"index": index, "source_node": file_id},
                )
                conn.execute(
                    "INSERT OR REPLACE INTO chunks(id, source_node, text, metadata_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, file_id, chunk,
                     _json({"index": index, "source_node": file_id}), _now()),
                )
                self._upsert_edge(conn, file_id, chunk_id, "포함함")

            # ── Concept / Feature / Error / Code 노드 + 동사형 엣지 ───────────
            concept_ids: Dict[str, str] = {}
            for concept in concepts:
                node_t = _classify_node_type(concept, full_text)
                cid = f"{node_t.lower()}:{_slug(concept)}"
                concept_ids[concept.lower()] = cid
                self._upsert_node(
                    conn, cid, node_t, concept,
                    metadata={"auto_extracted": True, "source_file": filename},
                )
                # 선: 동사 — Document가 Concept을 "포함함"
                self._upsert_edge(conn, file_id, cid, "포함함", weight=0.8)

            # ── Concept–Concept 엣지  (선: 동사형) ───────────────────────────
            for triple in triples:
                subj_id = concept_ids.get(triple["subject"].lower())
                obj_id  = concept_ids.get(triple["object"].lower())
                if subj_id and obj_id and subj_id != obj_id:
                    self._upsert_edge(
                        conn, subj_id, obj_id,
                        triple["relation"],
                        weight=1.0,
                        metadata={"context": triple.get("context", "")[:240]},
                    )

            # ── Task / Decision 노드 ──────────────────────────────────────────
            for item in _semantic_items(text):
                sem_type  = item["type"]
                sem_title = item["title"]
                sem_id = f"{sem_type.lower()}:{_sha256_text(f'{file_id}:{sem_type}:{sem_title}')[:24]}"
                self._upsert_node(
                    conn, sem_id, sem_type, sem_title,
                    summary=item["summary"],
                    metadata={"auto_extracted": True, "source_node": file_id, "filename": filename},
                    raw=item,
                )
                # 선: Document가 Task/Decision을 "포함함"
                self._upsert_edge(conn, file_id, sem_id, "포함함", weight=0.9)

        return {"node_id": file_id, "sha256": digest, "metadata": metadata}

    def ingest_event(
        self,
        event_type: str,
        title: str,
        *,
        user_email: Optional[str] = None,
        user_nickname: Optional[str] = None,
        source: Optional[str] = None,
        conversation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event_type = str(event_type or "Event")
        title = str(title or event_type)
        payload = {
            "event_type": event_type,
            "title": title,
            "user_email": user_email,
            "user_nickname": user_nickname,
            "source": source,
            "conversation_id": conversation_id,
            "metadata": metadata or {},
            "timestamp": _now(),
        }
        event_id = f"event:{_sha256_text(_json(payload))[:24]}"
        conv_id = f"conversation:{_slug(conversation_id or 'default')}"
        with self._connect() as conn:
            self._upsert_node(conn, event_id, event_type, title, summary=title, metadata=payload, raw=payload)
            self._upsert_node(conn, conv_id, "Conversation", conversation_id or "Default conversation", metadata={"source": source})
            self._upsert_edge(conn, conv_id, event_id, "has_event", metadata={"source": source})
            if user_email or user_nickname:
                person_key = user_email or user_nickname or "unknown"
                person_id = f"person:{_slug(person_key)}"
                self._upsert_node(conn, person_id, "Person", user_nickname or user_email or "Unknown user", metadata={"email": user_email})
                self._upsert_edge(conn, person_id, event_id, "triggered", metadata={"event_type": event_type})
        return {"node_id": event_id, "type": event_type}

    def _ingest_structure_nodes(
        self,
        conn: sqlite3.Connection,
        file_id: str,
        filename: str,
        structure: Dict[str, Any],
    ) -> None:
        for slide in structure.get("slides") or []:
            index = slide.get("index")
            slide_id = f"slide:{_sha256_text(f'{file_id}:slide:{index}')[:24]}"
            title = f"{filename} slide {index}"
            summary = "\n".join(slide.get("texts") or [])[:800]
            self._upsert_node(conn, slide_id, "Slide", title, summary=summary, metadata=slide)
            self._upsert_edge(conn, file_id, slide_id, "has_slide")
            for text in slide.get("texts") or []:
                for topic in _topic_candidates(text, limit=4):
                    topic_id = f"topic:{_slug(topic)}"
                    self._upsert_node(conn, topic_id, "Topic", topic, metadata={"auto_extracted": True})
                    self._upsert_edge(conn, slide_id, topic_id, "discusses", weight=0.6)

        for page in structure.get("pages") or []:
            index = page.get("index")
            page_id = f"page:{_sha256_text(f'{file_id}:page:{index}')[:24]}"
            title = f"{filename} page {index}"
            self._upsert_node(conn, page_id, "Page", title, summary=page.get("preview") or "", metadata=page)
            self._upsert_edge(conn, file_id, page_id, "has_page")
            for topic in _topic_candidates(page.get("preview") or "", limit=4):
                topic_id = f"topic:{_slug(topic)}"
                self._upsert_node(conn, topic_id, "Topic", topic, metadata={"auto_extracted": True})
                self._upsert_edge(conn, page_id, topic_id, "discusses", weight=0.6)

        for sheet in (structure.get("sheets") or []):
            sheet_title = sheet.get("title")
            sheet_id = f"sheet:{_sha256_text(f'{file_id}:sheet:{sheet_title}')[:24]}"
            self._upsert_node(conn, sheet_id, "Sheet", f"{filename} / {sheet_title}", metadata=sheet)
            self._upsert_edge(conn, file_id, sheet_id, "has_sheet")

        for image in (structure.get("images") or []):
            image_key = image.get("sha256") or _sha256_text(json.dumps(image, ensure_ascii=False, sort_keys=True))
            image_id = f"image:{str(image_key)[:24]}"
            title_parts = [filename, "image"]
            if image.get("page"):
                title_parts.append(f"page {image.get('page')}")
            if image.get("name"):
                title_parts.append(str(image.get("name")).split("/")[-1])
            self._upsert_node(conn, image_id, "Image", " / ".join(title_parts), metadata=image)
            self._upsert_edge(conn, file_id, image_id, "contains_image")

    def _document_structure(self, path: Path, ext: str) -> Dict[str, Any]:
        try:
            if ext == ".pptx":
                return self._pptx_structure(path)
            if ext == ".pdf":
                return self._pdf_structure(path)
            if ext == ".docx":
                return self._docx_structure(path)
            if ext == ".xlsx":
                return self._xlsx_structure(path)
        except Exception as exc:
            return {"error": str(exc)}
        return {}

    def _pptx_structure(self, path: Path) -> Dict[str, Any]:
        result: Dict[str, Any] = {"slides": [], "images": []}
        try:
            from PIL import Image
            from pptx import Presentation
            prs = Presentation(str(path))
            for slide_index, slide in enumerate(prs.slides, start=1):
                slide_info = {"index": slide_index, "shapes": [], "texts": []}
                for shape_index, shape in enumerate(slide.shapes, start=1):
                    shape_info = {
                        "index": shape_index,
                        "name": getattr(shape, "name", ""),
                        "shape_type": str(getattr(shape, "shape_type", "")),
                        "bbox": {
                            "left": int(getattr(shape, "left", 0) or 0),
                            "top": int(getattr(shape, "top", 0) or 0),
                            "width": int(getattr(shape, "width", 0) or 0),
                            "height": int(getattr(shape, "height", 0) or 0),
                        },
                    }
                    if getattr(shape, "has_text_frame", False):
                        text = shape.text_frame.text.strip()
                        if text:
                            shape_info["text"] = text[:1000]
                            slide_info["texts"].append(text)
                    slide_info["shapes"].append(shape_info)
                result["slides"].append(slide_info)
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if not name.startswith("ppt/media/"):
                        continue
                    data = zf.read(name)
                    image_info: Dict[str, Any] = {
                        "name": name,
                        "bytes": len(data),
                        "sha256": _sha256_bytes(data),
                    }
                    try:
                        from io import BytesIO
                        with Image.open(BytesIO(data)) as img:
                            image_info.update({"width": img.width, "height": img.height, "format": img.format})
                    except Exception:
                        pass
                    result["images"].append(image_info)
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def _pdf_structure(self, path: Path) -> Dict[str, Any]:
        result: Dict[str, Any] = {"pages": [], "images": []}
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                metadata = dict(pdf.metadata or {})
                result["metadata"] = {str(k): str(v) for k, v in metadata.items()}
                for page_index, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    page_info = {
                        "index": page_index,
                        "width": float(page.width or 0),
                        "height": float(page.height or 0),
                        "chars": len(text),
                        "preview": _clean_text(text)[:500],
                        "image_count": len(page.images or []),
                    }
                    result["pages"].append(page_info)
                    for image_index, image in enumerate(page.images or [], start=1):
                        result["images"].append({
                            "page": page_index,
                            "index": image_index,
                            "name": image.get("name"),
                            "width": image.get("width"),
                            "height": image.get("height"),
                            "bbox": {
                                "x0": image.get("x0"),
                                "top": image.get("top"),
                                "x1": image.get("x1"),
                                "bottom": image.get("bottom"),
                            },
                        })
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def _docx_structure(self, path: Path) -> Dict[str, Any]:
        from docx import Document
        doc = Document(str(path))
        headings = []
        paragraphs = 0
        for p in doc.paragraphs:
            text = p.text.strip()
            if not text:
                continue
            paragraphs += 1
            style = getattr(p.style, "name", "")
            if style.lower().startswith("heading"):
                headings.append({"style": style, "text": text[:240]})
        return {"paragraphs": paragraphs, "headings": headings[:80], "tables": len(doc.tables)}

    def _xlsx_structure(self, path: Path) -> Dict[str, Any]:
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        sheets = []
        for ws in wb.worksheets:
            sheets.append({"title": ws.title, "max_row": ws.max_row, "max_column": ws.max_column})
        return {"sheets": sheets}

    # ── 그래프에 표시되는 노드 타입  (점 = 명사) ──────────────────────────────
    # Message / AIResponse / Chunk 는 RAG 검색용으로만 저장, 그래프에서 숨김.
    _GRAPH_VISIBLE_TYPES = (
        "Computer",   # 내 컴퓨터
        "Drive",      # 드라이브 / 볼륨
        "Folder",     # 폴더
        "File",       # 일반 파일
        "Chat",       # 대화 세션
        "Document",   # 파일 (PDF·PPT·Word·Excel·이미지)
        "CodeFile",   # 코드 파일
        "Spreadsheet",# 엑셀/CSV
        "SlideDeck",  # 프레젠테이션
        "Image",      # 이미지
        "ImageText",  # OCR 텍스트
        "Concept",    # 개념 / 아이디어 / 기술 용어
        "Person",     # 사람
        "Error",      # 오류 / 버그
        "Code",       # 코드 / 함수
        "Feature",    # 소프트웨어 기능
        "Task",       # 할 일
        "Decision",   # 결정 사항
    )

    def graph(self, limit: int = 300) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 300), 2000))
        visible = ",".join(f"'{t}'" for t in self._GRAPH_VISIBLE_TYPES)
        with self._connect() as conn:
            nodes = [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in conn.execute(
                    f"SELECT id, type, title, summary, metadata_json, updated_at FROM nodes WHERE type IN ({visible}) ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
            ]
            node_ids = {node["id"] for node in nodes}
            edges: List[Dict[str, Any]] = []
            if node_ids:
                edge_rows = conn.execute(
                    f"""
                    SELECT id, from_node, to_node, type, weight, metadata_json
                    FROM edges
                    WHERE from_node IN (
                        SELECT id FROM nodes WHERE type IN ({visible})
                        ORDER BY updated_at DESC LIMIT ?
                    )
                    AND to_node IN (
                        SELECT id FROM nodes WHERE type IN ({visible})
                        ORDER BY updated_at DESC LIMIT ?
                    )
                    ORDER BY weight DESC, created_at DESC
                    """,
                    (limit, limit),
                ).fetchall()
                edges = [
                    {
                        "id": row["id"],
                        "from": row["from_node"],
                        "to": row["to_node"],
                        "type": row["type"],
                        "weight": row["weight"],
                        "metadata": _safe_loads(row["metadata_json"]),
                    }
                    for row in edge_rows
                ]

        degree_map: Dict[str, int] = {}
        now = datetime.now()
        node_by_id = {node["id"]: node for node in nodes}
        topic_metrics: Dict[str, Dict[str, Any]] = {}

        for edge in edges:
            degree_map[edge["from"]] = degree_map.get(edge["from"], 0) + 1
            degree_map[edge["to"]] = degree_map.get(edge["to"], 0) + 1
            from_node = node_by_id.get(edge["from"])
            to_node = node_by_id.get(edge["to"])
            if not from_node or not to_node:
                continue
            for topic_node, other_node in ((from_node, to_node), (to_node, from_node)):
                if topic_node["type"] != "Topic":
                    continue
                metrics = topic_metrics.setdefault(topic_node["id"], {
                    "mention_count": 0.0,
                    "conversation_ids": set(),
                })
                if edge["type"] in {"mentions", "discusses"}:
                    metrics["mention_count"] += max(0.5, float(edge.get("weight") or 1.0))
                other_meta = other_node.get("metadata") or {}
                conversation_id = other_meta.get("conversation_id")
                if other_node["type"] == "Conversation":
                    conversation_id = other_node["id"]
                if conversation_id:
                    metrics["conversation_ids"].add(str(conversation_id))

        type_max_raw: Dict[str, float] = {}
        for node in nodes:
            degree = degree_map.get(node["id"], 0)
            recency = _recency_score(node.get("updated_at"), now=now)
            metrics = {
                "degree": degree,
                "recency_score": round(recency, 4),
            }
            if node["type"] == "Topic":
                topic_stat = topic_metrics.get(node["id"], {})
                mention_count = float(topic_stat.get("mention_count") or 0.0)
                conversation_count = len(topic_stat.get("conversation_ids") or ())
                raw_importance = (
                    math.log1p(mention_count) * 2.8
                    + math.log1p(conversation_count) * 2.2
                    + recency * 1.4
                    + math.sqrt(max(0, degree)) * 0.45
                )
                metrics.update({
                    "mention_count": round(mention_count, 2),
                    "conversation_count": conversation_count,
                })
            else:
                raw_importance = math.log1p(max(0, degree)) * 1.4 + recency * 0.9

            metrics["importance_raw"] = round(raw_importance, 4)
            node["importance"] = round(raw_importance, 4)
            node["_raw_importance"] = raw_importance
            node["metadata"] = {**(node.get("metadata") or {}), "graph_metrics": metrics}
            type_max_raw[node["type"]] = max(type_max_raw.get(node["type"], 0.0), raw_importance)

        for node in nodes:
            max_raw = max(type_max_raw.get(node["type"], 0.0), 0.0001)
            importance_norm = min(1.0, (node.get("_raw_importance") or 0.0) / max_raw)
            node["importance_norm"] = round(importance_norm, 4)
            node["metadata"]["graph_metrics"]["importance_norm"] = node["importance_norm"]
            node.pop("_raw_importance", None)
        return {"nodes": nodes, "edges": edges}

    def search(self, query: str, limit: int = 30) -> Dict[str, Any]:
        query = str(query or "").strip()
        q = f"%{query}%"
        limit = max(1, min(int(limit or 30), 100))
        with self._connect() as conn:
            rows = []
            if query:
                rows = conn.execute(
                    """
                    SELECT id, type, title, summary, metadata_json, updated_at
                    FROM nodes
                    WHERE title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (q, q, q, limit),
                ).fetchall()

            if len(rows) < limit:
                terms = _topic_candidates(query, limit=8)
                if terms:
                    clauses = []
                    params: List[str] = []
                    for term in terms:
                        clauses.append("(title LIKE ? OR summary LIKE ? OR metadata_json LIKE ?)")
                        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
                    extra = conn.execute(
                        f"""
                        SELECT id, type, title, summary, metadata_json, updated_at
                        FROM nodes
                        WHERE {' OR '.join(clauses)}
                        ORDER BY updated_at DESC
                        LIMIT ?
                        """,
                        (*params, limit * 3),
                    ).fetchall()
                    by_id = {row["id"]: row for row in rows}
                    for row in extra:
                        by_id.setdefault(row["id"], row)
                    rows = list(by_id.values())

            terms_for_score = set(_topic_candidates(query, limit=12))
            def score(row: sqlite3.Row) -> tuple:
                haystack = f"{row['title']} {row['summary']} {row['metadata_json']}".lower()
                hits = sum(1 for term in terms_for_score if term.lower() in haystack)
                type_boost = 1 if row["type"] in {
                    "Decision", "Task", "File", "Document", "CodeFile",
                    "Spreadsheet", "SlideDeck", "Image", "ImageText", "Page", "Slide",
                } else 0
                return (hits, type_boost, row["updated_at"] or "")

            rows = sorted(rows, key=score, reverse=True)[:limit]
        return {
            "query": query,
            "matches": [
                {
                    "id": row["id"],
                    "type": row["type"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "metadata": _safe_loads(row["metadata_json"]),
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ],
        }

    def context_for_query(self, query: str, limit: int = 6) -> str:
        """Return compact graph-backed RAG context for chat generation."""
        query = str(query or "").strip()
        if not query:
            return ""
        matches = self.search(query, limit).get("matches", [])
        if not matches:
            topics = _topic_candidates(query, limit=4)
            if topics:
                with self._connect() as conn:
                    rows = []
                    for topic in topics:
                        rows.extend(conn.execute(
                            """
                            SELECT id, type, title, summary, metadata_json
                            FROM nodes
                            WHERE title LIKE ? OR metadata_json LIKE ?
                            ORDER BY updated_at DESC
                            LIMIT 3
                            """,
                            (f"%{topic}%", f"%{topic}%"),
                        ).fetchall())
                seen = set()
                matches = []
                for row in rows:
                    if row["id"] in seen:
                        continue
                    seen.add(row["id"])
                    matches.append({
                        "id": row["id"],
                        "type": row["type"],
                        "title": row["title"],
                        "summary": row["summary"],
                        "metadata": _safe_loads(row["metadata_json"]),
                    })
                    if len(matches) >= limit:
                        break
        lines = []
        for match in matches[:limit]:
            meta = match.get("metadata") or {}
            source = (
                meta.get("relative_path")
                or meta.get("filename")
                or meta.get("conversation_id")
                or meta.get("source")
                or match["id"]
            )
            summary = _clean_text(match.get("summary") or "")[:700]
            lines.append(f"- [{match['type']}] {match['title']} | source={source} | {summary}")
        return "\n".join(lines)

    def neighbors(self, node_id: str) -> Dict[str, Any]:
        """Return direct neighbors (1-hop) of a node."""
        with self._connect() as conn:
            edge_rows = conn.execute(
                "SELECT from_node, to_node, type, weight FROM edges WHERE from_node=? OR to_node=?",
                (node_id, node_id),
            ).fetchall()
            neighbor_ids: set = set()
            edges = []
            for row in edge_rows:
                neighbor_ids.add(row["from_node"])
                neighbor_ids.add(row["to_node"])
                edges.append({"from": row["from_node"], "to": row["to_node"], "type": row["type"], "weight": row["weight"]})
            neighbor_ids.discard(node_id)
            nodes = []
            if neighbor_ids:
                placeholders = ",".join("?" * len(neighbor_ids))
                nodes = [
                    {
                        "id": row["id"],
                        "type": row["type"],
                        "title": row["title"],
                        "summary": row["summary"],
                        "metadata": _safe_loads(row["metadata_json"]),
                    }
                    for row in conn.execute(
                        f"SELECT id, type, title, summary, metadata_json FROM nodes WHERE id IN ({placeholders})",
                        list(neighbor_ids),
                    )
                ]
        return {"node_id": node_id, "neighbors": nodes, "edges": edges}

    def delete_conversation(self, conversation_id: str) -> Dict[str, Any]:
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            return {"status": "skipped", "removed_nodes": 0}
        conv_id = f"conversation:{_slug(conversation_id)}"
        with self._connect() as conn:
            direct_ids = [
                row["to_node"]
                for row in conn.execute(
                    "SELECT to_node FROM edges WHERE from_node=? AND type='contains'",
                    (conv_id,),
                )
            ]
            remove_ids = set(direct_ids)
            for source_id in list(direct_ids):
                for row in conn.execute(
                    """
                    SELECT to_node FROM edges
                    WHERE from_node=? AND type IN ('has_chunk', 'implies', 'contains_signal', 'has_page', 'has_slide', 'has_sheet', 'contains_image')
                    """,
                    (source_id,),
                ):
                    remove_ids.add(row["to_node"])
            remove_ids.add(conv_id)
            for node_id in remove_ids:
                conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
            conn.execute(
                """
                DELETE FROM nodes
                WHERE type='Topic'
                  AND id NOT IN (SELECT to_node FROM edges)
                  AND id NOT IN (SELECT from_node FROM edges)
                """
            )
        return {"status": "ok", "conversation_id": conversation_id, "removed_nodes": len(remove_ids)}

    def clear_all(self) -> Dict[str, Any]:
        with self._connect() as conn:
            counts = {
                "nodes": conn.execute("SELECT COUNT(*) AS c FROM nodes").fetchone()["c"],
                "edges": conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"],
                "chunks": conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"],
                "knowledge_sources": conn.execute("SELECT COUNT(*) AS c FROM knowledge_sources").fetchone()["c"],
                "local_file_index": conn.execute("SELECT COUNT(*) AS c FROM local_file_index").fetchone()["c"],
            }
            conn.execute("DELETE FROM local_file_index")
            conn.execute("DELETE FROM knowledge_sources")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM nodes")
        if self.blob_dir.exists():
            shutil.rmtree(self.blob_dir, ignore_errors=True)
            self.blob_dir.mkdir(parents=True, exist_ok=True)
        return {"status": "ok", "removed": counts}

    def stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            node_counts = {
                row["type"]: row["count"]
                for row in conn.execute("SELECT type, COUNT(*) AS count FROM nodes GROUP BY type")
            }
            edge_counts = {
                row["type"]: row["count"]
                for row in conn.execute("SELECT type, COUNT(*) AS count FROM edges GROUP BY type")
            }
            local_sources = conn.execute("SELECT COUNT(*) AS c FROM knowledge_sources").fetchone()["c"]
            local_file_status = {
                row["status"]: row["count"]
                for row in conn.execute("SELECT status, COUNT(*) AS count FROM local_file_index GROUP BY status")
            }
        v2 = None
        if KGStoreV2 is not None:
            try:
                v2 = KGStoreV2(self.db_path).stats()
            except Exception as e:
                v2 = {"available": False, "error": str(e)}
        return {
            "db_path": str(self.db_path),
            "schema_version": GRAPH_SCHEMA_VERSION,
            "v2_schema_available": KGStoreV2 is not None,
            "nodes": node_counts,
            "edges": edge_counts,
            "local_sources": local_sources,
            "local_file_status": local_file_status,
            "v2": v2,
        }
