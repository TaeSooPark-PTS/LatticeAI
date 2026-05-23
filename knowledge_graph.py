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
import re
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


GRAPH_SCHEMA_VERSION = 1


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

    # De-duplicate: remove shorter concepts that are sub-strings of longer ones
    # e.g. if "Lattice AI" exists, drop "Lattice"; if "Graph RAG" exists, drop "Graph"
    final: List[str] = []
    values = list(seen.values())
    values_lower = [v.lower() for v in values]
    for i, v in enumerate(values):
        vl = v.lower()
        is_substring = any(
            vl != values_lower[j] and vl in values_lower[j]
            for j in range(len(values))
        )
        if not is_substring:
            final.append(v)

    return final[:limit]


def _infer_relation(sentence: str) -> str:
    """Infer a human-readable relation label from a sentence."""
    s = sentence.lower()
    if re.search(r'비교|versus|vs\.?|차이|다르', s):
        return "비교"
    if re.search(r'기능|feature|할 수 있|지원|support|제공|provide', s):
        return "기능 제공"
    if re.search(r'포함|include|consist|구성|구현|탑재', s):
        return "포함"
    if re.search(r'사용|use|활용|이용', s):
        return "사용"
    if re.search(r'연결|connect|통합|integrate|연동', s):
        return "연결"
    if re.search(r'설명|explain|describe|정의|definition|란|이란', s):
        return "설명"
    if re.search(r'확장|extend|플러그인|plugin|addon', s):
        return "확장"
    if re.search(r'대체|replace|instead|instead of', s):
        return "대체"
    if re.search(r'필요|require|depend|의존', s):
        return "의존"
    if re.search(r'생성|만들|create|generate|build', s):
        return "생성"
    return "관련"


def _extract_triples(
    text: str, concepts: List[str], limit: int = 16
) -> List[Dict[str, str]]:
    """Extract (subject, relation, object, context) triples from text.

    Scans each sentence for pairs of concepts and infers the relation from
    surrounding verb/particle patterns.
    """
    if len(concepts) < 2:
        return []

    # Build a fast lookup: lowercased concept → canonical form
    concept_lower = {c.lower(): c for c in concepts}

    triples: List[Dict[str, str]] = []
    seen_pairs: set = set()

    sentences = re.split(r'(?<=[.!?\n])\s+|\n{2,}', text)
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 8:
            continue
        sent_lower = sent.lower()

        # Find which concepts appear in this sentence
        present = [
            concept_lower[k]
            for k in concept_lower
            if k in sent_lower
        ]
        if len(present) < 2:
            continue

        relation = _infer_relation(sent)
        # Create one triple per adjacent concept pair (avoid combinatorial explosion)
        for i in range(len(present) - 1):
            subj = present[i]
            obj = present[i + 1]
            pair_key = f"{subj.lower()}|{relation}|{obj.lower()}"
            rev_key = f"{obj.lower()}|{relation}|{subj.lower()}"
            if pair_key in seen_pairs or rev_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            triples.append({
                "subject": subj,
                "relation": relation,
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
                CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
                CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node);
                CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node);
                CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_node);
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO graph_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(GRAPH_SCHEMA_VERSION)),
            )

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
        triples = _extract_triples(content, concepts)
        semantic = _semantic_items(content)

        with self._connect() as conn:
            # ── Conversation node (always kept, acts as context anchor) ──────
            self._upsert_node(
                conn, conv_id, "Conversation",
                conversation_id or "Default conversation",
                metadata={"source": source},
            )

            # ── Message/AIResponse node — stored for RAG/search but hidden
            #    from the graph visualization (type filtered in graph()) ──────
            self._upsert_node(
                conn, node_id, node_type,
                _clean_text(content)[:80] or role,
                summary=_clean_text(content)[:500],
                metadata=metadata,
                raw=raw or metadata,
            )
            self._upsert_edge(conn, conv_id, node_id, "contains", metadata={"source": source})

            # ── Person node ───────────────────────────────────────────────────
            person_id = None
            if user_email or user_nickname:
                person_key = user_email or user_nickname or "unknown"
                person_id = f"person:{_slug(person_key)}"
                self._upsert_node(
                    conn, person_id, "Person",
                    user_nickname or user_email or "Unknown user",
                    metadata={"email": user_email},
                )
                self._upsert_edge(conn, person_id, node_id, "authored", metadata={"role": role})

            # ── Text chunks (for RAG retrieval, invisible in graph) ───────────
            for index, chunk in enumerate(_chunks(content)):
                chunk_id = f"chunk:{_sha256_text(f'{node_id}:{index}:{chunk}')[:24]}"
                self._upsert_node(
                    conn, chunk_id, "Chunk",
                    f"{node_type} chunk {index + 1}",
                    summary=chunk[:500],
                    metadata={"index": index, "source_node": node_id},
                )
                conn.execute(
                    "INSERT OR REPLACE INTO chunks(id, source_node, text, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, node_id, chunk, _json({"index": index, "source_node": node_id}), _now()),
                )
                self._upsert_edge(conn, node_id, chunk_id, "has_chunk")

            # ── Concept nodes — the PRIMARY visible nodes in the graph ────────
            concept_ids: Dict[str, str] = {}
            for concept in concepts:
                cid = f"concept:{_slug(concept)}"
                concept_ids[concept.lower()] = cid
                self._upsert_node(
                    conn, cid, "Concept", concept,
                    metadata={"auto_extracted": True, "source": source},
                )
                # Conversation → Concept edge (for context lookup)
                self._upsert_edge(conn, conv_id, cid, "discusses", weight=0.6)

            # ── Concept–Concept edges from extracted triples ──────────────────
            for triple in triples:
                subj_id = concept_ids.get(triple["subject"].lower())
                obj_id = concept_ids.get(triple["object"].lower())
                if subj_id and obj_id and subj_id != obj_id:
                    self._upsert_edge(
                        conn, subj_id, obj_id, triple["relation"],
                        weight=0.9,
                        metadata={"context": triple.get("context", "")[:240]},
                    )

            # ── Decision / Task nodes ─────────────────────────────────────────
            for item in semantic:
                sem_type = item["type"]
                sem_title = item["title"]
                sem_id = f"{sem_type.lower()}:{_sha256_text(f'{node_id}:{sem_type}:{sem_title}')[:24]}"
                self._upsert_node(
                    conn, sem_id, sem_type, sem_title,
                    summary=item["summary"],
                    metadata={"auto_extracted": True, "source_node": node_id},
                    raw=item,
                )
                self._upsert_edge(conn, conv_id, sem_id, "produced", weight=0.8)
                # Link Decision/Task to mentioned concepts
                for cid in list(concept_ids.values())[:3]:
                    self._upsert_edge(conn, sem_id, cid, "involves", weight=0.7)

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
        with self._connect() as conn:
            self._upsert_node(conn, file_id, "File", filename, summary=(text or filename)[:500], metadata=metadata, raw=metadata)
            self._ingest_structure_nodes(conn, file_id, filename, doc_meta)
            if uploader:
                person_id = f"person:{_slug(uploader)}"
                self._upsert_node(conn, person_id, "Person", uploader, metadata={"email": uploader})
                self._upsert_edge(conn, person_id, file_id, "uploaded")
            if conversation_id:
                conv_id = f"conversation:{_slug(conversation_id)}"
                self._upsert_node(conn, conv_id, "Conversation", conversation_id)
                self._upsert_edge(conn, conv_id, file_id, "contains")
            for index, chunk in enumerate(_chunks(text)):
                chunk_id = f"chunk:{_sha256_text(f'{file_id}:{index}:{chunk}')[:24]}"
                self._upsert_node(conn, chunk_id, "Chunk", f"{filename} chunk {index + 1}", summary=chunk[:500], metadata={"index": index, "source_node": file_id})
                conn.execute(
                    "INSERT OR REPLACE INTO chunks(id, source_node, text, metadata_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, file_id, chunk, _json({"index": index, "source_node": file_id}), _now()),
                )
                self._upsert_edge(conn, file_id, chunk_id, "has_chunk")
            for topic in _topic_candidates(f"{filename}\n{text}"):
                topic_id = f"topic:{_slug(topic)}"
                self._upsert_node(conn, topic_id, "Topic", topic, metadata={"auto_extracted": True})
                self._upsert_edge(conn, file_id, topic_id, "discusses", weight=0.7)
            for item in _semantic_items(text):
                semantic_type = item["type"]
                semantic_title = item["title"]
                semantic_id = f"{semantic_type.lower()}:{_sha256_text(f'{file_id}:{semantic_type}:{semantic_title}')[:24]}"
                self._upsert_node(
                    conn,
                    semantic_id,
                    semantic_type,
                    semantic_title,
                    summary=item["summary"],
                    metadata={"auto_extracted": True, "source_node": file_id, "filename": filename},
                    raw=item,
                )
                self._upsert_edge(conn, file_id, semantic_id, "contains_signal", weight=0.8)
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

    # Node types visible in the graph visualization.
    # Message / AIResponse / Chunk are stored for RAG but hidden from the graph.
    _GRAPH_VISIBLE_TYPES = (
        "Concept", "Person", "File", "Conversation",
        "Decision", "Task", "Topic",
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
                type_boost = 1 if row["type"] in {"Decision", "Task", "File", "Page", "Slide"} else 0
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
            source = meta.get("filename") or meta.get("conversation_id") or meta.get("source") or match["id"]
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
            }
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
        return {"db_path": str(self.db_path), "nodes": node_counts, "edges": edge_counts}
