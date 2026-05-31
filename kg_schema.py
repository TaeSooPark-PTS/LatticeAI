"""
Lattice AI — Knowledge Graph v2 schema (PPT spec aligned)
=========================================================

명세: ``lattice_ai_full_spec.pptx`` 슬라이드 20~22 (Node / Edge / Data Model)

목적
----
기존 ``knowledge_graph.py`` 의 자유 문자열 노드/엣지 타입을 **명시 enum + Pydantic
모델 + SQLite v2 스키마** 로 정식화한다. embedding · confidence · evidence ·
owner/visibility · createdBy 필드를 1급 시민으로 승격해서, semantic search
(SIMILAR_TO 엣지 추론) 와 multi-tenant 권한 모델의 기반을 만든다.

설계 원칙
---------
1. **기존 코드를 깨지 않는다**: 새 테이블 이름은 ``nodes_v2`` / ``edges_v2``
   로 분리. 기존 ``nodes`` / ``edges`` 와 공존한다. legacy → v2 reprojection 은
   ``knowledge_graph.py`` 의 버전 게이트 백필 한 곳에서만 수행한다.
2. **정규화 + 무손실**: legacy 자유 문자열 타입은 ``NodeType``/``EdgeType``
   superset 으로 정규화해 ``type`` 칼럼에 저장하고, 원본 문자열은 ``legacy_type``
   칼럼에 그대로 보존한다. summary 와 metadata 는 ``attrs._kg`` 패스스루 blob 이
   아니라 전용 ``summary`` 칼럼 / ``attrs``·``metadata`` 칼럼에 1급으로 저장한다.
3. **표준 라이브러리만 사용**: Pydantic 이 없는 환경에서도 dataclass 로
   동작하도록 ``from dataclasses import dataclass`` 를 사용한다.
   타입 검증은 ``validate()`` 메서드에서 수동.
4. **embedding 은 옵셔널이지만 권장**: 차원은 환경 변수
   ``LATTICEAI_EMBED_DIM`` (기본 1024). bytes blob 으로 저장.
5. **정규화 매핑은 명시적**: 한글 동사/legacy 라벨 → 영문 enum 표가 코드 안에
   들어 있어서 어떤 옛 라벨이 어디로 매핑되는지 한눈에 보인다.

사용 예
-------
```python
from kg_schema import (
    KGStoreV2, Node, Edge, NodeType, EdgeType,
)

store = KGStoreV2("/Users/me/.ltcai/kg_v2.db")
store.init_schema()

n1 = Node(
    type=NodeType.FILE,
    label="LatticeAI_기획서.pdf",
    attrs={"mime": "application/pdf", "pageCount": 24, "lang": "ko"},
    owner_id="user_seoljun",
)
n2 = Node(type=NodeType.CONCEPT, label="MCP")
store.upsert_node(n1)
store.upsert_node(n2)

store.upsert_edge(Edge(
    source=n1.id, target=n2.id,
    type=EdgeType.MENTIONS,
    weight=0.82, confidence=0.91,
    evidence=["chunk:01HX7K…#p3", "chunk:01HX7K…#p11"],
    created_by="extractor:llm-gemma-3-12b",
))
```
"""

from __future__ import annotations

import json
import os
import re
import logging
import sqlite3
import struct
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ── Schema version ──────────────────────────────────────────────────────────
KG_SCHEMA_V2_VERSION = 2
EMBED_DIM = int(os.getenv("LATTICEAI_EMBED_DIM", "1024"))


# ── Node / Edge taxonomy (PPT 슬라이드 20·21) ──────────────────────────────
class NodeType(str, Enum):
    """워크스페이스의 모든 ‘명사’.

    PPT 슬라이드 20 카탈로그(상단 그룹)에 더해, ``knowledge_graph.py`` 가 실제로
    써오던 legacy 자유 문자열 타입을 **무손실 superset**(하단 그룹)으로 1급 enum 화
    한다. 덕분에 ``from_legacy`` 정규화가 의미를 잃지 않고(예: ``Computer`` →
    ``COMPUTER``), 알 수 없는/동적(이벤트) 타입만 ``CONCEPT`` 로 폴백한다.
    원본 문자열은 ``nodes_v2.legacy_type`` 에 그대로 보존되므로 정규화는 항상 무손실.
    """
    # PPT 슬라이드 20 정식 카탈로그
    CONVERSATION = "CONVERSATION"   # 대화 세션 전체
    MESSAGE      = "MESSAGE"        # 단일 발화
    FILE         = "FILE"           # 업로드/연결된 파일
    DOCUMENT     = "DOCUMENT"       # 생성/관리되는 문서 (보고서, 계획서 등)
    CHUNK        = "CHUNK"          # 파일의 분할 청크
    CODE_SYMBOL  = "CODE_SYMBOL"    # 함수·클래스·모듈
    CONCEPT      = "CONCEPT"        # 추출된 개념 / 태그
    PERSON       = "PERSON"         # 사용자·협업자
    MODEL        = "MODEL"          # 로컬/원격 LLM
    TOOL         = "TOOL"           # MCP 서버·외부 도구
    PROJECT      = "PROJECT"        # 주제별 작업 공간
    # legacy superset — knowledge_graph.py 가 실제로 생성하던 노드 타입들
    COMPUTER     = "COMPUTER"       # 내 컴퓨터 (로컬 스캔 루트)
    DRIVE        = "DRIVE"          # 드라이브 / 볼륨
    FOLDER       = "FOLDER"         # 폴더
    CODE_FILE    = "CODE_FILE"      # 코드 파일 (.py/.ts 등)
    SPREADSHEET  = "SPREADSHEET"    # 엑셀 / CSV
    SLIDE_DECK   = "SLIDE_DECK"     # 프레젠테이션
    IMAGE        = "IMAGE"          # 이미지 파일
    IMAGE_TEXT   = "IMAGE_TEXT"     # OCR 텍스트
    SLIDE        = "SLIDE"          # 슬라이드 (덱의 한 장)
    PAGE         = "PAGE"           # 페이지 (문서의 한 면)
    SHEET        = "SHEET"          # 시트 (스프레드시트의 한 탭)
    SECTION      = "SECTION"        # 문서 섹션
    CHAT         = "CHAT"           # 대화 세션(채팅 UI)
    AI_RESPONSE  = "AI_RESPONSE"    # 어시스턴트 발화
    TOPIC        = "TOPIC"          # 주제 / 토픽
    FEATURE      = "FEATURE"        # 소프트웨어 기능
    TASK         = "TASK"           # 할 일
    DECISION     = "DECISION"       # 결정 사항
    ERROR        = "ERROR"          # 오류 / 버그
    EVENT        = "EVENT"          # 분석/시스템 이벤트(동적 타입 폴백)

    @classmethod
    def from_legacy(cls, label: str) -> "NodeType":
        """legacy ``knowledge_graph.py`` 의 자유 문자열을 정식 enum 으로 정규화.

        매핑이 없는(동적 이벤트 등) 타입은 ``CONCEPT`` 로 폴백하지만, 호출부는
        원본 문자열을 ``legacy_type`` 칼럼에 별도 보존하므로 정보 손실은 없다.
        """
        m = (label or "").strip().lower()
        return _LEGACY_NODE_MAP.get(m, cls.CONCEPT)


class EdgeType(str, Enum):
    """노드 사이의 ‘방향성 있고 타입이 명시된’ 관계.  PPT 슬라이드 21."""
    CONTAINS      = "CONTAINS"        # FILE → CHUNK
    MENTIONS      = "MENTIONS"        # MESSAGE → CONCEPT
    REFERENCES    = "REFERENCES"      # FILE → FILE / URL
    REPLIES_TO    = "REPLIES_TO"      # MESSAGE → MESSAGE
    AUTHORED_BY   = "AUTHORED_BY"     # FILE → PERSON
    USES          = "USES"            # PROJECT → TOOL / MODEL
    DERIVED_FROM  = "DERIVED_FROM"    # CHUNK → CHUNK (요약 등)
    SIMILAR_TO    = "SIMILAR_TO"      # ANY ↔ ANY (의미 유사도)
    DEPENDS_ON    = "DEPENDS_ON"      # CODE_SYMBOL → CODE_SYMBOL
    TAGGED_AS     = "TAGGED_AS"       # ANY → CONCEPT
    VERSION_OF    = "VERSION_OF"      # FILE → FILE (히스토리)
    GRANTS_ACCESS = "GRANTS_ACCESS"   # PERSON → RESOURCE
    USED_IN       = "USED_IN"         # CONCEPT → DOCUMENT (문서에 활용됨)
    INSPIRED_BY   = "INSPIRED_BY"     # DOCUMENT → DOCUMENT (영감/참조 관계)
    CONTRADICTS   = "CONTRADICTS"     # DOCUMENT ↔ DOCUMENT (상충 관계)
    EVOLVES_FROM  = "EVOLVES_FROM"    # DOCUMENT → DOCUMENT (발전/개정 관계)
    # legacy superset — knowledge_graph.py 가 실제로 생성하던 엣지 타입들
    UPLOADED_BY    = "UPLOADED_BY"    # PERSON → FILE (업로드함)
    WROTE          = "WROTE"          # PERSON → CONVERSATION (작성함)
    HAS_EVENT      = "HAS_EVENT"      # CONVERSATION → EVENT (has_event)
    TRIGGERED      = "TRIGGERED"      # PERSON → EVENT (triggered)
    HAS_SLIDE      = "HAS_SLIDE"      # SLIDE_DECK → SLIDE (has_slide)
    HAS_PAGE       = "HAS_PAGE"       # DOCUMENT → PAGE (has_page)
    HAS_SHEET      = "HAS_SHEET"      # SPREADSHEET → SHEET (has_sheet)
    HAS_CHUNK      = "HAS_CHUNK"      # FILE → CHUNK (has_chunk)
    CONTAINS_IMAGE = "CONTAINS_IMAGE" # FILE → IMAGE (contains_image)
    CONTAINS_SIGNAL = "CONTAINS_SIGNAL"  # NODE → CONCEPT (contains_signal)
    DISCUSSES      = "DISCUSSES"      # SLIDE/PAGE → TOPIC (discusses)
    IMPLIES        = "IMPLIES"        # NODE → NODE (implies)
    RELATED_TO     = "RELATED_TO"     # ANY ↔ ANY (related_to)

    @classmethod
    def from_legacy(cls, label: str) -> "EdgeType":
        """legacy 자유 문자열/한글 동사를 정식 enum 으로 정규화.

        매핑이 없는 동적 타입은 ``MENTIONS`` 로 폴백하지만, 호출부는 원본 문자열을
        ``edges_v2.legacy_type`` 에 보존하므로 정보 손실은 없다.
        """
        m = (label or "").strip().lower()
        return _LEGACY_EDGE_MAP.get(m, cls.MENTIONS)


# legacy(자유 문자열 / 한글 동사) → enum 매핑 표.
# superset 정규화: 알려진 legacy 타입은 1:1 의미 보존 매핑, 미지/동적 타입만 폴백.
_LEGACY_NODE_MAP: Dict[str, NodeType] = {
    "conversation": NodeType.CONVERSATION,
    "chat":         NodeType.CHAT,
    "message":      NodeType.MESSAGE,
    "airesponse":   NodeType.AI_RESPONSE,
    "file":         NodeType.FILE,
    "codefile":     NodeType.CODE_FILE,
    "spreadsheet":  NodeType.SPREADSHEET,
    "slidedeck":    NodeType.SLIDE_DECK,
    "image":        NodeType.IMAGE,
    "imagetext":    NodeType.IMAGE_TEXT,
    "computer":     NodeType.COMPUTER,
    "drive":        NodeType.DRIVE,
    "folder":       NodeType.FOLDER,
    "page":         NodeType.PAGE,
    "sheet":        NodeType.SHEET,
    "slide":        NodeType.SLIDE,
    "section":      NodeType.SECTION,
    "chunk":        NodeType.CHUNK,
    "code":         NodeType.CODE_SYMBOL,
    "concept":      NodeType.CONCEPT,
    "topic":        NodeType.TOPIC,
    "feature":      NodeType.FEATURE,
    "task":         NodeType.TASK,
    "decision":     NodeType.DECISION,
    "error":        NodeType.ERROR,
    "event":        NodeType.EVENT,
    "tag":          NodeType.CONCEPT,
    "person":       NodeType.PERSON,
    "user":         NodeType.PERSON,
    "model":        NodeType.MODEL,
    "tool":         NodeType.TOOL,
    "mcp":          NodeType.TOOL,
    "project":      NodeType.PROJECT,
    "workspace":    NodeType.PROJECT,
    "document":     NodeType.DOCUMENT,
    "report":       NodeType.DOCUMENT,
    "plan":         NodeType.DOCUMENT,
    "proposal":     NodeType.DOCUMENT,
    "보고서":       NodeType.DOCUMENT,
    "계획서":       NodeType.DOCUMENT,
    "기획서":       NodeType.DOCUMENT,
}

_LEGACY_EDGE_MAP: Dict[str, EdgeType] = {
    # 한글 동사 (knowledge_graph.py 의 EDGE_VERB)
    "언급함": EdgeType.MENTIONS,
    "포함함": EdgeType.CONTAINS,
    "해결함": EdgeType.REFERENCES,
    "의존함": EdgeType.DEPENDS_ON,
    "설명함": EdgeType.MENTIONS,
    "비교함": EdgeType.SIMILAR_TO,
    "사용함": EdgeType.USES,
    "연결함": EdgeType.REFERENCES,
    "확장함": EdgeType.DERIVED_FROM,
    "생성함": EdgeType.AUTHORED_BY,
    "작성함": EdgeType.WROTE,
    "업로드함": EdgeType.UPLOADED_BY,
    "대체함": EdgeType.VERSION_OF,
    "지원함": EdgeType.USES,
    "발생함": EdgeType.REFERENCES,
    "관련됨": EdgeType.MENTIONS,
    # 영문 별칭
    "mentions": EdgeType.MENTIONS,
    "contains": EdgeType.CONTAINS,
    "references": EdgeType.REFERENCES,
    "replies_to": EdgeType.REPLIES_TO,
    "authored_by": EdgeType.AUTHORED_BY,
    "uses": EdgeType.USES,
    "derived_from": EdgeType.DERIVED_FROM,
    "similar_to": EdgeType.SIMILAR_TO,
    "depends_on": EdgeType.DEPENDS_ON,
    "tagged_as": EdgeType.TAGGED_AS,
    "version_of": EdgeType.VERSION_OF,
    "grants_access": EdgeType.GRANTS_ACCESS,
    "used_in":       EdgeType.USED_IN,
    "inspired_by":   EdgeType.INSPIRED_BY,
    "contradicts":   EdgeType.CONTRADICTS,
    "evolves_from":  EdgeType.EVOLVES_FROM,
    # legacy superset 별칭 (knowledge_graph.py 가 실제로 쓰던 엣지 타입)
    "uploaded_by":     EdgeType.UPLOADED_BY,
    "wrote":           EdgeType.WROTE,
    "has_event":       EdgeType.HAS_EVENT,
    "triggered":       EdgeType.TRIGGERED,
    "has_slide":       EdgeType.HAS_SLIDE,
    "has_page":        EdgeType.HAS_PAGE,
    "has_sheet":       EdgeType.HAS_SHEET,
    "has_chunk":       EdgeType.HAS_CHUNK,
    "contains_image":  EdgeType.CONTAINS_IMAGE,
    "contains_signal": EdgeType.CONTAINS_SIGNAL,
    "discusses":       EdgeType.DISCUSSES,
    "implies":         EdgeType.IMPLIES,
    "related_to":      EdgeType.RELATED_TO,
    "활용됨":        EdgeType.USED_IN,
    "영감받음":      EdgeType.INSPIRED_BY,
    "상충함":        EdgeType.CONTRADICTS,
    "발전함":        EdgeType.EVOLVES_FROM,
}

# 노드 타입별로 허용되는 source / target 조합 (PPT 카탈로그 그대로)
# None == 모든 타입 허용
EDGE_ENDPOINT_RULES: Dict[EdgeType, Tuple[Optional[Sequence[NodeType]], Optional[Sequence[NodeType]]]] = {
    EdgeType.CONTAINS:      ((NodeType.FILE, NodeType.DOCUMENT),
                             (NodeType.CHUNK,)),
    EdgeType.MENTIONS:      ((NodeType.MESSAGE, NodeType.FILE, NodeType.CHUNK, NodeType.DOCUMENT),
                             (NodeType.CONCEPT, NodeType.PERSON, NodeType.MODEL, NodeType.TOOL)),
    EdgeType.REFERENCES:    ((NodeType.FILE, NodeType.MESSAGE, NodeType.CHUNK),
                             (NodeType.FILE, NodeType.MESSAGE, NodeType.CHUNK)),
    EdgeType.REPLIES_TO:    ((NodeType.MESSAGE,),         (NodeType.MESSAGE,)),
    EdgeType.AUTHORED_BY:   ((NodeType.FILE, NodeType.MESSAGE, NodeType.CONVERSATION, NodeType.DOCUMENT),
                             (NodeType.PERSON,)),
    EdgeType.USES:          ((NodeType.PROJECT, NodeType.CONVERSATION),
                             (NodeType.TOOL, NodeType.MODEL)),
    EdgeType.DERIVED_FROM:  ((NodeType.CHUNK, NodeType.FILE),
                             (NodeType.CHUNK, NodeType.FILE)),
    EdgeType.SIMILAR_TO:    (None, None),
    EdgeType.DEPENDS_ON:    ((NodeType.CODE_SYMBOL,), (NodeType.CODE_SYMBOL,)),
    EdgeType.TAGGED_AS:     (None, (NodeType.CONCEPT,)),
    EdgeType.VERSION_OF:    ((NodeType.FILE,), (NodeType.FILE,)),
    EdgeType.GRANTS_ACCESS: ((NodeType.PERSON,),
                             (NodeType.FILE, NodeType.CONVERSATION, NodeType.PROJECT)),
    EdgeType.USED_IN:       ((NodeType.CONCEPT,),
                             (NodeType.DOCUMENT, NodeType.FILE)),
    EdgeType.INSPIRED_BY:   ((NodeType.DOCUMENT, NodeType.FILE),
                             (NodeType.DOCUMENT, NodeType.FILE)),
    EdgeType.CONTRADICTS:   ((NodeType.DOCUMENT, NodeType.FILE),
                             (NodeType.DOCUMENT, NodeType.FILE)),
    EdgeType.EVOLVES_FROM:  ((NodeType.DOCUMENT, NodeType.FILE),
                             (NodeType.DOCUMENT, NodeType.FILE)),
}


# ── Models ──────────────────────────────────────────────────────────────────
class Visibility(str, Enum):
    PRIVATE = "private"        # 소유자만
    INTERNAL = "internal"      # 같은 조직
    SHARED  = "shared"         # 명시 공유
    PUBLIC  = "public"         # 누구나


def _ulid() -> str:
    """간이 ULID (timestamp + uuid4 base32). 외부 의존성 없이."""
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().int & ((1 << 80) - 1)
    encoded = (ts << 80) | rand
    chars = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford
    out: List[str] = []
    for _ in range(26):
        encoded, r = divmod(encoded, 32)
        out.append(chars[r])
    return "".join(reversed(out))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def encode_embedding(vec: Sequence[float]) -> Optional[bytes]:
    """list[float] → SQLite BLOB. ``None`` 입력은 None 반환."""
    if vec is None:
        return None
    if len(vec) != EMBED_DIM:
        raise ValueError(
            f"embedding dim mismatch: got {len(vec)}, expected {EMBED_DIM} "
            f"(set LATTICEAI_EMBED_DIM to override)"
        )
    return struct.pack(f"<{EMBED_DIM}f", *vec)


def decode_embedding(blob: Optional[bytes]) -> Optional[List[float]]:
    if not blob:
        return None
    return list(struct.unpack(f"<{EMBED_DIM}f", blob))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """단순 코사인 유사도. numpy 없이."""
    if not a or not b:
        return 0.0
    s = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return s / (na * nb) if na and nb else 0.0


@dataclass
class Node:
    """PPT 슬라이드 20 의 노드 정의.

    ``summary`` 와 ``legacy_type`` 은 1급 칼럼이다. (이전엔 summary·metadata 를
    ``attrs._kg`` 패스스루 blob 에 숨겨 넣었지만 정규화 스키마에서는 ``summary`` 는
    전용 칼럼, metadata 는 ``attrs`` 자체로 직접 보존한다.)
    """
    type: NodeType
    label: str
    id: str = field(default_factory=lambda: f"node:{_ulid()}")
    attrs: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    legacy_type: Optional[str] = None   # 원본 legacy 타입 문자열(무손실 보존)
    embedding: Optional[List[float]] = None
    owner_id: Optional[str] = None
    visibility: Visibility = Visibility.PRIVATE
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    style: Optional[str] = None
    tone: Optional[str] = None
    importance_score: float = 0.0
    last_used: Optional[str] = None

    def validate(self) -> None:
        if not isinstance(self.type, NodeType):
            raise TypeError(f"Node.type must be NodeType, got {type(self.type)!r}")
        if not self.label or not self.label.strip():
            raise ValueError("Node.label is required and non-empty")
        if len(self.label) > 240:
            raise ValueError("Node.label max length is 240 chars")
        if not isinstance(self.attrs, dict):
            raise TypeError("Node.attrs must be a dict")
        if not isinstance(self.visibility, Visibility):
            raise TypeError("Node.visibility must be Visibility enum")
        if self.embedding is not None and len(self.embedding) != EMBED_DIM:
            raise ValueError(f"Node.embedding dim must be {EMBED_DIM}")

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["visibility"] = self.visibility.value
        # embedding 은 JSON 직렬화시 length 만 노출 (가독성)
        if self.embedding is not None:
            d["embedding"] = f"[…{len(self.embedding)} dims]"
        return d


@dataclass
class Edge:
    """PPT 슬라이드 21 의 엣지 정의.

    ``metadata`` 와 ``legacy_type`` 은 1급 칼럼이다. (이전 프로젝션은 엣지
    metadata_json 을 ``evidence`` 칼럼에 우겨넣었지만, 정규화 스키마에서는 전용
    ``metadata`` 칼럼에 보존하고 ``evidence`` 는 본래 의미(근거 ID 목록)로 되돌린다.)
    """
    source: str
    target: str
    type: EdgeType
    id: str = field(default_factory=lambda: f"edge:{_ulid()}")
    weight: float = 1.0           # 강도 0..1
    confidence: float = 1.0       # 추출 신뢰도 0..1
    evidence: List[str] = field(default_factory=list)   # 근거(노드/청크 ID)
    metadata: Dict[str, Any] = field(default_factory=dict)  # legacy metadata_json 보존
    legacy_type: Optional[str] = None   # 원본 legacy 타입 문자열(무손실 보존)
    created_by: str = "user"      # extractor 식별자
    created_at: str = field(default_factory=_now_iso)

    def validate(self) -> None:
        if not isinstance(self.type, EdgeType):
            raise TypeError("Edge.type must be EdgeType")
        if not self.source or not self.target:
            raise ValueError("Edge.source and Edge.target are required")
        if self.source == self.target and self.type is not EdgeType.SIMILAR_TO:
            # SIMILAR_TO 외에는 자기참조 금지
            raise ValueError(f"self-loop not allowed for {self.type.value}")
        if not (0.0 <= self.weight <= 1.0):
            raise ValueError("Edge.weight must be in [0, 1]")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Edge.confidence must be in [0, 1]")

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        return d


def validate_endpoints(edge_type: EdgeType, src_type: NodeType, tgt_type: NodeType) -> None:
    """엣지가 허용된 source/target 타입을 잇고 있는지 검증."""
    rule = EDGE_ENDPOINT_RULES.get(edge_type)
    if rule is None:
        return
    src_allowed, tgt_allowed = rule
    if src_allowed is not None and src_type not in src_allowed:
        raise ValueError(
            f"{edge_type.value}: source must be one of "
            f"{[t.value for t in src_allowed]}, got {src_type.value}"
        )
    if tgt_allowed is not None and tgt_type not in tgt_allowed:
        raise ValueError(
            f"{edge_type.value}: target must be one of "
            f"{[t.value for t in tgt_allowed]}, got {tgt_type.value}"
        )


# ── SQLite v2 store ─────────────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kg_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes_v2 (
  id               TEXT PRIMARY KEY,
  type             TEXT NOT NULL,
  legacy_type      TEXT,
  label            TEXT NOT NULL,
  summary          TEXT NOT NULL DEFAULT '',
  attrs            TEXT NOT NULL DEFAULT '{}',
  embedding        BLOB,
  owner_id         TEXT,
  visibility       TEXT NOT NULL DEFAULT 'private',
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  style            TEXT,
  tone             TEXT,
  importance_score REAL NOT NULL DEFAULT 0.0,
  last_used        TEXT
);

CREATE TABLE IF NOT EXISTS edges_v2 (
  id           TEXT PRIMARY KEY,
  source       TEXT NOT NULL,
  target       TEXT NOT NULL,
  type         TEXT NOT NULL,
  legacy_type  TEXT NOT NULL DEFAULT '',
  weight       REAL NOT NULL DEFAULT 1.0,
  confidence   REAL NOT NULL DEFAULT 1.0,
  evidence     TEXT NOT NULL DEFAULT '[]',
  metadata     TEXT NOT NULL DEFAULT '{}',
  created_by   TEXT NOT NULL DEFAULT 'user',
  created_at   TEXT NOT NULL,
  -- Edge identity follows the *raw* legacy type, not the normalized type:
  -- two distinct legacy types between the same pair (e.g. "mentions" and
  -- "관련됨") must stay distinct edges even though both normalize to MENTIONS.
  UNIQUE(source, target, legacy_type),
  FOREIGN KEY(source) REFERENCES nodes_v2(id) ON DELETE CASCADE,
  FOREIGN KEY(target) REFERENCES nodes_v2(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nodes_v2_type     ON nodes_v2(type);
CREATE INDEX IF NOT EXISTS idx_nodes_v2_legacy   ON nodes_v2(legacy_type);
CREATE INDEX IF NOT EXISTS idx_nodes_v2_owner    ON nodes_v2(owner_id);
CREATE INDEX IF NOT EXISTS idx_edges_v2_source   ON edges_v2(source);
CREATE INDEX IF NOT EXISTS idx_edges_v2_target   ON edges_v2(target);
CREATE INDEX IF NOT EXISTS idx_edges_v2_type     ON edges_v2(type);
CREATE INDEX IF NOT EXISTS idx_edges_v2_legacy   ON edges_v2(legacy_type);
"""


class KGStoreV2:
    """가벼운 SQLite 기반 v2 스토어. sqlite-vec 가 있으면 벡터 인덱스도 활용,
    없으면 Python cosine 으로 폴백."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._has_vec: Optional[bool] = None

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # Columns the current code writes; used to detect schema-evolution drift in
    # v2 tables that an older ``CREATE TABLE IF NOT EXISTS`` left behind.
    _V2_EXPECTED_COLUMNS = {
        "edges_v2": {"id", "source", "target", "type", "legacy_type", "weight",
                     "confidence", "evidence", "metadata", "created_by", "created_at"},
        "nodes_v2": {"id", "type", "legacy_type", "label", "summary", "attrs",
                     "embedding", "owner_id", "visibility", "created_at",
                     "updated_at", "style", "tone", "importance_score", "last_used"},
    }

    def _drop_stale_empty_v2_tables(self, conn: sqlite3.Connection) -> None:
        """Drop v2 tables that predate a schema change — but only when empty.

        ``CREATE TABLE IF NOT EXISTS`` never upgrades an existing table, so a
        v2 table created by an older version keeps its old columns and breaks
        inserts. Recreating is safe precisely because these tables have never
        held data (the v2 read-path isn't wired yet); we refuse to drop any
        table that contains rows.
        """
        # edges_v2 first (it has FKs into nodes_v2)
        for table in ("edges_v2", "nodes_v2"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                continue
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            missing = self._V2_EXPECTED_COLUMNS[table] - cols
            if not missing:
                continue
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count == 0:
                conn.execute(f"DROP TABLE {table}")
            else:
                logging.warning(
                    "kg_schema: %s is missing columns %s but holds %d rows — "
                    "leaving it untouched (manual migration required).",
                    table, sorted(missing), count,
                )

    def init_schema(self) -> None:
        with self._conn() as conn:
            self._drop_stale_empty_v2_tables(conn)
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO kg_meta(key, value) VALUES (?, ?)",
                ("schema_version", str(KG_SCHEMA_V2_VERSION)),
            )
            conn.execute(
                "INSERT OR REPLACE INTO kg_meta(key, value) VALUES (?, ?)",
                ("embed_dim", str(EMBED_DIM)),
            )

    # ── Upsert ───────────────────────────────────────────────
    def upsert_node(self, node: Node) -> str:
        node.validate()
        node.updated_at = _now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO nodes_v2(id, type, legacy_type, label, summary, attrs,
                                     embedding, owner_id, visibility, created_at,
                                     updated_at, style, tone, importance_score, last_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  type=excluded.type,
                  legacy_type=excluded.legacy_type,
                  label=excluded.label,
                  summary=excluded.summary,
                  attrs=excluded.attrs,
                  embedding=COALESCE(excluded.embedding, nodes_v2.embedding),
                  owner_id=excluded.owner_id,
                  visibility=excluded.visibility,
                  updated_at=excluded.updated_at,
                  style=COALESCE(excluded.style, nodes_v2.style),
                  tone=COALESCE(excluded.tone, nodes_v2.tone),
                  importance_score=MAX(excluded.importance_score, nodes_v2.importance_score),
                  last_used=COALESCE(excluded.last_used, nodes_v2.last_used)
                """,
                (
                    node.id, node.type.value, node.legacy_type or node.type.value,
                    node.label, node.summary,
                    json.dumps(node.attrs, ensure_ascii=False),
                    encode_embedding(node.embedding),
                    node.owner_id, node.visibility.value,
                    node.created_at, node.updated_at,
                    node.style, node.tone,
                    float(node.importance_score), node.last_used,
                ),
            )
        return node.id

    def upsert_edge(self, edge: Edge, *, check_endpoints: bool = True) -> str:
        edge.validate()
        if check_endpoints:
            src = self.get_node(edge.source)
            tgt = self.get_node(edge.target)
            if src is None or tgt is None:
                raise ValueError("Edge endpoints must exist as nodes")
            validate_endpoints(edge.type, src.type, tgt.type)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO edges_v2(id, source, target, type, legacy_type, weight,
                                     confidence, evidence, metadata, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, target, legacy_type) DO UPDATE SET
                  type=excluded.type,
                  weight=excluded.weight,
                  confidence=excluded.confidence,
                  evidence=excluded.evidence,
                  metadata=excluded.metadata,
                  created_by=excluded.created_by
                """,
                (
                    edge.id, edge.source, edge.target, edge.type.value,
                    edge.legacy_type or edge.type.value,
                    float(edge.weight), float(edge.confidence),
                    json.dumps(edge.evidence, ensure_ascii=False),
                    json.dumps(edge.metadata, ensure_ascii=False),
                    edge.created_by, edge.created_at,
                ),
            )
        return edge.id

    # ── Read ─────────────────────────────────────────────────
    def get_node(self, node_id: str) -> Optional[Node]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM nodes_v2 WHERE id = ?", (node_id,)
            ).fetchone()
        return _row_to_node(row) if row else None

    def list_nodes(self, *, type: Optional[NodeType] = None,
                   owner_id: Optional[str] = None,
                   limit: int = 100) -> List[Node]:
        sql = "SELECT * FROM nodes_v2 WHERE 1=1"
        args: List[Any] = []
        if type is not None:
            sql += " AND type = ?"
            args.append(type.value)
        if owner_id is not None:
            sql += " AND owner_id = ?"
            args.append(owner_id)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_row_to_node(r) for r in rows]

    def neighbors(self, node_id: str, *,
                  edge_type: Optional[EdgeType] = None,
                  direction: str = "both",
                  limit: int = 50) -> List[Tuple[Edge, Node]]:
        """node_id 에 인접한 (edge, other_node) 페어를 반환."""
        if direction not in ("out", "in", "both"):
            raise ValueError("direction must be 'out' | 'in' | 'both'")
        clauses, args = [], []
        if direction in ("out", "both"):
            clauses.append("source = ?"); args.append(node_id)
        if direction in ("in", "both"):
            clauses.append("target = ?"); args.append(node_id)
        sql = f"SELECT * FROM edges_v2 WHERE ({' OR '.join(clauses)})"
        if edge_type:
            sql += " AND type = ?"; args.append(edge_type.value)
        sql += " ORDER BY weight DESC, confidence DESC LIMIT ?"
        args.append(int(limit))
        with self._conn() as conn:
            edges = [_row_to_edge(r) for r in conn.execute(sql, args).fetchall()]
            out: List[Tuple[Edge, Node]] = []
            for e in edges:
                other_id = e.target if e.source == node_id else e.source
                row = conn.execute(
                    "SELECT * FROM nodes_v2 WHERE id = ?", (other_id,)
                ).fetchone()
                if row:
                    out.append((e, _row_to_node(row)))
        return out

    def search_similar(self, vec: Sequence[float], *,
                       top_k: int = 8,
                       type: Optional[NodeType] = None,
                       owner_id: Optional[str] = None) -> List[Tuple[Node, float]]:
        """코사인 기반 semantic search. sqlite-vec 가 없을 때의 폴백 구현."""
        if len(vec) != EMBED_DIM:
            raise ValueError(f"query embedding dim must be {EMBED_DIM}")
        sql = "SELECT * FROM nodes_v2 WHERE embedding IS NOT NULL"
        args: List[Any] = []
        if type is not None:
            sql += " AND type = ?"; args.append(type.value)
        if owner_id is not None:
            sql += " AND owner_id = ?"; args.append(owner_id)
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        scored = []
        for r in rows:
            emb = decode_embedding(r["embedding"])
            if emb is None:
                continue
            scored.append((_row_to_node(r), cosine(vec, emb)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ── Maintenance ──────────────────────────────────────────
    def stats(self) -> Dict[str, Any]:
        with self._conn() as conn:
            n_nodes = conn.execute("SELECT COUNT(*) FROM nodes_v2").fetchone()[0]
            n_edges = conn.execute("SELECT COUNT(*) FROM edges_v2").fetchone()[0]
            per_type = {
                r["type"]: r["c"]
                for r in conn.execute(
                    "SELECT type, COUNT(*) AS c FROM nodes_v2 GROUP BY type"
                ).fetchall()
            }
            per_edge = {
                r["type"]: r["c"]
                for r in conn.execute(
                    "SELECT type, COUNT(*) AS c FROM edges_v2 GROUP BY type"
                ).fetchall()
            }
        return {
            "schema_version": KG_SCHEMA_V2_VERSION,
            "embed_dim":      EMBED_DIM,
            "nodes":          n_nodes,
            "edges":          n_edges,
            "by_node_type":   per_type,
            "by_edge_type":   per_edge,
        }


# ── Row → model helpers ────────────────────────────────────────────────────
def _row_to_node(row: sqlite3.Row) -> Node:
    keys = row.keys() if hasattr(row, "keys") else []
    return Node(
        id=row["id"],
        type=NodeType(row["type"]),
        legacy_type=row["legacy_type"] if "legacy_type" in keys else None,
        label=row["label"],
        summary=row["summary"] if "summary" in keys else "",
        attrs=json.loads(row["attrs"] or "{}"),
        embedding=decode_embedding(row["embedding"]),
        owner_id=row["owner_id"],
        visibility=Visibility(row["visibility"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        style=row["style"] if "style" in keys else None,
        tone=row["tone"] if "tone" in keys else None,
        importance_score=float(row["importance_score"]) if "importance_score" in keys else 0.0,
        last_used=row["last_used"] if "last_used" in keys else None,
    )


def _row_to_edge(row: sqlite3.Row) -> Edge:
    keys = row.keys() if hasattr(row, "keys") else []
    return Edge(
        id=row["id"],
        source=row["source"],
        target=row["target"],
        type=EdgeType(row["type"]),
        legacy_type=row["legacy_type"] if "legacy_type" in keys else None,
        weight=float(row["weight"]),
        confidence=float(row["confidence"]),
        evidence=json.loads(row["evidence"] or "[]"),
        metadata=json.loads(row["metadata"]) if "metadata" in keys and row["metadata"] else {},
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


# NOTE: legacy → v2 reprojection lives in ``knowledge_graph.py``
# (``KnowledgeGraphStore._backfill_v2_if_needed`` / ``_v2_project_node``/_edge),
# which is the single live, version-gated migration path. The old standalone
# ``migrate_legacy_to_v2()`` helper + CLI ``migrate`` subcommand were removed as
# dead code (no callers); the normalized projection now writes the first-class
# ``legacy_type``/``summary``/``metadata`` columns directly.


# ── CLI ────────────────────────────────────────────────────────────────────
def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(prog="kg_schema",
                                description="Lattice AI KG v2 utilities")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub_init = sub.add_parser("init", help="initialize v2 schema in a DB")
    sub_init.add_argument("db", help="path to sqlite db")

    sub_stats = sub.add_parser("stats", help="print store statistics")
    sub_stats.add_argument("db", help="path to sqlite db")

    args = p.parse_args()
    if args.cmd == "init":
        KGStoreV2(args.db).init_schema()
        print(f"initialized v2 schema in {args.db}")
        return 0
    if args.cmd == "stats":
        print(json.dumps(KGStoreV2(args.db).stats(), indent=2, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
