"""
Lattice AI — Knowledge Graph v2 schema (PPT spec aligned)
=========================================================

명세: ``lattice_ai_full_spec.pptx`` 슬라이드 20~22 (Node / Edge / Data Model)

목적
----
기존 ``knowledge_graph.py`` 의 자유 문자열 노드/엣지 타입을 **명시 enum + SQLite v2
스키마** 로 정식화한다. 이 모듈은 **스키마/초기화/프로젝션 지원** 역할만 담당한다:
``NodeType``/``EdgeType`` taxonomy + legacy 정규화 매핑, ``nodes_v2``/``edges_v2``
DDL(``SCHEMA_SQL``), 그리고 ``KGStoreV2``(스키마 init·heal·stats).

실제 데이터 read/write 는 ``knowledge_graph.py`` 의 ``KnowledgeGraphStore`` 가
legacy 테이블에 대한 dual-write 프로젝션(raw SQL) + ``kgv2_*`` 재구성 뷰로 수행한다.
(과거의 native ``Node``/``Edge`` 모델과 ``KGStoreV2.upsert_*``/``get_node``/
``search_*`` API 는 production 에서 쓰이지 않아 제거되었다.)

설계 원칙
---------
1. **기존 코드를 깨지 않는다**: 새 테이블 이름은 ``nodes_v2`` / ``edges_v2``
   로 분리. 기존 ``nodes`` / ``edges`` 와 공존한다. legacy → v2 reprojection 은
   ``knowledge_graph.py`` 의 버전 게이트 백필 한 곳에서만 수행한다.
2. **정규화 + 무손실**: legacy 자유 문자열 타입은 ``NodeType``/``EdgeType``
   superset 으로 정규화해 ``type`` 칼럼에 저장하고, 원본 문자열은 ``legacy_type``
   칼럼에 그대로 보존한다. summary 와 metadata 는 ``attrs._kg`` 패스스루 blob 이
   아니라 전용 ``summary`` 칼럼 / ``attrs``·``metadata`` 칼럼에 1급으로 저장한다.
3. **표준 라이브러리만 사용**: 외부 의존성 없이 ``sqlite3`` 만으로 동작한다.
4. **정규화 매핑은 명시적**: 한글 동사/legacy 라벨 → 영문 enum 표가 코드 안에
   들어 있어서 어떤 옛 라벨이 어디로 매핑되는지 한눈에 보인다.

사용 예
-------
```python
from kg_schema import KGStoreV2

store = KGStoreV2("/Users/me/.ltcai/kg_v2.db")
store.init_schema()        # nodes_v2 / edges_v2 생성 + 컬럼 drift self-heal
print(store.stats())       # {"nodes": ..., "by_node_type": {...}, ...}
```
"""

from __future__ import annotations

import json
import os
import logging
import sqlite3
from contextlib import contextmanager
from enum import Enum
from typing import Any, Dict, Optional


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
    CONVERSATION = "CONVERSATION"  # 대화 세션 전체
    MESSAGE = "MESSAGE"  # 단일 발화
    FILE = "FILE"  # 업로드/연결된 파일
    DOCUMENT = "DOCUMENT"  # 생성/관리되는 문서 (보고서, 계획서 등)
    CHUNK = "CHUNK"  # 파일의 분할 청크
    CODE_SYMBOL = "CODE_SYMBOL"  # 함수·클래스·모듈
    CONCEPT = "CONCEPT"  # 추출된 개념 / 태그
    PERSON = "PERSON"  # 사용자·협업자
    MODEL = "MODEL"  # 로컬/원격 LLM
    TOOL = "TOOL"  # MCP 서버·외부 도구
    PROJECT = "PROJECT"  # 주제별 작업 공간
    # legacy superset — knowledge_graph.py 가 실제로 생성하던 노드 타입들
    COMPUTER = "COMPUTER"  # 내 컴퓨터 (로컬 스캔 루트)
    DRIVE = "DRIVE"  # 드라이브 / 볼륨
    FOLDER = "FOLDER"  # 폴더
    CODE_FILE = "CODE_FILE"  # 코드 파일 (.py/.ts 등)
    SPREADSHEET = "SPREADSHEET"  # 엑셀 / CSV
    SLIDE_DECK = "SLIDE_DECK"  # 프레젠테이션
    IMAGE = "IMAGE"  # 이미지 파일
    IMAGE_TEXT = "IMAGE_TEXT"  # OCR 텍스트
    SLIDE = "SLIDE"  # 슬라이드 (덱의 한 장)
    PAGE = "PAGE"  # 페이지 (문서의 한 면)
    SHEET = "SHEET"  # 시트 (스프레드시트의 한 탭)
    SECTION = "SECTION"  # 문서 섹션
    CHAT = "CHAT"  # 대화 세션(채팅 UI)
    AI_RESPONSE = "AI_RESPONSE"  # 어시스턴트 발화
    TOPIC = "TOPIC"  # 주제 / 토픽
    FEATURE = "FEATURE"  # 소프트웨어 기능
    TASK = "TASK"  # 할 일
    DECISION = "DECISION"  # 결정 사항
    ERROR = "ERROR"  # 오류 / 버그
    EVENT = "EVENT"  # 분석/시스템 이벤트(동적 타입 폴백)
    # v3.6.0 Knowledge Graph First — 모든 데이터 소스가 그래프로 수렴하기 위한
    # 1급 엔티티. 추가형(additive)·확장 가능(extensible): 새 도메인 엔티티는
    # 여기에 enum 멤버를 추가하고 _LEGACY_NODE_MAP 에 별칭만 등록하면 된다.
    SOURCE = "SOURCE"  # 수집 출처(파일/URL/브라우저 탭/git 등)의 출처 노드
    REPOSITORY = "REPOSITORY"  # git 저장소
    MEETING = "MEETING"  # 회의 / 미팅
    ORGANIZATION = "ORGANIZATION"  # 조직 / 회사 / 팀
    WORKFLOW = "WORKFLOW"  # 워크플로우 정의/실행
    AGENT = "AGENT"  # 에이전트(역할/실행 주체)

    @classmethod
    def from_legacy(cls, label: str) -> "NodeType":
        """legacy ``knowledge_graph.py`` 의 자유 문자열을 정식 enum 으로 정규화.

        매핑이 없는(동적 이벤트 등) 타입은 ``CONCEPT`` 로 폴백하지만, 호출부는
        원본 문자열을 ``legacy_type`` 칼럼에 별도 보존하므로 정보 손실은 없다.
        """
        m = (label or "").strip()
        # Canonical values round-trip exactly (v4 native writes use them);
        # without this, CODE_FILE/AI_RESPONSE etc. would degrade to CONCEPT.
        try:
            return cls(m.upper())
        except ValueError:
            pass
        return _LEGACY_NODE_MAP.get(m.lower(), cls.CONCEPT)


class EdgeType(str, Enum):
    """노드 사이의 ‘방향성 있고 타입이 명시된’ 관계.  PPT 슬라이드 21."""

    CONTAINS = "CONTAINS"  # FILE → CHUNK
    MENTIONS = "MENTIONS"  # MESSAGE → CONCEPT
    REFERENCES = "REFERENCES"  # FILE → FILE / URL
    REPLIES_TO = "REPLIES_TO"  # MESSAGE → MESSAGE
    AUTHORED_BY = "AUTHORED_BY"  # FILE → PERSON
    USES = "USES"  # PROJECT → TOOL / MODEL
    DERIVED_FROM = "DERIVED_FROM"  # CHUNK → CHUNK (요약 등)
    SIMILAR_TO = "SIMILAR_TO"  # ANY ↔ ANY (의미 유사도)
    DEPENDS_ON = "DEPENDS_ON"  # CODE_SYMBOL → CODE_SYMBOL
    TAGGED_AS = "TAGGED_AS"  # ANY → CONCEPT
    VERSION_OF = "VERSION_OF"  # FILE → FILE (히스토리)
    GRANTS_ACCESS = "GRANTS_ACCESS"  # PERSON → RESOURCE
    USED_IN = "USED_IN"  # CONCEPT → DOCUMENT (문서에 활용됨)
    INSPIRED_BY = "INSPIRED_BY"  # DOCUMENT → DOCUMENT (영감/참조 관계)
    CONTRADICTS = "CONTRADICTS"  # DOCUMENT ↔ DOCUMENT (상충 관계)
    EVOLVES_FROM = "EVOLVES_FROM"  # DOCUMENT → DOCUMENT (발전/개정 관계)
    # legacy superset — knowledge_graph.py 가 실제로 생성하던 엣지 타입들
    UPLOADED_BY = "UPLOADED_BY"  # PERSON → FILE (업로드함)
    WROTE = "WROTE"  # PERSON → CONVERSATION (작성함)
    HAS_EVENT = "HAS_EVENT"  # CONVERSATION → EVENT (has_event)
    TRIGGERED = "TRIGGERED"  # PERSON → EVENT (triggered)
    HAS_SLIDE = "HAS_SLIDE"  # SLIDE_DECK → SLIDE (has_slide)
    HAS_PAGE = "HAS_PAGE"  # DOCUMENT → PAGE (has_page)
    HAS_SHEET = "HAS_SHEET"  # SPREADSHEET → SHEET (has_sheet)
    HAS_CHUNK = "HAS_CHUNK"  # FILE → CHUNK (has_chunk)
    CONTAINS_IMAGE = "CONTAINS_IMAGE"  # FILE → IMAGE (contains_image)
    CONTAINS_SIGNAL = "CONTAINS_SIGNAL"  # NODE → CONCEPT (contains_signal)
    DISCUSSES = "DISCUSSES"  # SLIDE/PAGE → TOPIC (discusses)
    IMPLIES = "IMPLIES"  # NODE → NODE (implies)
    RELATED_TO = "RELATED_TO"  # ANY ↔ ANY (related_to)
    # v3.6.0 Knowledge Graph First — 출처/소유/구성/결정 관계를 1급 엣지로 승격.
    # 추가형: 새 관계는 enum 멤버 추가 + _LEGACY_EDGE_MAP 별칭 등록만으로 확장된다.
    INDEXED_FROM = "INDEXED_FROM"  # NODE → SOURCE (어떤 출처에서 색인됐는가)
    MODIFIED_BY = "MODIFIED_BY"  # NODE → PERSON (마지막 수정자)
    BELONGS_TO_PROJECT = "BELONGS_TO_PROJECT"  # NODE → PROJECT
    PART_OF = "PART_OF"  # NODE → NODE (구성요소 관계)
    DISCUSSED_IN = "DISCUSSED_IN"  # CONCEPT/DECISION → MEETING/CHAT
    DECIDED_BY = "DECIDED_BY"  # DECISION → PERSON
    GENERATED_BY = "GENERATED_BY"  # NODE → AGENT/MODEL/WORKFLOW
    USED_BY_AGENT = "USED_BY_AGENT"  # NODE → AGENT (에이전트가 사용함)

    @classmethod
    def from_legacy(cls, label: str) -> "EdgeType":
        """legacy 자유 문자열/한글 동사를 정식 enum 으로 정규화.

        매핑이 없는 동적 타입은 ``MENTIONS`` 로 폴백하지만, 호출부는 원본 문자열을
        ``edges_v2.legacy_type`` 에 보존하므로 정보 손실은 없다.
        """
        m = (label or "").strip()
        # Canonical values round-trip exactly (v4 native writes use them).
        try:
            return cls(m.upper())
        except ValueError:
            pass
        return _LEGACY_EDGE_MAP.get(m.lower(), cls.MENTIONS)


# legacy(자유 문자열 / 한글 동사) → enum 매핑 표.
# superset 정규화: 알려진 legacy 타입은 1:1 의미 보존 매핑, 미지/동적 타입만 폴백.
_LEGACY_NODE_MAP: Dict[str, NodeType] = {
    "conversation": NodeType.CONVERSATION,
    "chat": NodeType.CHAT,
    "message": NodeType.MESSAGE,
    "airesponse": NodeType.AI_RESPONSE,
    "file": NodeType.FILE,
    "codefile": NodeType.CODE_FILE,
    "spreadsheet": NodeType.SPREADSHEET,
    "slidedeck": NodeType.SLIDE_DECK,
    "image": NodeType.IMAGE,
    "imagetext": NodeType.IMAGE_TEXT,
    "computer": NodeType.COMPUTER,
    "drive": NodeType.DRIVE,
    "folder": NodeType.FOLDER,
    "page": NodeType.PAGE,
    "sheet": NodeType.SHEET,
    "slide": NodeType.SLIDE,
    "section": NodeType.SECTION,
    "chunk": NodeType.CHUNK,
    "code": NodeType.CODE_SYMBOL,
    "concept": NodeType.CONCEPT,
    "topic": NodeType.TOPIC,
    "feature": NodeType.FEATURE,
    "task": NodeType.TASK,
    "decision": NodeType.DECISION,
    "error": NodeType.ERROR,
    "event": NodeType.EVENT,
    "tag": NodeType.CONCEPT,
    "person": NodeType.PERSON,
    "user": NodeType.PERSON,
    "model": NodeType.MODEL,
    "tool": NodeType.TOOL,
    "mcp": NodeType.TOOL,
    "project": NodeType.PROJECT,
    "workspace": NodeType.PROJECT,
    "document": NodeType.DOCUMENT,
    "report": NodeType.DOCUMENT,
    "plan": NodeType.DOCUMENT,
    "proposal": NodeType.DOCUMENT,
    "보고서": NodeType.DOCUMENT,
    "계획서": NodeType.DOCUMENT,
    "기획서": NodeType.DOCUMENT,
    # v3.6.0 Knowledge Graph First 엔티티
    "source": NodeType.SOURCE,
    "ingestionsource": NodeType.SOURCE,
    "repository": NodeType.REPOSITORY,
    "repo": NodeType.REPOSITORY,
    "gitrepo": NodeType.REPOSITORY,
    "meeting": NodeType.MEETING,
    "organization": NodeType.ORGANIZATION,
    "org": NodeType.ORGANIZATION,
    "company": NodeType.ORGANIZATION,
    "team": NodeType.ORGANIZATION,
    "workflow": NodeType.WORKFLOW,
    "agent": NodeType.AGENT,
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
    "used_in": EdgeType.USED_IN,
    "inspired_by": EdgeType.INSPIRED_BY,
    "contradicts": EdgeType.CONTRADICTS,
    "evolves_from": EdgeType.EVOLVES_FROM,
    # legacy superset 별칭 (knowledge_graph.py 가 실제로 쓰던 엣지 타입)
    "uploaded_by": EdgeType.UPLOADED_BY,
    "wrote": EdgeType.WROTE,
    "has_event": EdgeType.HAS_EVENT,
    "triggered": EdgeType.TRIGGERED,
    "has_slide": EdgeType.HAS_SLIDE,
    "has_page": EdgeType.HAS_PAGE,
    "has_sheet": EdgeType.HAS_SHEET,
    "has_chunk": EdgeType.HAS_CHUNK,
    "contains_image": EdgeType.CONTAINS_IMAGE,
    "contains_signal": EdgeType.CONTAINS_SIGNAL,
    "discusses": EdgeType.DISCUSSES,
    "implies": EdgeType.IMPLIES,
    "related_to": EdgeType.RELATED_TO,
    "활용됨": EdgeType.USED_IN,
    "영감받음": EdgeType.INSPIRED_BY,
    "상충함": EdgeType.CONTRADICTS,
    "발전함": EdgeType.EVOLVES_FROM,
    # v3.6.0 Knowledge Graph First 관계
    "indexed_from": EdgeType.INDEXED_FROM,
    "modified_by": EdgeType.MODIFIED_BY,
    "belongs_to_project": EdgeType.BELONGS_TO_PROJECT,
    "belongs_to": EdgeType.BELONGS_TO_PROJECT,
    "part_of": EdgeType.PART_OF,
    "discussed_in": EdgeType.DISCUSSED_IN,
    "decided_by": EdgeType.DECIDED_BY,
    "generated_by": EdgeType.GENERATED_BY,
    "used_by_agent": EdgeType.USED_BY_AGENT,
    "색인됨": EdgeType.INDEXED_FROM,
    "수정함": EdgeType.MODIFIED_BY,
    "결정함": EdgeType.DECIDED_BY,
    "구성요소": EdgeType.PART_OF,
}

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
  summary          TEXT,
  attrs            TEXT NOT NULL DEFAULT '{}',
  embedding        BLOB,
  owner_id         TEXT,
  -- NULL workspace_id = legacy-global (pre-scoping rows, readable machine-wide).
  workspace_id     TEXT,
  -- 'legacy' marks rows that predate scoping — the 'private' default must not
  -- silently privatize previously machine-shared data (design-review ruling).
  visibility       TEXT NOT NULL DEFAULT 'private',
  -- Revision chain: a node replaced by a newer one points at its successor.
  superseded_by    TEXT,
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
  -- Edge identity (v4): the normalized type AND the raw legacy type.
  -- Migrated rows keep their legacy_type discriminator, so two distinct
  -- legacy strings between one pair (e.g. "mentions" / "관련됨") stay
  -- distinct even though both normalize to MENTIONS. Native canonical
  -- writes carry legacy_type='' so their identity is effectively
  -- (source, target, type) — two canonical types between the same pair
  -- (e.g. MENTIONS + CONTAINS) never collide. The pre-v4
  -- UNIQUE(source, target, legacy_type) would have silently merged them.
  UNIQUE(source, target, type, legacy_type),
  FOREIGN KEY(source) REFERENCES nodes_v2(id) ON DELETE CASCADE,
  FOREIGN KEY(target) REFERENCES nodes_v2(id) ON DELETE CASCADE
);

-- Temporal dimension (v4): every repeated observation of a relationship is
-- recorded — edges_v2's UNIQUE identity + weight=max would otherwise erase
-- when something was learned, how often, and whether it still holds.
CREATE TABLE IF NOT EXISTS edge_occurrences (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_id     TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  weight      REAL NOT NULL DEFAULT 1.0,
  source      TEXT,
  FOREIGN KEY(edge_id) REFERENCES edges_v2(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_edge_occurrences_edge ON edge_occurrences(edge_id);
CREATE INDEX IF NOT EXISTS idx_edge_occurrences_time ON edge_occurrences(observed_at);

CREATE INDEX IF NOT EXISTS idx_nodes_v2_type     ON nodes_v2(type);
CREATE INDEX IF NOT EXISTS idx_nodes_v2_legacy   ON nodes_v2(legacy_type);
CREATE INDEX IF NOT EXISTS idx_nodes_v2_owner    ON nodes_v2(owner_id);
CREATE INDEX IF NOT EXISTS idx_edges_v2_source   ON edges_v2(source);
CREATE INDEX IF NOT EXISTS idx_edges_v2_target   ON edges_v2(target);
CREATE INDEX IF NOT EXISTS idx_edges_v2_type     ON edges_v2(type);
CREATE INDEX IF NOT EXISTS idx_edges_v2_legacy   ON edges_v2(legacy_type);
"""


def _exec_script(conn: sqlite3.Connection, script: str) -> None:
    """Run a multi-statement SQL script on ``conn`` statement-by-statement.

    Unlike ``sqlite3.Connection.executescript``, this does NOT issue an implicit
    COMMIT before running, so the statements join the caller's open transaction.
    Safe for our schema/view DDL (no ``;`` inside string literals).
    """
    for stmt in script.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)


class KGStoreV2:
    """가벼운 SQLite 기반 v2 스토어 — **스키마/초기화 지원 전용**.

    ``init_schema`` 으로 ``nodes_v2``/``edges_v2`` 를 생성·heal 하고 ``stats`` 로
    집계를 노출한다. 데이터 read/write 는 ``knowledge_graph.KnowledgeGraphStore``
    프로젝션이 담당하므로 native upsert/get/search API 는 두지 않는다.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

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
        "edges_v2": {
            "id",
            "source",
            "target",
            "type",
            "legacy_type",
            "weight",
            "confidence",
            "evidence",
            "metadata",
            "created_by",
            "created_at",
        },
        "nodes_v2": {
            "id",
            "type",
            "legacy_type",
            "label",
            "summary",
            "attrs",
            "embedding",
            "owner_id",
            "workspace_id",
            "visibility",
            "superseded_by",
            "created_at",
            "updated_at",
            "style",
            "tone",
            "importance_score",
            "last_used",
        },
    }

    # Columns added after a table's first release that can be healed in place
    # with ALTER TABLE ADD COLUMN (nullable / defaulted only).
    _V2_ADDABLE_COLUMNS = {
        "nodes_v2": {"workspace_id": "TEXT", "superseded_by": "TEXT"},
        "edges_v2": {},
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
            cols = {
                r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing = self._V2_EXPECTED_COLUMNS[table] - cols
            if not missing:
                continue
            # Additive columns heal in place without touching data.
            addable = self._V2_ADDABLE_COLUMNS.get(table, {})
            for col in sorted(missing & set(addable)):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {addable[col]}")
            missing -= set(addable)
            if not missing:
                continue
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count == 0:
                conn.execute(f"DROP TABLE {table}")
            else:
                logging.warning(
                    "kg_schema: %s is missing columns %s but holds %d rows — "
                    "leaving it untouched (manual migration required).",
                    table,
                    sorted(missing),
                    count,
                )

    def init_schema(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """Create the v2 schema and record metadata.

        Pass ``conn`` to run inside the caller's open transaction (used by the
        atomic knowledge_graph migration); otherwise a private connection is
        opened and committed. Uses ``_exec_script`` rather than
        ``executescript`` so it never force-commits the caller's transaction.
        """
        if conn is not None:
            self._init_schema_on(conn)
            return
        with self._conn() as own:
            self._init_schema_on(own)

    def _rebuild_edges_identity(self, conn: sqlite3.Connection) -> None:
        """Migrate edges_v2 from the pre-v4 UNIQUE(source, target, legacy_type)
        identity to UNIQUE(source, target, type, legacy_type).

        SQLite cannot alter constraints, so this is a create→copy→swap inside
        the caller's transaction. Data-preserving: every existing row keeps its
        legacy_type discriminator. Re-entrant: keyed on the actual constraint
        in sqlite_master, not a one-time stamp.
        """
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='edges_v2'"
        ).fetchone()
        if not row or "UNIQUE(source, target, type, legacy_type)" in (row["sql"] or ""):
            return
        conn.execute("ALTER TABLE edges_v2 RENAME TO edges_v2_old")
        # Recreate from the canonical DDL (edges_v2 portion of SCHEMA_SQL).
        start = SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS edges_v2")
        end = SCHEMA_SQL.index(");", start) + 2
        conn.execute(SCHEMA_SQL[start:end].rstrip(";"))
        conn.execute(
            """
            INSERT INTO edges_v2 (id, source, target, type, legacy_type, weight,
                                  confidence, evidence, metadata, created_by, created_at)
            SELECT id, source, target, type, legacy_type, weight,
                   confidence, evidence, metadata, created_by, created_at
            FROM edges_v2_old
            """
        )
        conn.execute("DROP TABLE edges_v2_old")
        logging.info(
            "kg_schema: rebuilt edges_v2 with (source, target, type, legacy_type) identity"
        )

    def _init_schema_on(self, conn: sqlite3.Connection) -> None:
        self._drop_stale_empty_v2_tables(conn)
        self._rebuild_edges_identity(conn)
        _exec_script(conn, SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO kg_meta(key, value) VALUES (?, ?)",
            ("schema_version", str(KG_SCHEMA_V2_VERSION)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO kg_meta(key, value) VALUES (?, ?)",
            ("embed_dim", str(EMBED_DIM)),
        )

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
            "embed_dim": EMBED_DIM,
            "nodes": n_nodes,
            "edges": n_edges,
            "by_node_type": per_type,
            "by_edge_type": per_edge,
        }


# NOTE: legacy → v2 reprojection lives in ``knowledge_graph.py``
# (``KnowledgeGraphStore._backfill_v2_if_needed`` / ``_v2_project_node``/_edge),
# which is the single live, version-gated migration path. The old standalone
# ``migrate_legacy_to_v2()`` helper + CLI ``migrate`` subcommand were removed as
# dead code (no callers); the normalized projection now writes the first-class
# ``legacy_type``/``summary``/``metadata`` columns directly.


# ── CLI ────────────────────────────────────────────────────────────────────
def _cli() -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="kg_schema", description="Lattice AI KG v2 utilities"
    )
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
